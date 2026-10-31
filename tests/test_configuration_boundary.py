"""One settings owner; real CLI/IPC, file identity and safe-control boundaries."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from conftest import wait_for
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.__main__ import main
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.resident import ResidentRuntime
from test_access_products import bootstrap
from test_provider_authoring import terms
from test_resident_runtime import configuration


def product(tmp_path,monkeypatch,role):
    module=bootstrap();root=tmp_path/'skill';(root/'assets').mkdir(parents=True)
    (root/'assets/skill.json').write_text(json.dumps({'role':role}),encoding='utf-8')
    module.__file__=str(root/'scripts/ioalab.py')
    monkeypatch.setattr(module,'ensure_runtime',lambda:Path(sys.executable))
    return module


@pytest.mark.parametrize('role,operation',[('assistant','runtime.status'),('service','provider.status'),
    ('assistant','runtime.web-extension'),('service','runtime.web-extension')])
def test_bootstrap_only_forwards_user_paths_without_opening_them(tmp_path,monkeypatch,capsys,role,operation):
    module=product(tmp_path,monkeypatch,role);calls=[]
    def invoke(python,arguments):
        calls.append(arguments)
        return SimpleNamespace(returncode=0,stdout='{"status":"completed","data":{},"error":null}',stderr='')
    monkeypatch.setattr(module,'invoke',invoke)
    config=tmp_path/'not-opened-settings.json';args=tmp_path/'not-opened-arguments.json'
    assert module.main([operation,'--config',str(config),'--args-file',str(args)])==0
    result=json.loads(capsys.readouterr().out);assert result['status']=='completed'
    assert len(calls)==1 and str(config.resolve()) in calls[0] and str(args.resolve()) in calls[0]
    assert '--home' not in calls[0] and not config.exists() and not args.exists()
    if operation=='runtime.web-extension':assert calls[0][-2:]==['--role',role]


def test_bom_configuration_and_arguments_cross_actual_product_cli_and_ipc(platform,tmp_path,monkeypatch,capsys):
    path,_=configuration(platform,tmp_path)
    path.write_text(json.dumps(read_json(path),ensure_ascii=False),encoding='utf-8-sig')
    original=path.read_bytes();config=RuntimeConfig.load(path)
    runtime=ResidentRuntime(config,modules=(ProviderRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        module=product(tmp_path,monkeypatch,'service')
        args=tmp_path/'起草参数.json';args.write_text(json.dumps({'profile':'service','terms':terms()},ensure_ascii=False),encoding='utf-8-sig')
        command=['provider.template.draft','--config',str(path),'--args-file',str(args)]
        assert module.main(command)==0
        one=json.loads(capsys.readouterr().out)
        assert one['status']=='completed' and one['data']['template']['kind']=='template'
        assert module.main(command)==0
        two=json.loads(capsys.readouterr().out)
        assert one==two and read_json(args)['operationId']==one['operationId']
        assert path.read_bytes()==original and 'password' not in json.dumps(one)
    finally:runtime.close()


@pytest.mark.parametrize('alias',['same','hardlink','runtime_data'])
def test_parameters_cannot_rewrite_account_configuration_or_runtime_data(tmp_path,monkeypatch,capsys,alias):
    config=tmp_path/'settings.json';home=tmp_path/'state'
    private_json(config,{'data_dir':'state','password':'never-return-this-test-value'})
    target=config
    if alias=='hardlink':
        target=tmp_path/'arguments.json';os.link(config,target)
    elif alias=='runtime_data':
        target=home/'runtime.json';private_json(target,{'token':'never-rewrite-runtime-token'})
    original=config.read_bytes();target_original=target.read_bytes()
    def no_rpc(*args,**kwargs):pytest.fail('Rejected files reached RPC')
    monkeypatch.setattr('ioalab_sdk.runtime.__main__.LocalClient',no_rpc)
    assert main(['call','runtime.stop','--config',str(config),'--args-file',str(target)])==1
    text=capsys.readouterr().out;result=json.loads(text)
    assert result['error']['code']=='invalid_input' and result['error']['recovery']=='correct_input'
    assert config.read_bytes()==original and target.read_bytes()==target_original
    assert 'never-return-this-test-value' not in text and 'never-rewrite-runtime-token' not in text


@pytest.mark.parametrize('change',['missing_secret','broken_business'])
def test_original_instance_can_be_diagnosed_and_stopped_with_only_its_locator(platform,tmp_path,capsys,change):
    path,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(path)).start()
    try:
        before=LocalClient(runtime.config.home).call('runtime.status')['data']
        value=read_json(path)
        if change=='missing_secret':value.pop('password',None);value['password_env']='IOALAB_UNSET_DIAGNOSTIC_ONLY_CREDENTIAL'
        else:value['provider']='not-a-business-object';value['unsupported_field']='not-accepted-for-business'
        private_json(path,value);original=path.read_bytes()
        assert main(['call','runtime.status','--config',str(path)])==0
        status=json.loads(capsys.readouterr().out)
        assert status['data']['bootId']==before['bootId']
        args=tmp_path/'stop.json';private_json(args,{})
        assert main(['call','runtime.stop','--config',str(path),'--args-file',str(args)])==0
        stopped=json.loads(capsys.readouterr().out)
        assert stopped['data']['bootId']==before['bootId'] and path.read_bytes()==original
        assert read_json(args)['bootId']==before['bootId']
    finally:runtime.close()


@pytest.mark.parametrize('change,code',[('account','identity_conflict'),('configuration','configuration_changed')])
def test_business_configuration_mismatch_has_no_effect_or_argument_rewrite(platform,tmp_path,capsys,change,code):
    path,_=configuration(platform,tmp_path,service=False);effects=[]
    runtime=ResidentRuntime(RuntimeConfig.load(path),operations={'test.effect':lambda *_:(effects.append(True) or {'status':'completed','data':{}})}).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        before=LocalClient(runtime.config.home).call('runtime.status')['data']
        value=read_json(path)
        if change=='account':value['username']='another-owner'
        else:value['max_model_runs']=1 if value.get('max_model_runs',2)!=1 else 2
        private_json(path,value);args=tmp_path/'effect.json';private_json(args,{})
        original=args.read_bytes()
        assert main(['call','test.effect','--config',str(path),'--args-file',str(args)])==1
        assert json.loads(capsys.readouterr().out)['error']['code']==code
        assert not effects and args.read_bytes()==original
        assert LocalClient(runtime.config.home).call('runtime.status')['data']['bootId']==before['bootId']
    finally:runtime.close()


def test_bad_json_is_an_input_error_without_guessing_another_instance(tmp_path,monkeypatch,capsys):
    path=tmp_path/'settings.json';path.write_text('{bad',encoding='utf-8')
    monkeypatch.setattr('ioalab_sdk.runtime.__main__.LocalClient',lambda *_:pytest.fail('Guessed another instance'))
    assert main(['call','runtime.status','--config',str(path)])==1
    value=json.loads(capsys.readouterr().out)
    assert value['status']=='rejected' and value['error']['code']=='invalid_input'
    assert path.read_text()=='{bad'


def test_removed_home_cli_flag_is_not_a_compatibility_alias(tmp_path):
    with pytest.raises(SystemExit) as error:main(['call','runtime.status','--home',str(tmp_path)])
    assert error.value.code==2
