"""Installation changes cannot hide behind an identical account/configuration."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from ioalab_sdk.runtime import installation
from ioalab_sdk.runtime.__main__ import main,start
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json
from ioalab_sdk.runtime.ipc import IpcServer,LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from test_access_products import bootstrap
from test_resident_runtime import configuration


def managed(root,release='a'*64):
    root.mkdir(parents=True,exist_ok=True)
    value={'format':installation.FORMAT,'releaseId':release,
           'versions':{'ioalab-sdk':installation.installed_version('ioalab-sdk')}}
    (root/'ioalab-release.json').write_text(json.dumps(value),encoding='utf-8')
    (root/'.ready').write_text(release,encoding='utf-8')
    return value


@pytest.mark.parametrize('corruption',['none','missing_ready','missing_marker','different_ready','different_version','invalid_marker'])
def test_snapshot_checks_actual_managed_versions_and_does_not_repair(tmp_path,monkeypatch,corruption):
    value=managed(tmp_path)
    if corruption=='missing_ready':(tmp_path/'.ready').unlink()
    if corruption=='missing_marker':(tmp_path/'ioalab-release.json').unlink()
    if corruption=='different_ready':(tmp_path/'.ready').write_text('b'*64)
    if corruption=='different_version':
        value['versions']['ioalab-sdk']='999999';(tmp_path/'ioalab-release.json').write_text(json.dumps(value))
    if corruption=='invalid_marker':(tmp_path/'ioalab-release.json').write_text('[]')
    before={path.name:path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(installation,'sys',SimpleNamespace(prefix=str(tmp_path)))
    if corruption=='none':
        snapshot=installation.current();assert snapshot['releaseId']=='a'*64 and snapshot['versions']==value['versions']
    else:
        with pytest.raises(RuntimeFault,match='incomplete or changed'):installation.current()
    assert {path.name:path.read_bytes() for path in tmp_path.iterdir()}==before


@pytest.mark.parametrize('change',['installation','missing_information','assembly'])
def test_same_config_cannot_reuse_an_old_installation_or_dispatch_business(platform,tmp_path,monkeypatch,capsys,change):
    config,_=configuration(platform,tmp_path,service=False);effects=[]
    runtime=ResidentRuntime(RuntimeConfig.load(config),operations={'test.effect':lambda *_:(effects.append(True) or {'status':'completed','data':{}})}).start()
    original=LocalClient(runtime.config.home).call('runtime.status')['data']
    args=tmp_path/'action.json';private_json(args,{});private_json(tmp_path/'stop.json',{})
    try:
        with monkeypatch.context() as patch:
            if change=='installation':
                prefix=tmp_path/'new-environment';managed(prefix)
                patch.setattr(installation,'sys',SimpleNamespace(prefix=str(prefix)))
            elif change=='missing_information':
                original_status=runtime.status
                patch.setattr(runtime,'status',lambda:{key:value for key,value in original_status().items() if key!='installation'})
            else:
                runtime.installation={**runtime.installation,'assembly':'ioalab-jiuwen',
                                      'versions':{**runtime.installation['versions'],'ioalab-jiuwen':'older-build'}}
            with pytest.raises(RuntimeFault) as error:start(config)
            assert error.value.code=='installation_changed'
            assert main(['call','test.effect','--config',str(config),'--args-file',str(args)])==1
            assert json.loads(capsys.readouterr().out)['error']['code']=='installation_changed'
            assert not effects and LocalClient(runtime.config.home).call('runtime.status')['data']['bootId']==original['bootId']
            assert main(['call','runtime.status','--config',str(config)])==0
            assert json.loads(capsys.readouterr().out)['status']=='completed'
            assert main(['call','runtime.stop','--config',str(config),'--args-file',str(tmp_path/'stop.json')])==0
            assert json.loads(capsys.readouterr().out)['data']['bootId']==original['bootId']
    finally:runtime.close()


def test_cli_preflight_cannot_switch_to_another_boot_before_a_business_request(tmp_path,monkeypatch,capsys):
    import ioalab_sdk.runtime.__main__ as entry
    effects=[];servers=[];home=tmp_path/'state';config=tmp_path/'settings.json'
    private_json(config,{'platform_url':'http://127.0.0.1:8750','username':'owner','password':'test-only',
        'data_dir':str(home),'identities':[{'key':'personal','role':'assistant','name':'Owner'}]})
    configured=RuntimeConfig.load(config)
    def dispatch(operation,operation_id,args):
        if operation=='runtime.status':data={'bootId':servers[0].boot_id,'installation':installation.current(),
            'account':{'username':configured.username,'platform':configured.platform},
            'configurationDigest':configured.public_summary()['configurationDigest']}
        else:effects.append((operation,args));data={}
        return {'operationId':operation_id,'status':'completed','data':data,'error':None}
    first=IpcServer(home,'first',dispatch);servers.append(first);first.start()
    second=IpcServer(home,'second',dispatch)
    original=entry.require_matching
    def replace_instance(actual,**kwargs):
        original(actual,**kwargs);second.start()
    monkeypatch.setattr(entry,'require_matching',replace_instance)
    private_json(tmp_path/'action.json',{})
    try:
        assert main(['call','test.effect','--config',str(config),'--args-file',str(tmp_path/'action.json')])==1
        result=json.loads(capsys.readouterr().out)
        assert result['error']['code']=='instance_changed' and not effects
    finally:first.close();second.close()


@pytest.mark.parametrize('corruption',['marker','ready','python'])
def test_completed_cache_is_never_repaired_under_an_existing_runtime(tmp_path,monkeypatch,corruption):
    import os
    module=bootstrap();assets=tmp_path/'assets';wheels=assets/'wheels';wheels.mkdir(parents=True)
    (assets/'requirements.lock').write_bytes(b'');(wheels/'sdk.whl').write_bytes(b'input')
    manifest={'versions':{},'constraintsSha256':hashlib.sha256(b'').hexdigest(),
              'wheels':[{'file':'sdk.whl','sha256':hashlib.sha256(b'input').hexdigest()}]}
    raw=json.dumps(manifest).encode();(assets/'runtime.json').write_bytes(raw);fingerprint=hashlib.sha256(raw).hexdigest()
    cache=tmp_path/'cache';environment=cache/fingerprint[:24];environment.mkdir(parents=True)
    (environment/'.ready').write_text(fingerprint if corruption!='ready' else 'wrong')
    (environment/'ioalab-release.json').write_text(json.dumps({'format':installation.FORMAT,'releaseId':fingerprint,'versions':{}}) if corruption!='marker' else '{}')
    if corruption!='python':
        python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python');python.parent.mkdir();python.write_bytes(b'not used')
    monkeypatch.setattr(module.venv.EnvBuilder,'create',lambda *_:pytest.fail('overwrote a completed environment'))
    with pytest.raises(RuntimeError,match='do not repair it in place'):module.ensure_runtime(assets,cache)
