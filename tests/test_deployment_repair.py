"""Narrow repair admission, immutable parent evidence and interrupted submission."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from deploy import repair
from deploy.environment import writer
from deploy.jobs import Job, SOURCE_FILES, source_identity
from deploy.layout import Layout
from deploy.release import digest
from deploy.workspace import save

REAL_STOPPED_OWNERSHIP=repair.stopped_ownership


@pytest.fixture
def subject(tmp_path, monkeypatch):
    layout = Layout(tmp_path/'runtime', tmp_path/'state', tmp_path/'config')
    identity = layout.claim()
    old, new = tmp_path/'old-source', tmp_path/'new-source'
    for root in (old, new):
        for name in ('deploy', 'tools', 'src', 'vendor'):
            (root/name).mkdir(parents=True)
            (root/name/'fixture.txt').write_text(name)
        for name in SOURCE_FILES:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(name)
        (root/'deploy/units.py').write_text('original')
    (new/'deploy/units.py').write_text('reviewed-path-fix')
    value={'admin_assistant':{'enabled':True}}
    input_digest=digest(value)
    save(layout.private/'initial.json',{'deploymentId':identity['deploymentId'],
        'inputDigest':input_digest,'input':value})
    parent=Job(layout,uuid.uuid4().hex)
    parent.create('install',source_identity(old),input_digest)
    candidate={'setId':'original-set','release':'original-release','units':{'original':'installation'}}
    save(parent.folder/'progress.json',{'stage':'bootstrap','outcome':'failed','candidate':candidate})
    save(layout.private/'installation.json',{'deploymentId':identity['deploymentId'],'units':{}})
    save(layout.private/'current-job.json',repair.pointer_for(parent,parent.request()))
    state={'candidate':candidate,'ownershipChecks':0,'submissions':[]}
    def observe(job):
        p=job.folder/'progress.json'
        v=json.loads(p.read_text()) if p.exists() else {}
        return {'status':state.get('parentStatus','stopped') if job.operation==parent.operation else state.get('childStatus','stopped'),
                'stage':v.get('stage'),'outcome':v.get('outcome')}
    monkeypatch.setattr(Job,'observe',observe)
    def submit(job):
        state['submissions'].append(job.operation)
        return {'status':'waiting','operationId':job.operation}
    monkeypatch.setattr(Job,'submit',submit)
    monkeypatch.setattr(repair.activation,'current',lambda _:copy.deepcopy(state['candidate']))
    def ownership(*_):state['ownershipChecks']+=1
    monkeypatch.setattr(repair,'stopped_ownership',ownership)
    return layout,parent,old,new,state


def selected(subject):
    layout,parent,_,new,_=subject
    return Job(layout,repair.operation_id(parent.operation,source_identity(new)))


def test_child_request_is_complete_and_parent_data_is_unchanged(subject):
    layout,parent,_,new,state=subject
    before={p.name:p.read_bytes() for p in parent.folder.iterdir() if p.is_file()}
    response=repair.submit(layout,new,parent.operation)
    child=selected(subject);request=child.request()
    assert response['operationId']==child.operation
    assert request['action']=='repair-bootstrap'
    assert request['inputDigest']==parent.request()['inputDigest']
    assert request['repair']['parentSource']==parent.request()['source']
    assert request['repair']['candidate']==state['candidate']
    assert set(request['repair']['changes'])=={'deploy/units.py'}
    assert {p.name:p.read_bytes() for p in parent.folder.iterdir() if p.is_file()}==before
    assert state['ownershipChecks']==1


def test_same_child_is_observed_without_repeating_first_admission(subject):
    layout,parent,_,new,state=subject
    first=repair.submit(layout,new,parent.operation)
    state['childStatus']='waiting'
    with writer(layout.private/'installer.lock'):
        assert repair.submit(layout,new,parent.operation)==first
    assert state['ownershipChecks']==1
    assert set(state['submissions'])=={first['operationId']}


def test_request_to_pointer_interruption_reuses_same_immutable_child(subject,monkeypatch):
    layout,parent,_,new,state=subject
    original=repair.save
    def interrupt(path,value):
        if path.name=='current-job.json':raise OSError('lost pointer result')
        original(path,value)
    monkeypatch.setattr(repair,'save',interrupt)
    with pytest.raises(OSError):repair.submit(layout,new,parent.operation)
    child=selected(subject);before=(child.folder/'request.json').read_bytes()
    assert not state['submissions']
    assert json.loads((layout.private/'current-job.json').read_text())['operationId']==parent.operation
    monkeypatch.setattr(repair,'save',original)
    repair.submit(layout,new,parent.operation)
    assert (child.folder/'request.json').read_bytes()==before
    assert len(list((layout.private/'jobs').iterdir()))==2


@pytest.mark.parametrize('change',['src/fixture.txt','vendor/fixture.txt','deploy/configs.py','tools/build.py',SOURCE_FILES[0]])
def test_noncontroller_changes_are_rejected_before_pointer_write(subject,change):
    layout,parent,_,new,state=subject
    p=new/change;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('not-controller-only')
    with pytest.raises(RuntimeError,match='reviewed'):repair.submit(layout,new,parent.operation)
    assert json.loads((layout.private/'current-job.json').read_text())['operationId']==parent.operation
    assert not state['submissions']


@pytest.mark.parametrize('field,value',[('stage','verifying'),('outcome','completed')])
def test_wrong_parent_stage_or_result_is_not_repairable(subject,field,value):
    layout,parent,_,new,state=subject
    path=parent.folder/'progress.json';data=json.loads(path.read_text());data[field]=value;save(path,data)
    with pytest.raises(RuntimeError,match='stopped failed'):repair.submit(layout,new,parent.operation)
    assert not state['submissions']


@pytest.mark.parametrize('status',['waiting','unknown','completed'])
def test_nonstopped_parent_never_launches(subject,status):
    layout,parent,_,new,state=subject;state['parentStatus']=status
    with pytest.raises(RuntimeError,match='stopped failed'):repair.submit(layout,new,parent.operation)
    assert not state['submissions']


def test_changed_original_source_and_current_candidate_are_not_adopted(subject):
    layout,parent,old,new,state=subject
    state['candidate']={'setId':'another'}
    with pytest.raises(RuntimeError,match='candidate changed'):repair.submit(layout,new,parent.operation)
    state['candidate']=json.loads((parent.folder/'progress.json').read_text())['candidate']
    (old/'deploy/units.py').write_text('changed-failed-source')
    with pytest.raises(RuntimeError,match='original failed installation source'):repair.submit(layout,new,parent.operation)
    assert not state['submissions']


def test_deterministic_id_does_not_hide_changed_complete_request(subject):
    layout,parent,_,new,state=subject
    repair.submit(layout,new,parent.operation);child=selected(subject)
    path=child.folder/'request.json';value=json.loads(path.read_text());value['repair']['candidate']={'setId':'another'};save(path,value)
    with pytest.raises(RuntimeError,match='candidate changed'):repair.submit(layout,new,parent.operation)
    assert len(state['submissions'])==1


def test_changed_input_or_other_current_owner_blocks_repair(subject):
    layout,parent,_,new,state=subject
    pointer=layout.private/'current-job.json';save(pointer,{'operationId':uuid.uuid4().hex})
    with pytest.raises(RuntimeError,match='Another original operation'):repair.submit(layout,new,parent.operation)
    save(pointer,repair.pointer_for(parent,parent.request()))
    path=layout.private/'initial.json';value=json.loads(path.read_text());value['input']['changed']=True;save(path,value)
    with pytest.raises(RuntimeError,match='complete installation input'):repair.submit(layout,new,parent.operation)
    assert not state['submissions']


@pytest.mark.parametrize('lock',['launcher.lock','installer.lock'])
def test_real_os_lock_blocks_a_second_repair_writer(subject,lock):
    layout,parent,_,new,state=subject
    with writer(layout.private/lock):
        with pytest.raises(RuntimeError,match='writer lock'):repair.submit(layout,new,parent.operation)
    assert not state['submissions']


def test_worker_checks_first_admission_again_then_allows_same_stage_resume(subject):
    layout,parent,_,new,state=subject
    repair.submit(layout,new,parent.operation);child=selected(subject);request=child.request()
    repair.worker_admission(child,request)
    assert state['ownershipChecks']==2
    save(child.folder/'progress.json',repair.initial_progress(request,{'core':{}}))
    repair.worker_admission(child,request)
    assert state['ownershipChecks']==2
    state['candidate']={'setId':'replaced-between-attempts'}
    with pytest.raises(RuntimeError,match='candidate changed'):repair.worker_admission(child,request)


def test_initial_progress_reuses_existing_candidate_without_build_or_activation(subject):
    layout,parent,_,new,state=subject
    repair.submit(layout,new,parent.operation);request=selected(subject).request()
    result=repair.initial_progress(request,{'core':{},'official':{}})
    assert result=={'stage':'bootstrap','candidate':state['candidate'],'requiredRunning':['core','official']}
    assert repair.initial_progress({'action':'install'}, {})=={'stage':'candidate'}


@pytest.mark.parametrize('fault',[None,'active','populated','account','unit-map','path-phase'])
def test_stopped_ownership_uses_existing_checks_and_never_creates(subject,monkeypatch,fault):
    import sys
    layout,parent,_,_,_=subject
    calls=[]
    records={'deploymentId':layout.inspect()['deploymentId'],
             'accounts':{role:{'phase':'ready','name':'ioalab-'+role} for role in ('core','agents')},
             'paths':{str(layout.state/'core'):{'phase':'owned','role':'core'}},
             'units':{'ioalab-core.service':{'phase':'ready'},'ioalab-official.service':{'phase':'ready'}}}
    if fault=='unit-map':records['units'].pop('ioalab-official.service')
    if fault=='path-phase':next(iter(records['paths'].values()))['phase']='preparing'
    class ReadOnlyAccounts:
        def __init__(self,*args):pass
        def ensure(self,role):
            calls.append(('verify',role))
            if fault=='account':raise RuntimeError('original group changed')
        def reserve(self,path,role,*,create):assert create is False;calls.append(('path',role))
    class ReadOnlyManager:
        def __init__(self,*args):pass
        def owned(self,name):return {'ActiveState':'active' if fault=='active' else 'inactive','ControlGroup':'/owned'}
        def empty(self,group):return fault!='populated'
    monkeypatch.setattr(repair,'Accounts',ReadOnlyAccounts)
    monkeypatch.setattr(repair,'Systemd',ReadOnlyManager)
    monkeypatch.setattr(repair,'protected_file',lambda _:True)
    monkeypatch.setattr(repair.units,'plans',lambda *_args,**_kwargs:{'core':{'unit':'ioalab-core.service'},'official':{'unit':'ioalab-official.service'}})
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda _:object()))
    before=copy.deepcopy(records)
    if fault:
        with pytest.raises(RuntimeError):REAL_STOPPED_OWNERSHIP(layout,records,{'input':{'admin_assistant':{'enabled':False}}})
    else:
        REAL_STOPPED_OWNERSHIP(layout,records,{'input':{'admin_assistant':{'enabled':False}}})
        assert calls==[('verify','core'),('verify','agents'),('path','core')]
    assert records==before


def test_unknown_missing_ready_os_account_is_never_created(subject,monkeypatch):
    import sys
    layout,_,_,_,_=subject
    records={'deploymentId':layout.inspect()['deploymentId'],
             'accounts':{role:{'phase':'ready','name':'ioalab-'+role} for role in ('core','agents')},'paths':{},'units':{}}
    monkeypatch.setattr(repair,'protected_file',lambda _:True)
    def absent(_):raise KeyError('missing account')
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=absent))
    monkeypatch.setattr(repair,'Accounts',lambda *args:SimpleNamespace(ensure=lambda _:pytest.fail('must not call account creation')))
    with pytest.raises(RuntimeError,match='cannot recreate'):REAL_STOPPED_OWNERSHIP(layout,records,{})


def test_actual_installation_run_reuses_bootstrap_and_records_real_completion(subject,monkeypatch):
    from deploy import installation
    layout,parent,_,new,state=subject
    repair.submit(layout,new,parent.operation);child=selected(subject)
    installer=object.__new__(installation.Installation)
    installer.layout,installer.job,installer.request=layout,child,child.request()
    installer.plans={'core':{},'official':{}}
    installer.progress=repair.initial_progress(installer.request,installer.plans)
    installer.progress_path=child.folder/'progress.json'
    installer.metadata={'deploymentId':layout.inspect()['deploymentId']}
    events=[];saved=[]
    installer.directories=lambda:events.append('directories')
    installer.permissions=lambda:events.append('permissions')
    installer.config=SimpleNamespace(base=lambda:events.append('configuration'),public=lambda:{'original':True})
    installer.candidate=lambda:pytest.fail('repair must not build a candidate')
    monkeypatch.setattr(installation.activation,'activate',lambda *_:pytest.fail('repair must not reactivate the set'))
    def bootstrap():
        events.append('bootstrap');installer.metadata['initialized']=True
    installer.bootstrap=bootstrap
    readiness={'runtimeReady':True,'official':{'availableServices':1}}
    def ready():events.append('readiness');return readiness
    installer.readiness=ready
    installer.wait=lambda query:query()
    installer.save_metadata=lambda:saved.append(copy.deepcopy(installer.metadata))
    response=installer.run()
    assert events==['directories','configuration','permissions','bootstrap','readiness']
    assert response['status']=='completed' and response['readiness']==readiness
    assert installer.progress['stage']=='completed' and installer.progress['outcome']=='completed'
    assert saved[-1]['completedSet']==state['candidate']['setId']
    assert saved[-1]['source']==installer.request['source']
    assert saved[-1]['lastReadiness']==readiness and saved[-1]['verifiedAt']


def test_repair_actual_readiness_requires_every_initial_service_available(monkeypatch):
    from deploy import installation
    installer=object.__new__(installation.Installation)
    installer.request={'action':'repair-bootstrap'}
    installer.progress={'requiredRunning':['official'],'candidate':{'release':'/original-release'}}
    installer.plans={'official':{'unit':'ioalab-official.service'}}
    installer.manager=SimpleNamespace(ready=lambda _:{'ActiveState':'active','UnitFileState':'enabled'})
    installer.layout=SimpleNamespace(config=Path('/not-read'))
    monkeypatch.setattr(installation,'load',lambda _:{})
    monkeypatch.setattr(installation,'official_catalog',lambda *_:{'ISP_008':['programming']})
    monkeypatch.setattr(installation,'read_environment',lambda _:{})
    current={'indexReady':True,'templateReady':True,'localExecution':{'executionReady':True},
             'admissionPolicy':{'accepting':True}}
    rows=[{'account':'ISP_008','runtime':{'platformConnected':True,'host':{'available':True}},
           'services':[{'profile':'programming','current':current}]}]
    installer.installed=lambda *_args,**_kwargs:{'accounts':rows}
    assert installer.readiness()['official']['availableServices']==1
    current['admissionPolicy']['accepting']=False
    with pytest.raises(RuntimeError,match='publication/index is not ready'):installer.readiness()
    current['admissionPolicy']['accepting']=True
    current['indexReady']=False
    with pytest.raises(RuntimeError,match='publication/index is not ready'):installer.readiness()
    current['indexReady']=True
    rows[0]['services'].clear()
    with pytest.raises(RuntimeError,match='status is incomplete'):installer.readiness()


def test_explicit_cli_uses_original_state_and_parent_only(monkeypatch,capsys):
    from deploy import __main__ as entry
    calls=[];owned=object();parent=uuid.uuid4().hex
    monkeypatch.setattr(entry,'require_host',lambda:None)
    monkeypatch.setattr(entry,'existing',lambda path:owned)
    def submit(layout,source,operation):
        calls.append((layout,source,operation));return {'status':'waiting','operationId':'same-repair'}
    monkeypatch.setattr(repair,'submit',submit)
    assert entry.main(['repair-bootstrap','--state','original-state','--operation',parent])==0
    assert calls==[(owned,Path(entry.__file__).resolve().parent.parent,parent)]
    assert json.loads(capsys.readouterr().out)['operationId']=='same-repair'


@pytest.mark.parametrize('option',['--root','--config-dir','--config','--force'])
def test_repair_cli_has_no_replacement_root_input_or_force(monkeypatch,option):
    from deploy import __main__ as entry
    monkeypatch.setattr(entry,'require_host',lambda:pytest.fail('invalid CLI must not reach host operations'))
    with pytest.raises(SystemExit) as caught:
        entry.main(['repair-bootstrap','--state','original-state','--operation',uuid.uuid4().hex,option,'replacement'])
    assert caught.value.code==2
