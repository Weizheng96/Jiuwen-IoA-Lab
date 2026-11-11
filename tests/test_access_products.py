"""Packaged product skills, immutable installation inputs and real configuration reuse."""
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.access import main
from ioalab_sdk.runtime import start
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_runtime import configuration

ROOT=Path(__file__).resolve().parents[1]


def bootstrap():
    spec=importlib.util.spec_from_file_location('product_bootstrap',ROOT/'src/access/skills/_shared/ioalab.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_validate_is_read_only_and_never_returns_credentials(tmp_path,capsys):
    config=tmp_path/'settings.json';home=tmp_path/'not-created'
    private_json(config,{'platform_url':'http://127.0.0.1:8000','username':'public-account','password':'private-test-value',
                        'data_dir':str(home),'identities':[{'key':'personal','role':'assistant','name':'My assistant'}]})
    assert main(['validate','--config',str(config)])==0
    text=capsys.readouterr().out;value=json.loads(text)
    assert value['status']=='completed' and not home.exists()
    assert 'password' not in text and 'private-test-value' not in text
    original=RuntimeConfig.load(config);summary=original.public_summary()
    assert replace(original,password='another-private-value').public_summary()==summary
    assert replace(original,max_model_runs=original.max_model_runs+1).public_summary()['configurationDigest']!=summary['configurationDigest']
    assert replace(original,provider={'services':{'service':{'native_tools':{'write_file':'allow'}}}}).public_summary()['configurationDigest']!=summary['configurationDigest']


def test_existing_instance_cannot_hide_new_role_configuration(platform,tmp_path,monkeypatch):
    config,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(config)).start()
    try:
        before=LocalClient(runtime.config.home).call('runtime.status')['data']
        assert start(config)['data']['bootId']==before['bootId']
        edited=read_json(config);edited['identities'].append({'key':'service','role':'service','name':'New role'})
        private_json(config,edited)
        original_spawn=subprocess.Popen
        def spawn(command,*args,**kwargs):
            if 'serve' in command:pytest.fail('spawned a competing runtime')
            return original_spawn(command,*args,**kwargs)
        monkeypatch.setattr('ioalab_sdk.runtime.lifecycle.subprocess.Popen',spawn)
        with pytest.raises(RuntimeFault) as exc:start(config)
        assert exc.value.code=='configuration_changed'
        after=LocalClient(runtime.config.home).call('runtime.status')['data']
        assert after['bootId']==before['bootId'] and after['configurationDigest']==before['configurationDigest']
        assert len(after['identities'])==1
    finally:runtime.close()


@pytest.mark.parametrize('what',['wheel','constraint','path'])
def test_tampered_installer_inputs_never_create_an_environment(tmp_path,what):
    module=bootstrap();assets=tmp_path/'assets';(assets/'wheels').mkdir(parents=True)
    lock=b'package==1\n';(assets/'requirements.lock').write_bytes(lock)
    data=b'original';(assets/'wheels/local.whl').write_bytes(b'tampered' if what=='wheel' else data)
    manifest={'format':1,'skillVersion':'0.0.1','versions':{},'constraintsSha256':hashlib.sha256(b'wrong' if what=='constraint' else lock).hexdigest(),
              'wheels':[{'file':'../outside.whl' if what=='path' else 'local.whl','sha256':hashlib.sha256(data).hexdigest()}]}
    (assets/'runtime.json').write_text(json.dumps(manifest));cache=tmp_path/'cache'
    with pytest.raises(RuntimeError,match='checksum|path'):module.installation().Release(assets,cache)
    assert not cache.exists()


@pytest.mark.parametrize('role,operation,example',[
    ('assistant','provider.publish','assistant.delegation.prepare'),
    ('assistant','prepare','assistant.delegation.prepare'),
    ('assistant','assistant','assistant.delegation.prepare'),
    ('service','assistant.delegation.prepare','provider.tasks.get'),
    ('service','prepare','provider.tasks.get'),
    ('service','provider','provider.tasks.get'),
])
def test_unrelated_role_cannot_trigger_bootstrap_or_rpc(tmp_path,monkeypatch,capsys,role,operation,example):
    module=bootstrap();root=tmp_path/'skill';(root/'assets').mkdir(parents=True)
    (root/'assets/skill.json').write_text(json.dumps({'role':role}));module.__file__=str(root/'scripts/ioalab.py')
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('unrelated operation installed packages'))
    monkeypatch.setattr(module,'invoke',lambda *args:pytest.fail('invalid operation invoked SDK'))
    arguments=tmp_path/'arguments.json';arguments.write_text('{"request":"unchanged"}')
    assert module.main([operation,'--config',str(tmp_path/'missing-config.json'),'--args-file',str(arguments)])==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='rejected' and result['error']['code']=='invalid_input'
    assert result['error']['recovery']=='correct_input'
    assert example in result['error']['message'] and 'complete operation name' in result['error']['message']
    assert 'do not change identity roles' in result['error']['message']
    assert json.loads(arguments.read_text())=={'request':'unchanged'}


@pytest.mark.parametrize('role,operation,status,error',[
    ('assistant','assistant.delegation.prepare','waiting',None),
    ('service','provider.tasks.get','completed',None),
    ('assistant','assistant.unsupported','rejected',{'code':'not_ready','message':'Unknown capability','recovery':'inspect_capabilities'}),
])
def test_complete_role_operation_is_forwarded_without_alias_or_error_reclassification(tmp_path,monkeypatch,capsys,role,operation,status,error):
    from types import SimpleNamespace
    module=bootstrap();root=tmp_path/'skill';(root/'assets').mkdir(parents=True)
    (root/'assets/skill.json').write_text(json.dumps({'role':role}));module.__file__=str(root/'scripts/ioalab.py')
    python=tmp_path/'installed-python.exe';prepared={'status':'completed','data':{'python':str(python)}}
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda *args,**kwargs:prepared))
    config=tmp_path/'external-config.json';arguments=tmp_path/'arguments.json'
    arguments.write_text('{"request":"unchanged"}')
    expected={'status':status,'data':{'reference':'original'},'error':error};calls=[]
    def invoke(actual_python,command):
        calls.append((actual_python,command))
        return SimpleNamespace(stdout=json.dumps(expected),stderr='',returncode=1 if status=='rejected' else 0)
    monkeypatch.setattr(module,'invoke',invoke)
    assert module.main([operation,'--config',str(config),'--args-file',str(arguments)])==(1 if status=='rejected' else 0)
    assert json.loads(capsys.readouterr().out)==expected
    assert calls==[(python,['-m','ioalab_sdk.access','call',operation,'--config',str(config.resolve()),'--args-file',str(arguments.resolve())])]
    assert json.loads(arguments.read_text())=={'request':'unchanged'} and not config.exists()


def test_both_real_product_bundles_have_one_manifest_and_generated_entry(tmp_path):
    output=tmp_path/'bundles'
    result=subprocess.run([sys.executable,str(ROOT/'tools/build_access_skills.py'),'--out',str(output)],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    manifests=[]
    for folder,root,role in [('assistant','ioalab-delegate','assistant'),('provider','ioalab-provider','service')]:
        with zipfile.ZipFile(output/('ioalab-'+folder+'-skill.zip')) as archive:
            assert not any('settings.local.json' in name or name.endswith('.pyc') for name in archive.namelist())
            manifests.append(archive.read(root+'/assets/runtime.json'))
            assert json.loads(archive.read(root+'/assets/skill.json'))['role']==role
            for entry in ('ioalab.py','installer.py'):
                assert archive.read(root+'/scripts/'+entry)==(ROOT/'src/access/skills/_shared'/entry).read_bytes()
            assert root+'/references/setup.md' in archive.namelist()
            assert archive.read(root+'/references/shared-runtime.md')==(ROOT/'src/access/skills/_shared/references/shared-runtime.md').read_bytes()
            assert archive.read(root+'/references/location.md')==(ROOT/'src/access/skills/_shared/references/location.md').read_bytes().replace(b'__IOALAB_SKILL_NAME__',root.encode('ascii'))
            if role=='service':assert root+'/references/contracts.md' in archive.namelist()
            assert any('/assets/wheels/ioalab_official-' in name for name in archive.namelist())
            manifest=json.loads(manifests[-1])
            assert manifest['format']==1
            assert manifest['skillVersion']==(ROOT/'src/access/skills/VERSION').read_text().strip().removeprefix('v')
            assert set(manifest['versions'])=={'a2c','ioalab-sdk','ioalab-jiuwen','ioalab-official','jiuwenswarm-process-sdk'}
            for wheel in manifest['wheels']:
                assert hashlib.sha256(archive.read(root+'/assets/wheels/'+wheel['file'])).hexdigest()==wheel['sha256']
    assert manifests[0]==manifests[1]
    repeated=tmp_path/'repeated'
    result=subprocess.run([sys.executable,str(ROOT/'tools/build_access_skills.py'),'--out',str(repeated)],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    with zipfile.ZipFile(repeated/'ioalab-assistant-skill.zip') as archive:
        assert archive.read('ioalab-delegate/assets/runtime.json')==manifests[0]


def test_new_downloads_only_serve_fixed_product_names(platform,client,tmp_path,monkeypatch):
    monkeypatch.setattr(platform.s,'bundles_dir',tmp_path)
    for name in ('ioalab-assistant-skill.zip','ioalab-provider-skill.zip'):
        assert client.get('/downloads/'+name).status_code==503
        data=b'public product bundle '+name.encode();(tmp_path/name).write_bytes(data)
        assert client.get('/downloads/'+name).content==data
    (tmp_path/'private.json').write_text('private')
    assert client.get('/downloads/private.json').status_code==404


@pytest.mark.parametrize('parent_encoding',['ascii','gbk'])
def test_product_entry_returns_utf8_under_non_utf8_parent(tmp_path,parent_encoding):
    import os
    import shutil
    skill=tmp_path/'skill';scripts=skill/'scripts';scripts.mkdir(parents=True)
    (skill/'assets').mkdir();(skill/'assets/skill.json').write_text('{"role":"assistant"}',encoding='utf-8')
    shutil.copy2(ROOT/'src/access/skills/_shared/ioalab.py',scripts/'ioalab.py')
    (scripts/'installer.py').write_text(
        'def prepare(*args,**kwargs):\n    return {"status":"waiting","data":{"notice":"中文账号与参数路径"}}\n',encoding='utf-8')
    result=subprocess.run([sys.executable,str(scripts/'ioalab.py'),'runtime.install.status'],capture_output=True,
        env={**os.environ,'PYTHONIOENCODING':parent_encoding,'PYTHONUTF8':'0'})
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')
    assert json.loads(result.stdout.decode('utf-8'))=={'status':'waiting','data':{'notice':'中文账号与参数路径'}}


def test_old_installation_status_preserves_observation_and_allows_safe_stop(platform,tmp_path,monkeypatch,capsys):
    config,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(config)).start()
    try:
        command=['call','runtime.status','--config',str(config)]
        assert main(command)==0
        before=json.loads(capsys.readouterr().out)
        # Same package versions but a different release must not look like this caller's ready runtime.
        monkeypatch.setattr(runtime,'installation',{**runtime.installation,'releaseId':'f'*64})
        assert main(command)==1
        mismatch=json.loads(capsys.readouterr().out)
        assert mismatch['status']=='rejected' and mismatch['error']['code']=='installation_changed'
        assert mismatch['data']['bootId']==before['data']['bootId']
        assert mismatch['data']['runtimeId']==before['data']['runtimeId']
        assert mismatch['data']['installation']['releaseId']=='f'*64
        args=tmp_path/'stop.json';private_json(args,{})
        assert main(['call','runtime.stop','--config',str(config),'--args-file',str(args)])==0
        stopped=json.loads(capsys.readouterr().out)
        assert stopped['status']=='completed' and stopped['data']['preserved']
        assert stopped['data']['bootId']==before['data']['bootId']
    finally:runtime.close()
