"""Real pre-start commands preserve credentials and fence a live instance."""
import json
from pathlib import Path
import sys

import pytest

from ioalab_sdk.runtime.lease import Lease
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_jiuwen.__main__ import main
from test_configuration_boundary import product
from test_resident_runtime import configuration


SECRET='PRIVATE-ACCOUNT-CREDENTIAL-SENTINEL'
RESOURCE='PRIVATE-RESOURCE-SENTINEL'
TOOL='PRIVATE-TOOL-OPTION-SENTINEL'
HOST='PRIVATE-COMMAND-ARGUMENT-SENTINEL'


def settings(tmp_path):
    path=tmp_path/'settings.json'
    value={'platform_url':'http://127.0.0.1:1','username':'PRIVATE-ACCOUNT-NAME','password':SECRET,
           'data_dir':'state','identities':[{'key':'personal','role':'assistant','name':'Owner'},
                                           {'key':'existing','role':'service','name':'Existing'}],
           'provider':{'services':{'existing':{'instructions':'Original instructions','resources':{'private':RESOURCE}}}},
           'host':{'command':['unchanged-original-process',HOST],'data_dir':'unchanged-jiuwen',
                   'business_tools':[{'factory':'local-only','profile':'personal','options':{'credential':TOOL}}]}}
    private_json(path,value)
    return path,value


def command(capsys,name,path,args=None):
    argv=[name,'--config',str(path)]
    if args is not None:argv+=['--args-file',str(args)]
    code=main(argv);text=capsys.readouterr().out
    for secret in (SECRET,RESOURCE,TOOL,HOST,'PRIVATE-ACCOUNT-NAME'):
        assert secret not in text
    return code,json.loads(text)


def arguments(tmp_path,path,changes,*,expected=None,name='changes.json'):
    file=tmp_path/name
    private_json(file,{'expectedDigest':expected or RuntimeConfig.load(path).public_summary()['configurationDigest'],
                       'changes':changes})
    return file


def test_offline_inspection_and_validation_do_not_read_tools_start_hosts_or_create_state(tmp_path,monkeypatch,capsys):
    path,original=settings(tmp_path);before=path.read_bytes()
    monkeypatch.setattr('ioalab_jiuwen.process.ProcessHost.probe',lambda *_:pytest.fail('Setup started a host'))
    monkeypatch.setattr('ioalab_jiuwen.tools.ConfiguredTools',lambda *_a,**_k:pytest.fail('Setup loaded private tools'))
    for name in ('configuration','validate'):
        code,result=command(capsys,name,path)
        assert code==0 and result['status']=='completed'
        assert result['data']['identities']==original['identities']
    code,result=command(capsys,'configuration',path)
    assert result['data']['configuredServices']==['existing']
    assert 'host.skill_directories' in result['data']['editableFields']
    assert set(result['data'])=={'configurationDigest','dataDir','identities','configuredServices','hostFields','editableFields'}
    assert path.read_bytes()==before and not (tmp_path/'state').exists()
    assert not (tmp_path/'unchanged-jiuwen').exists()


def test_partial_service_and_skill_configuration_preserves_other_role_secrets_and_replay(tmp_path,capsys):
    path,original=settings(tmp_path)
    changes={'identities':[{'key':'programming','role':'service','name':'Programming'}],
             'provider':{'services':{'programming':{'instructions':'Build requested programs','skills':['coding'],
                 'native_tools':{'write_file':'allow'},'max_active_delegations':3}}},
             'host':{'skill_directories':{'coding':str(tmp_path/'business-skill')}},'max_model_runs':1}
    args=arguments(tmp_path,path,changes);before=args.read_bytes()
    code,result=command(capsys,'configure',path,args)
    assert code==0 and result['data']['changed'] is True
    actual=read_json(path)
    for key in ('platform_url','username','password','data_dir'):assert actual[key]==original[key]
    assert actual['provider']['services']['existing']==original['provider']['services']['existing']
    assert actual['host']['business_tools']==original['host']['business_tools']
    assert actual['host']['command']==original['host']['command']
    assert actual['identities'][:2]==original['identities']
    assert actual['provider']['services']['programming']['max_active_delegations']==3
    assert args.read_bytes()==before and not (tmp_path/'state/account.sqlite').exists()
    once=path.read_bytes()
    code,replay=command(capsys,'configure',path,args)
    assert code==0 and replay['data']['changed'] is False
    assert path.read_bytes()==once and args.read_bytes()==before
    assert result['data']['configurationDigest']==replay['data']['configurationDigest']
    assert not (tmp_path/'unchanged-jiuwen').exists()


@pytest.mark.parametrize('changes',[
    {'password':'should-not-be-accepted'}, {'username':'other'}, {'platform_url':'https://other.invalid'},
    {'data_dir':'another-state'}, {'host':{'business_tools':[]}}, {'provider':{'require_publication_authorization':False}},
    {'provider':{'services':{'existing':{'resources':{'anything':'new'}}}}},
    {'identities':[{'key':'personal','role':'service','name':'wrong-role'}]},
    {'identities':[{'key':'Personal','role':'assistant','name':'case-collision'}]},
    {'provider':{'services':{'personal':{'instructions':'wrong role'}}}},
    {'provider':{'services':{'existing':{'max_active_delegations':True}}}},
    {'provider':{'services':{'existing':{'skills':['coding',[]]}}}},
    {'host':{'model':{'not':'a selection key'}}}, {'host':{'data_dir':None}},
    {'host':{'skill_directories':{'coding':'relative/not-an-installed-directory'}}},
])
def test_invalid_or_private_fields_have_no_file_or_runtime_effect(tmp_path,capsys,changes):
    path,_=settings(tmp_path);before=path.read_bytes();args=arguments(tmp_path,path,changes)
    code,result=command(capsys,'configure',path,args)
    assert code==1 and result['status']=='rejected'
    assert result['error']['code'] in ('invalid_input','identity_conflict')
    assert path.read_bytes()==before and not (tmp_path/'state').exists()


def test_stale_preview_preserves_new_manual_configuration(tmp_path,capsys):
    path,_=settings(tmp_path);args=arguments(tmp_path,path,{'max_model_runs':1})
    value=read_json(path);value['max_model_runs']=3;private_json(path,value);before=path.read_bytes()
    code,result=command(capsys,'configure',path,args)
    assert code==1 and result['error']['code']=='configuration_changed'
    assert path.read_bytes()==before and not (tmp_path/'state/account.sqlite').exists()


@pytest.mark.parametrize('host_directory',['state','state/swarm','.'])
def test_host_and_sdk_directories_cannot_overlap(tmp_path,capsys,host_directory):
    path,_=settings(tmp_path);before=path.read_bytes()
    args=arguments(tmp_path,path,{'host':{'data_dir':str(tmp_path/host_directory)}})
    code,result=command(capsys,'configure',path,args)
    assert code==1 and result['error']['code']=='invalid_input'
    assert path.read_bytes()==before and not (tmp_path/'state').exists()


def test_assembly_rejects_mixed_host_directory_before_loading_resources(tmp_path,monkeypatch):
    from ioalab_jiuwen import assembled
    path,value=settings(tmp_path);value['host']['data_dir']='state';private_json(path,value)
    monkeypatch.setattr('ioalab_jiuwen.assembly.ConfiguredTools',lambda *_a,**_k:pytest.fail('Mixed directory loaded private resources'))
    with pytest.raises(RuntimeFault) as error:
        with assembled(path):pytest.fail('Mixed directory started an assembly')
    assert error.value.code=='invalid_input' and not (tmp_path/'state').exists()


def test_live_instance_lock_prevents_configuration_changes(tmp_path,capsys):
    path,_=settings(tmp_path);args=arguments(tmp_path,path,{'max_model_runs':1})
    before=path.read_bytes()
    with Lease(tmp_path/'state/client.lock',timeout=0):
        code,result=command(capsys,'configure',path,args)
    assert code==1 and result['error']['recovery']=='runtime.stop'
    assert path.read_bytes()==before and not (tmp_path/'state/account.sqlite').exists()


def test_startup_rechecks_file_after_lock_before_any_authentication(tmp_path,monkeypatch):
    path,_=settings(tmp_path);loaded=RuntimeConfig.load(path)
    value=read_json(path);value['max_model_runs']=1;private_json(path,value)
    monkeypatch.setattr('ioalab_sdk.runtime.resident.AccountSession',lambda *_a,**_k:pytest.fail('Stale setup authenticated'))
    with pytest.raises(RuntimeFault) as exc:ResidentRuntime(loaded)
    assert exc.value.code=='configuration_changed'
    assert not (tmp_path/'state/account.sqlite').exists() and not (tmp_path/'state/runtime.sqlite').exists()
    with Lease(tmp_path/'state/client.lock',timeout=0):pass


def test_configuration_file_cannot_be_its_own_arguments(tmp_path,capsys):
    path,_=settings(tmp_path);before=path.read_bytes()
    code,result=command(capsys,'configure',path,path)
    assert code==1 and result['error']['code']=='invalid_input'
    assert path.read_bytes()==before and not (tmp_path/'state').exists()


def test_actual_product_entry_can_configure_without_reading_settings(tmp_path,monkeypatch,capsys):
    path,_=settings(tmp_path);module=product(tmp_path,monkeypatch,'service')
    assert module.main(['runtime.configuration','--config',str(path)])==0
    text=capsys.readouterr().out;result=json.loads(text)
    assert result['data']['configuredServices']==['existing']
    assert all(value not in text for value in (SECRET,RESOURCE,TOOL,HOST))
    args=arguments(tmp_path,path,{'max_model_runs':1})
    assert module.main(['runtime.configure','--config',str(path),'--args-file',str(args)])==0
    text=capsys.readouterr().out
    assert json.loads(text)['data']['changed'] and all(value not in text for value in (SECRET,RESOURCE,TOOL,HOST))
    assert read_json(path)['password']==SECRET


def test_real_instance_stops_before_update_and_restarts_with_same_identity(platform,tmp_path,capsys):
    path,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(path)).start()
    try:
        original=runtime.identities[0].agent_id;account=runtime.account.account['id']
        args=arguments(tmp_path,path,{'max_model_runs':1})
        before=path.read_bytes();code,result=command(capsys,'configure',path,args)
        assert code==1 and result['error']['recovery']=='runtime.stop' and path.read_bytes()==before
    finally:runtime.close()
    code,result=command(capsys,'configure',path,args)
    assert code==0 and result['data']['changed']
    with ResidentRuntime(RuntimeConfig.load(path)) as restored:
        assert restored.identities[0].agent_id==original
        assert restored.account.account['id']==account and restored.config.max_model_runs==1


def test_edit_detects_change_while_waiting_for_ownership(tmp_path,monkeypatch,capsys):
    path,_=settings(tmp_path);args=arguments(tmp_path,path,{'max_model_runs':1})
    actual_lease=Lease
    def racing(path_lock,**kwargs):
        current=read_json(path);current['max_model_runs']=4;private_json(path,current)
        return actual_lease(path_lock,**kwargs)
    monkeypatch.setattr('ioalab_sdk.runtime.configuration.Lease',racing)
    code,result=command(capsys,'configure',path,args)
    assert code==1 and result['error']['code']=='configuration_changed'
    assert read_json(path)['max_model_runs']==4
    assert not (tmp_path/'state/account.sqlite').exists()


def test_plain_sdk_setup_has_no_concrete_host_write_authority(tmp_path,capsys):
    from ioalab_sdk.access import main as sdk_main
    path,_=settings(tmp_path)
    args=arguments(tmp_path,path,{'max_model_runs':1})
    assert sdk_main(['configure','--config',str(path),'--args-file',str(args)])==0
    output=capsys.readouterr().out
    assert json.loads(output)['data']['changed'] and SECRET not in output
    before=path.read_bytes();args=arguments(tmp_path,path,{'host':{'model':'another'}})
    assert sdk_main(['configure','--config',str(path),'--args-file',str(args)])==1
    assert json.loads(capsys.readouterr().out)['error']['code']=='invalid_input'
    assert path.read_bytes()==before
