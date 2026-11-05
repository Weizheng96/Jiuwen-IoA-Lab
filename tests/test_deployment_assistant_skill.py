"""Deployed product binding reuses one SDK; tampering never installs a fallback."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

import deploy.assistant_skill as skill_module
from deploy.assistant_skill import ensure_assistant_skill, package_files, upload, verify_files
from deploy.release import digest

ROOT=Path(__file__).resolve().parents[1]


def installer():
    spec=importlib.util.spec_from_file_location('deployed_installer',ROOT/'src/access/skills/_shared/installer.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def installed(tmp_path):
    assets=tmp_path/'skill/assets';(assets/'wheels').mkdir(parents=True)
    wheel=b'controlled test wheel';checksum=hashlib.sha256(wheel).hexdigest()
    (assets/'wheels/local.whl').write_bytes(wheel)
    lock=b'ioalab-sdk==1\n';(assets/'requirements.lock').write_bytes(lock)
    manifest={'versions':{'ioalab-sdk':'1'},'constraintsSha256':hashlib.sha256(lock).hexdigest(),
              'wheels':[{'file':'local.whl','sha256':checksum}]}
    raw=json.dumps(manifest).encode();(assets/'runtime.json').write_bytes(raw)
    prefix=tmp_path/'participant';python=prefix/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    python.parent.mkdir(parents=True);python.write_text('fixture; never run')
    identity={'format':'ioalab.deployment-environment/1','unit':'participant',
              'plan':{'wheels':[{'path':'wheels/local.whl','sha256':checksum}]}}
    identifier=digest(identity)
    (prefix/'deployment.json').write_text(json.dumps({'identity':identity,'environmentId':identifier,'versions':manifest['versions']}))
    (prefix/'.ready').write_text(identifier)
    config=tmp_path/'original-settings.json';config.write_text('PRIVATE CONTENT MUST NEVER BE READ')
    binding=tmp_path/'skill/scripts/deployment.local.json';binding.parent.mkdir()
    value={'format':'ioalab.deployed-skill/1','python':str(python),'configuration':str(config),
           'environmentId':identifier,'manifestSha256':hashlib.sha256(raw).hexdigest()}
    binding.write_text(json.dumps(value));return assets,binding,prefix,config,value


def test_deployed_binding_uses_original_environment_without_new_cache_or_config_read(tmp_path,monkeypatch):
    module=installer();assets,binding,prefix,config,value=installed(tmp_path);calls=[]
    read=Path.read_text
    def guarded(path,*args,**kwargs):
        assert path!=config,'deployer read private account contents'
        return read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded)
    monkeypatch.setattr(module.subprocess,'run',lambda command,**kwargs:(calls.append((command,kwargs)) or SimpleNamespace(returncode=0)))
    result=module.deployed(assets,binding)
    assert result['data']['python']==value['python'] and result['data']['configuration']==str(config)
    assert result['data']['releaseId']==value['environmentId'] and len(calls)==1
    assert 'PRIVATE CONTENT' not in str(calls) and not (tmp_path/'cache').exists()
    assert 'current("ioalab-official")' in calls[0][0][-1]


@pytest.mark.parametrize('part',['wheel','lock','manifest','ready','identity','wrong-unit','config-missing','invalid-binding'])
def test_deployed_binding_tampering_fails_before_sdk_or_install(tmp_path,monkeypatch,part):
    module=installer();assets,binding,prefix,config,value=installed(tmp_path)
    if part=='wheel':(assets/'wheels/local.whl').write_bytes(b'changed')
    elif part=='lock':(assets/'requirements.lock').write_bytes(b'changed')
    elif part=='manifest':(assets/'runtime.json').write_text('{}')
    elif part=='ready':(prefix/'.ready').write_text('changed')
    elif part=='identity':value['environmentId']='0'*64;binding.write_text(json.dumps(value))
    elif part=='wrong-unit':
        metadata=json.loads((prefix/'deployment.json').read_text());metadata['identity']['unit']='swarm'
        (prefix/'deployment.json').write_text(json.dumps(metadata))
    elif part=='config-missing':config.unlink()
    else:binding.write_text('[]')
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:pytest.fail('invalid binding executed SDK'))
    with pytest.raises(RuntimeError):module.deployed(assets,binding)


@pytest.mark.parametrize('name', ['../outside','ioalab-delegate/../outside','other/SKILL.md','ioalab-delegate/scripts/deployment.local.json'])
def test_skill_archive_rejects_escape_or_private_instance_locator(tmp_path,name):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as output:output.writestr(name,b'untrusted')
    with pytest.raises(ValueError):package_files(archive)


def test_existing_import_assets_are_never_overwritten(tmp_path):
    folder=tmp_path/'skill';folder.mkdir();(folder/'SKILL.md').write_bytes(b'changed')
    with pytest.raises(RuntimeError,match='differs'):verify_files(folder,{'SKILL.md':b'original'})
    assert (folder/'SKILL.md').read_bytes()==b'changed'


@pytest.mark.parametrize('url',['https://127.0.0.1:1234','http://example.invalid:1234','http://127.0.0.1:1234/?token=private','http://user:secret@127.0.0.1:1234'])
def test_original_import_only_accepts_owned_plain_loopback_url(tmp_path,url):
    with pytest.raises(ValueError):upload(url,tmp_path/'never-read.zip')


@pytest.mark.parametrize('changed',[None,'boot','account','installation','role'])
def test_original_import_binds_assets_and_the_actual_resident_only(tmp_path,monkeypatch,changed):
    release=tmp_path/'release';release.mkdir();archive=release/'assistant.zip'
    contents={'SKILL.md':b'name: ioalab-delegate','scripts/ioalab.py':b'test-entry',
              'scripts/installer.py':b'test-installer','assets/runtime.json':b'{}','assets/skill.json':b'{"role":"assistant"}'}
    with zipfile.ZipFile(archive,'w') as output:
        for name,data in contents.items():output.writestr('ioalab-delegate/'+name,data)
    workspace=tmp_path/'agents/swarm-admin';workspace.mkdir(parents=True)
    config=workspace.parent/'admin-assistant/config.json';config.parent.mkdir();config.write_text('never read private contents')
    prefix=tmp_path/'participant';python=prefix/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    python.parent.mkdir(parents=True);python.write_text('test executable')
    plan={'released':'same'}
    (prefix/'deployment.json').write_text(json.dumps({'environmentId':'original-installation','identity':{'unit':'participant','plan':plan}}))
    document={'assets':{'assistantSkill':{'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}},'units':{'participant':plan}}
    monkeypatch.setattr(skill_module,'load',lambda *args:document)
    monkeypatch.setattr(skill_module,'checked',lambda *args:archive)
    uploads=[]
    def native_upload(url,path):
        uploads.append((url,path));folder=workspace/'agent/workspace/skills/ioalab-delegate'
        for name,data in contents.items():
            target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        return {'success':True,'skill':{'name':'ioalab-delegate','workspace_path':str(folder)}}
    monkeypatch.setattr(skill_module,'upload',native_upload)
    live={'bootId':'original-boot','account':{'username':'ISC_001'},'platformConnected':True,'host':{'available':True},
          'installation':{'releaseId':'original-installation'},'identities':[{'profile':'admin','role':'assistant'}]}
    if changed=='boot':live['bootId']='other'
    elif changed=='account':live['account']['username']='other'
    elif changed=='installation':live['installation']['releaseId']='other'
    elif changed=='role':live['identities'][0]['role']='service'
    calls=[]
    monkeypatch.setattr(skill_module,'sdk_status',lambda command:(calls.append(command) or live))
    arguments=(release,python,workspace,config,'http://127.0.0.1:18902','original-boot')
    if changed:
        with pytest.raises(RuntimeError,match='original ready assistant'):ensure_assistant_skill(*arguments)
    else:
        first=ensure_assistant_skill(*arguments);second=ensure_assistant_skill(*arguments)
        assert first==second and first['sameResidentVerified'] and len(uploads)==1
        assert calls[0][-1]=='runtime.status' and '--config' not in calls[0]
        binding=json.loads((workspace/'agent/workspace/skills/ioalab-delegate/scripts/deployment.local.json').read_text())
        assert binding['configuration']==str(config) and 'private contents' not in str(binding)
