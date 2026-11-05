"""Real SDK/HTTP preset publications; Linux root authority is explicitly isolated."""
from conftest import platform_settings
from copy import deepcopy
import json
import os
from pathlib import Path
import threading

import httpx
import pytest

from deploy.layout import Layout
from ioalab_admin_assistant import deployment as configuration, preparation
from ioalab_admin_assistant.deployment import Deployment
from ioalab_sdk.runtime.lease import Lease
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json, read_json
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_tools.schedule import LocalSchedule
from conftest import free_port, wait_for


@pytest.fixture
def platform(tmp_path):
    # ISC_001 is intentionally fixed; each experiment needs its own actual core.
    import uvicorn
    from ioalab.config import Settings
    from ioalab.app import Platform
    from ioalab.web.app import create_app
    core=Platform(platform_settings(port=free_port(),data=tmp_path/'isolated-core',autoseed=True))
    server=uvicorn.Server(uvicorn.Config(create_app(core),host='127.0.0.1',port=core.s.port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    wait_for(lambda:server.started)
    yield core
    server.should_exit=True;thread.join(8)
    assert not thread.is_alive()


@pytest.fixture
def managed(platform,tmp_path,monkeypatch):
    # Test only the fixed preparation lifecycle, not operating-system root privileges.
    monkeypatch.setattr(configuration,'operator',lambda:None)
    monkeypatch.setattr(preparation,'operator',lambda:None)
    monkeypatch.setattr(configuration,'owned',lambda *_a,**_k:None)
    monkeypatch.setattr(preparation,'owned',lambda *_a,**_k:None)
    monkeypatch.delenv('IOALAB_ADMIN_WEB_SECRET',raising=False)
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'etc')
    identity=layout.claim()
    workspace=layout.state/'agents/swarm-admin'
    (workspace/'config').mkdir(parents=True)
    (workspace/'config/config.yaml').write_text('original: preserved\n',encoding='utf-8')
    recipe=layout.private/'admin-assistant/config.json'
    private_json(recipe,{'deployment_id':identity['deploymentId'],'platform_url':platform.s.base_url,'password':'001',
        'host':{'command':['existing-original-jiuwenswarm-process'],'data_dir':str(workspace),'model':'existing-model'},
        'web_url':'ws://127.0.0.1:18901/ws?user_id=ioa-admin','recipient':'zhengw.cc@outlook.com'})
    return Deployment(recipe)


def rows(platform):
    return platform.db.rows('SELECT * FROM message_interfaces ORDER BY name')


def snapshot(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_fixed_presets_use_same_sdk_without_running_host_or_business(platform,managed,monkeypatch):
    from ioalab_jiuwen.process import ProcessHost
    from ioalab_jiuwen.web_notifications import WebNotifications
    monkeypatch.setattr(ResidentRuntime,'start',lambda *_:pytest.fail('No receiver or scheduler during prepare'))
    monkeypatch.setattr(ProcessHost,'probe',lambda *_:pytest.fail('No model during prepare'))
    monkeypatch.setattr(WebNotifications,'notify',lambda *_:pytest.fail('No notification during prepare'))
    before=(managed.workspace/'config/config.yaml').read_bytes()
    value=preparation.prepare(managed)
    assert value['status']=='completed' and value['modelStarted'] is False
    assert value['runtimeChecked'] is value['currentInterfacesChecked'] is False
    assert (managed.workspace/'config/config.yaml').read_bytes()==before
    assert (managed.workspace/'application_plugins/ioa-notifications/extension.yaml').is_file()
    interfaces=rows(platform)
    assert [r['name'] for r in interfaces]==['meeting.request','message.forward']
    assert [r['enabled'] for r in interfaces]==[0,1]
    state=managed.state()
    assert state['identity']['profile']=='admin'
    config=RuntimeConfig.load(managed.runtime_path)
    runtime=ResidentRuntime(config)
    try:
        approvals=runtime.confirmations.list()
        assert len(approvals)==2
        assert all(c['source'].startswith('deployment-preset:'+managed.value['deployment_id']+':') for c in approvals)
        assert all(c['stage']=='rules.publish' and c['action']=='interface.publish' for c in approvals)
        assert runtime.store.summary()['pendingOperations']==0
    finally:runtime.close()
    secret=state['webSecret']
    assert secret not in json.dumps(value)
    assert not managed.schedule.exists()


def test_completed_setup_preserves_manual_disable_config_and_never_logs_in_again(platform,managed,monkeypatch):
    preparation.prepare(managed)
    item=rows(platform)[1]
    with platform.db.tx() as connection:
        connection.execute('UPDATE message_interfaces SET enabled=0 WHERE owner=? AND name=?',(item['owner'],item['name']))
    config=read_json(managed.runtime_path)
    config['max_model_runs']=7
    config['host']['model']='operator-selected-model'
    private_json(managed.runtime_path,config)
    before=snapshot(managed.home)
    monkeypatch.setattr(preparation,'ResidentRuntime',lambda *_a,**_k:pytest.fail('Completed setup must not create a runtime'))
    assert preparation.prepare(managed)['phase']=='prepared'
    assert snapshot(managed.home)==before
    assert [r['revision'] for r in rows(platform)]==[1,1]
    assert not rows(platform)[1]['enabled']
    assert preparation.status(managed)['currentInterfacesChecked'] is False


def test_lost_publish_reply_recovers_original_operation_and_identity(platform,managed,monkeypatch):
    original=httpx.Client.request
    lost=[]
    def lose(self,method,url,*args,**kwargs):
        response=original(self,method,url,*args,**kwargs)
        if method=='POST' and str(url).endswith('/messaging/interfaces') and response.is_success and not lost:
            lost.append(kwargs['json']['operationId']);raise httpx.ReadError('isolated lost receipt')
        return response
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'request',lose)
        with pytest.raises(httpx.ReadError,match='lost receipt'):preparation.prepare(managed)
    saved=managed.state();actor=saved['identity']['agentId']
    assert lost==[saved['presets']['message.forward']['operationId']]
    assert preparation.prepare(managed)['status']=='completed'
    done=managed.state()
    assert done['identity']['agentId']==actor
    assert done['presets']['message.forward']['operationId']==lost[0]
    assert [r['revision'] for r in rows(platform)]==[1,1]
    assert len(done['presets'])==2


@pytest.mark.parametrize('after_write',[False,True])
def test_final_config_crash_resumes_without_reopening_sdk(managed,monkeypatch,after_write):
    original=preparation.private_json
    def crash(path,value):
        final=path==managed.runtime_path and 'host' in value
        if final and not after_write:raise OSError('isolated final write crash')
        original(path,value)
        if final:raise OSError('isolated final write crash')
    with monkeypatch.context() as patch:
        patch.setattr(preparation,'private_json',crash)
        with pytest.raises(OSError,match='final write crash'):preparation.prepare(managed)
    assert managed.state()['phase']=='finishing'
    with monkeypatch.context() as patch:
        patch.setattr(preparation,'ResidentRuntime',lambda *_a,**_k:pytest.fail('Do not recreate completed SDK preparation'))
        assert preparation.prepare(managed)['status']=='completed'


def test_active_sdk_lease_refuses_preparation_without_new_identity(managed):
    home=managed.home/'resident'
    # The deployment record must precede local state; do not adopt an unrelated folder.
    managed.save(managed.state())
    with Lease(home/'client.lock',timeout=0):
        with pytest.raises(RuntimeError,match='already in use'):preparation.prepare(managed)
    assert not (home/'identity-index.json').exists()


def test_missing_original_files_and_resource_change_are_not_reinitialized(managed,monkeypatch):
    preparation.prepare(managed)
    index=managed.home/'resident/identity-index.json'
    original=index.read_bytes();index.unlink()
    with pytest.raises(RuntimeFault,match='original administrator'):preparation.prepare(managed)
    assert not index.exists()
    index.write_bytes(original)
    value=read_json(managed.path);value['recipient']='changed@example.com';private_json(managed.path,value)
    with pytest.raises(RuntimeFault,match='original administrator preparation'):Deployment(managed.path).state()


def test_full_notification_explicitly_initializes_schedule_and_preserves_edits(platform,managed):
    value=deepcopy(managed.value)
    value['notification']={'transport':'smtp-capture','host':'127.0.0.1','port':1025,'sender':'notice@example.test'}
    private_json(managed.path,value);managed=Deployment(managed.path)
    assert preparation.prepare(managed)['meetingInitiallyEnabled'] is True
    schedule=LocalSchedule(managed.schedule)
    shown=schedule.show();doc=shown['document'];doc['rules']['startHour']=11
    schedule.replace(doc,shown['revision']);before=managed.schedule.read_bytes()
    preparation.prepare(managed)
    assert managed.schedule.read_bytes()==before
    assert all(row['enabled'] for row in rows(platform))
    managed.schedule.unlink()
    with pytest.raises(FileNotFoundError):preparation.prepare(managed)
    assert not managed.schedule.exists()


def test_status_is_read_only_and_unregistered_instance_is_refused(managed):
    before=snapshot(managed.state_root)
    assert preparation.status(managed)['phase']=='preparing'
    assert snapshot(managed.state_root)==before
    managed.home.mkdir(parents=True)
    with pytest.raises(RuntimeFault,match='unregistered administrator'):preparation.prepare(managed)


@pytest.mark.parametrize('field,value',[
    ('deployment_id','0'*32),('web_url','ws://example.com/ws?user_id=ioa-admin'),
    ('web_url','ws://127.0.0.1:18901/ws?user_id=someone'),('username','another-account'),
    ('rules',[{'name':'arbitrary.permission'}])])
def test_recipe_cannot_select_another_owner_target_or_arbitrary_permission(managed,field,value):
    changed=deepcopy(managed.value);changed[field]=value;private_json(managed.path,changed)
    with pytest.raises(RuntimeFault):Deployment(managed.path)
    assert not managed.home.exists()


def test_root_requirement_is_not_a_configuration_switch(monkeypatch):
    if os.name=='posix':
        monkeypatch.setattr(os,'geteuid',lambda:1000)
        with pytest.raises(RuntimeFault,match='Linux deployment root'):configuration.operator()
    else:
        def unavailable():raise RuntimeFault('not_ready','Local owner SID unavailable')
        monkeypatch.setattr(configuration,'windows_sid',unavailable)
        with pytest.raises(RuntimeFault,match='Local owner SID unavailable'):configuration.operator()


def test_interrupted_fixed_confirmation_is_resumed_without_new_reference(managed,monkeypatch):
    from ioalab_sdk.runtime.confirmations import Confirmations
    original=Confirmations.decide
    def interrupt(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise OSError('isolated after trusted decision')
    with monkeypatch.context() as patch:
        patch.setattr(Confirmations,'decide',interrupt)
        with pytest.raises(OSError,match='trusted decision'):preparation.prepare(managed)
    state=managed.state();before=deepcopy(state['presets']['message.forward'])
    preparation.prepare(managed)
    after=managed.state()['presets']['message.forward']
    assert after['operationId']==before['operationId']
    assert after['confirmation']==before['confirmation']


def test_expired_initial_confirmation_is_not_replaced_or_approved(managed,monkeypatch):
    from ioalab_sdk.runtime.confirmations import Confirmations
    original=Confirmations.decide
    def expire(self,*args,**kwargs):
        self.clock=lambda:10**12
        return original(self,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Confirmations,'decide',expire)
        with pytest.raises(RuntimeFault,match='expired'):preparation.prepare(managed)
    saved=deepcopy(managed.state()['presets']['message.forward'])
    initialize=Confirmations.__init__
    def future(self,*args,**kwargs):
        initialize(self,*args,**kwargs)
        self.clock=lambda:10**12
    monkeypatch.setattr(Confirmations,'__init__',future)
    with pytest.raises(RuntimeFault,match='expired'):preparation.prepare(managed)
    assert managed.state()['presets']['message.forward']==saved


def test_changed_local_draft_cannot_receive_fixed_preset_permission(managed,monkeypatch):
    from ioalab_sdk.assistant.rules import Rules
    original=Rules.draft
    def changed(self,*args,**kwargs):
        value=original(self,*args,**kwargs)
        path=Path(value['path'])
        path.write_text(path.read_text(encoding='utf-8')+'\nModified local instructions.\n',encoding='utf-8')
        return value
    monkeypatch.setattr(Rules,'draft',changed)
    with pytest.raises(RuntimeFault,match='packaged fixed preset'):preparation.prepare(managed)
    assert 'confirmation' not in managed.state()['presets']['message.forward']
