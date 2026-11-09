"""Product skill installer: a deployed binding reuses one SDK; tampering never installs a fallback."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

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
