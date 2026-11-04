"""Fixed upgrade fault recovery with real SDK-format OS locks and private file archives."""
import copy
import json
import os
from pathlib import Path
import uuid

import pytest

from deploy import installation
from deploy.environment import writer
from deploy.layout import Layout


class Manager:
    def __init__(self):
        self.states={'ioalab-core.service':{'ActiveState':'active','InvocationID':'core-original','UnitFileState':'enabled'},
            'ioalab-official.service':{'ActiveState':'inactive','InvocationID':'official-original','UnitFileState':'disabled'}}
        self.stops=[];self.starts=[];self.fail_install=False;self.fail_start=False
    def freeze(self,names):return copy.deepcopy({name:self.states[name] for name in names})
    def owned(self,name):return dict(self.states[name])
    def request_stop(self,name,before):
        assert self.states[name]['InvocationID']==before['InvocationID']
        self.stops.append(name);self.states[name]['ActiveState']='inactive'
    def released(self,name,before):return self.states[name]['ActiveState']=='inactive'
    def install(self,contents):
        if self.fail_install:self.fail_install=False;raise RuntimeError('lost install completion')
    def start(self,name):
        self.starts.append(name);self.states[name].update(ActiveState='active',InvocationID='new-invocation')
        if self.fail_start:self.fail_start=False;raise RuntimeError('lost restart completion')


@pytest.fixture
def setup(tmp_path,monkeypatch):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    (layout.state/'core').mkdir();(layout.state/'core/db').write_text('preserve signed original')
    official=layout.state/'agents/official';home=official/'accounts/ISP_001';(home/'resident').mkdir(parents=True)
    (official/'state.json').write_text(json.dumps({'accounts':{'ISP_001':{'phase':'prepared'}}}))
    (official/'deployment.lock').write_bytes(b'0')
    (home/'config.json').write_text(json.dumps({'data_dir':'./resident'}))
    lock=home/'resident/client.lock';lock.write_bytes(b'0')
    (layout.config/'agents').mkdir();(layout.config/'agents/official.json').write_text(json.dumps({'data_dir':str(official)}))
    current={'setId':'old'};candidate={'setId':'new'};activations=[]
    def activate(selected,value,previous):
        with pytest.raises(RuntimeError,match='writer lock'):
            with writer(lock):pass
        assert current=={'setId':previous} or current==value
        activations.append(value['setId']);current.clear();current.update(value)
    monkeypatch.setattr(installation.activation,'current',lambda _:dict(current))
    monkeypatch.setattr(installation.activation,'activate',activate)
    real_capture=installation.backup.capture;captures=[]
    def capture(*args,**kwargs):
        with pytest.raises(RuntimeError,match='writer lock'):
            with writer(lock):pass
        captures.append(1);return real_capture(*args,**kwargs)
    monkeypatch.setattr(installation.backup,'capture',capture)
    plans={'core':{'unit':'ioalab-core.service'},'official':{'unit':'ioalab-official.service'}}
    manager=Manager();record={};saved=[]
    flow=installation.Upgrade(layout,uuid.uuid4().hex,candidate,manager,plans,{},record,lambda:saved.append(copy.deepcopy(record)))
    return flow,manager,record,lock,captures,activations,current


@pytest.mark.skipif(os.name!='posix',reason='Linux deployment archives while holding advisory flock locks; Windows byte locks prohibit another reader')
def test_upgrade_holds_sdk_locks_through_snapshot_and_switch_and_preserves_manual_stop(setup):
    flow,manager,record,lock,captures,activations,current=setup
    reply=flow.run()
    assert reply['stage']=='completed' and reply['runtimeReadyChecked'] is False
    assert len(captures)==1 and activations==['new']
    assert manager.stops==['ioalab-official.service','ioalab-core.service']
    assert manager.starts==['ioalab-core.service']
    assert manager.states['ioalab-official.service']['UnitFileState']=='disabled'
    assert manager.states['ioalab-official.service']['ActiveState']=='inactive'
    with writer(lock):pass


@pytest.mark.skipif(os.name!='posix',reason='Linux deployment archives while holding advisory flock locks; Windows byte locks prohibit another reader')
def test_lost_unit_install_resumes_original_snapshot_without_second_archive(setup):
    flow,manager,record,_,captures,activations,current=setup;manager.fail_install=True
    with pytest.raises(RuntimeError,match='lost install'):flow.run()
    assert record['stage']=='activating' and current=={'setId':'new'} and len(captures)==1
    flow.run()
    assert record['stage']=='completed' and len(captures)==1 and manager.starts==['ioalab-core.service']


@pytest.mark.skipif(os.name!='posix',reason='Linux deployment archives while holding advisory flock locks; Windows byte locks prohibit another reader')
def test_lost_restart_never_drains_new_instance_or_repeats_backup(setup):
    flow,manager,record,_,captures,_,_=setup;manager.fail_start=True
    with pytest.raises(RuntimeError,match='lost restart'):flow.run()
    original_stops=list(manager.stops)
    assert record['stage']=='restarting' and manager.states['ioalab-core.service']['InvocationID']=='new-invocation'
    flow.run()
    assert record['stage']=='completed' and manager.stops==original_stops and len(captures)==1


def test_locked_original_sdk_prevents_backup_or_activation(setup):
    flow,manager,record,lock,captures,activations,current=setup
    with writer(lock):
        with pytest.raises(RuntimeError,match='writer lock'):flow.run()
    assert record['stage']=='snapshot' and not captures and not activations and current=={'setId':'old'}


@pytest.mark.skipif(os.name!='posix',reason='Linux deployment archives while holding advisory flock locks; Windows byte locks prohibit another reader')
def test_boot_enablement_change_is_reported_without_restarting_another_service(setup):
    flow,manager,record,_,_,_,_=setup;manager.fail_install=True
    with pytest.raises(RuntimeError):flow.run()
    manager.states['ioalab-core.service']['UnitFileState']='disabled'
    with pytest.raises(RuntimeError,match='boot enablement'):flow.run()
    assert not manager.starts
