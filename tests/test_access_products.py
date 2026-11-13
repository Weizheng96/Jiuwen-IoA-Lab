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
    spec=importlib.util.spec_from_file_location('ioalab_client.test_commands',ROOT/'src/client/ioalab_client/commands.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_bound_skill_role_cannot_be_overridden_by_call_arguments(monkeypatch):
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('Role override reached installation or RPC'))
    with pytest.raises(SystemExit) as error:
        module.main(['--role','assistant','--role','service','runtime.status'])
    assert error.value.code==2


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
    assert module.main(['--role',role,operation,'--config',str(tmp_path/'missing-config.json'),'--args-file',str(arguments)])==1
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
    assert module.main(['--role',role,operation,'--config',str(config),'--args-file',str(arguments)])==(1 if status=='rejected' else 0)
    assert json.loads(capsys.readouterr().out)==expected
    assert calls==[(python,['-m','ioalab_sdk.access','call',operation,'--config',str(config.resolve()),'--args-file',str(arguments.resolve())])]
    assert json.loads(arguments.read_text())=={'request':'unchanged'} and not config.exists()


def test_both_real_product_bundles_share_one_installed_client(tmp_path):
    from ioalab_client import export_skills
    paths=export_skills(tmp_path/'bundles',source=ROOT/'src/client/skills',python=sys.executable)
    for path,root,role in zip(paths,['ioalab-delegate','ioalab-provider'],['assistant','service']):
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            assert root+'/SKILL.md' in names and root+'/references/setup.md' in names
            assert not any('/assets/' in name or name.endswith('/installer.py') or name.endswith('.whl') for name in names)
            script=archive.read(root+'/scripts/ioalab.py').decode()
            assert repr(sys.executable) in script and role in script
            assert archive.read(root+'/references/shared-runtime.md')==(ROOT/'src/client/skills/_shared/references/shared-runtime.md').read_bytes()
    assert export_skills(tmp_path/'bundles',source=ROOT/'src/client/skills',python=sys.executable)==paths
    paths[0].write_bytes(b'previous-user-export')
    with pytest.raises(RuntimeError,match='preserve'):
        export_skills(tmp_path/'bundles',source=ROOT/'src/client/skills',python=sys.executable)
    assert paths[0].read_bytes()==b'previous-user-export'


def test_new_downloads_only_serve_fixed_product_names(platform,client,tmp_path,monkeypatch):
    monkeypatch.setattr(platform.s,'bundles_dir',tmp_path)
    for name in ('ioalab-assistant-skill.zip','ioalab-provider-skill.zip'):
        assert client.get('/downloads/'+name).status_code==503
        data=b'public product bundle '+name.encode();(tmp_path/name).write_bytes(data)
        assert client.get('/downloads/'+name).content==data
    (tmp_path/'private.json').write_text('private')
    assert client.get('/downloads/private.json').status_code==404
    import hashlib
    manifest=client.get('/api/access/bundles').json()
    assert len(manifest['bundles'])==2
    for item in manifest['bundles']:
        actual=client.get('/downloads/'+item['name']).content
        assert item['available'] and item['size']==len(actual)
        assert item['sha256']==hashlib.sha256(actual).hexdigest()
    assert client.get('/readme_agent.md',follow_redirects=False).status_code==404


@pytest.mark.parametrize('parent_encoding',['ascii','gbk'])
def test_product_entry_returns_utf8_under_non_utf8_parent(tmp_path,parent_encoding,monkeypatch,capsys):
    from types import SimpleNamespace
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'notice':'中文账号与参数路径','python':sys.executable}}))
    assert module.main(['--role','assistant','runtime.install.status'])==0
    result=capsys.readouterr().out
    assert result.isascii() and json.loads(result)['data']['notice']=='中文账号与参数路径'


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


def test_connect_reports_missing_host_configuration_before_launch(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    module=bootstrap();root=tmp_path/'skill';(root/'assets').mkdir(parents=True)
    (root/'assets/skill.json').write_text(json.dumps({'role':'assistant'}));module.__file__=str(root/'scripts/ioalab.py')
    config=tmp_path/'settings.json';config.write_text('{}')
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda *a,**kw:
        {'status':'completed','data':{'python':sys.executable}}))
    def invoke(python,command):
        if command[2]=='validate':data={'identities':[{'role':'assistant'}]}
        elif command[2]=='configuration':data={'hostFields':[],'configurationDigest':'sha256:test'}
        else:pytest.fail('Incomplete onboarding launched a resident instead of requesting host configuration')
        return SimpleNamespace(stdout=json.dumps({'status':'completed','data':data,'error':None}),stderr='',returncode=0)
    monkeypatch.setattr(module,'invoke',invoke)
    assert module.main(['--role','assistant','runtime.connect','--config',str(config)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='waiting' and result['stage']=='configuration'
    assert result['nextAction']=='runtime.configure'
    assert set(result['data']['requiredFields'])=={'host.command','host.data_dir'}
    assert result['data']['configurationDigest']=='sha256:test'
