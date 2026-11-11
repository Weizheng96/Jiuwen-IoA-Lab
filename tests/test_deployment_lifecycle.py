from contextlib import contextmanager
from types import SimpleNamespace
import pytest

from deploy import lifecycle,units


@pytest.fixture
def subject(tmp_path):
    private=tmp_path/'private';private.mkdir()
    obj=object.__new__(lifecycle.Lifecycle)
    obj.owner='a'*32;obj.layout=SimpleNamespace(private=private)
    obj.plans={key:{'unit':key} for key in ('trial','official','assistant','swarm-web','swarm-gateway','swarm-agent','core')}
    obj.metadata={'units':{key:{} for key in obj.plans},'initialized':True}
    obj.path=private/'lifecycle.json';calls=[];live={key:'active' for key in obj.plans}
    class Manager:
        def ready(self,name):return {'ActiveState':live[name]}
        def freeze(self,names):return {name:{'InvocationID':name} for name in names}
        def request_stop(self,name,frozen):calls.append(name);live[name]='inactive'
        def released(self,name,frozen):return live[name]=='inactive'
    obj.manager=Manager()
    @contextmanager
    def leases():
        assert all(value=='inactive' for value in live.values());calls.append('actual-locks');yield
    obj.leases=leases
    return obj,calls,live


def test_stop_drains_trial_and_participants_before_hosts_and_core(subject):
    obj,calls,live=subject
    assert obj.stop(0)['executionLocksReleased'] is True
    assert calls==['trial','official','assistant','swarm-web','swarm-gateway','swarm-agent','core','actual-locks']
    assert obj.stop(0)['phase']=='stopped'


def test_pending_trial_does_not_stop_remaining_components(subject):
    obj,calls,live=subject
    obj.manager.request_stop=lambda name,frozen:calls.append(name)
    result=obj.stop(0)
    assert result['status']=='waiting' and calls==['trial']
    assert live['core']=='active'
    from deploy.release import read_json
    assert read_json(obj.path)['group']==0


def test_lock_unknown_cannot_report_stopped(subject):
    obj,calls,live=subject
    def unknown():raise RuntimeError('original writer remains')
    obj.leases=unknown
    with pytest.raises(RuntimeError,match='original writer'):obj.stop(0)
    from deploy.release import read_json
    assert read_json(obj.path)['phase']=='stopping'


def test_status_rechecks_stopped_groups_and_locks(subject):
    obj,calls,live=subject
    obj.stop(0)
    assert obj.status()['phase']=='stopped'
    live['core']='deactivating'
    assert obj.status()['phase']=='unknown'
    live['core']='inactive'
    def unavailable():raise RuntimeError('lock remains')
    obj.leases=unavailable
    assert obj.status()['phase']=='unknown'


def test_status_with_incomplete_candidate_progress_does_not_hide_the_original_task(subject):
    import json
    obj,_,_=subject
    progress=obj.layout.private/'candidate-progress.json'
    progress.write_text(json.dumps({'stage':'candidate'}))
    def readiness():return json.loads(progress.read_text())['candidate']['release']
    obj.readiness=readiness
    result=obj.status()
    assert result['phase']=='starting' and result['runtimeReadinessVerified'] is False
    assert all(row['ActiveState']=='active' for row in result['components'].values())


def test_start_waits_for_original_core_and_resumes_without_relogin(subject,monkeypatch):
    obj,calls,live=subject
    for name in live:live[name]='inactive'
    ids={name:'old-'+name for name in live}
    obj.manager.ready=lambda name:{'ActiveState':live[name],'InvocationID':ids[name]}
    obj.manager.freeze=lambda names:{name:obj.manager.ready(name) for name in names}
    def start(name):calls.append('start-'+name);live[name]='active';ids[name]='new-'+name
    obj.manager.start=start
    monkeypatch.setattr(lifecycle.activation,'current',lambda layout:{'setId':'fixed'})
    healthy=[False]
    def core_ready():
        if not healthy[0]:raise RuntimeError('HTTP not ready')
    obj.installer=lambda:SimpleNamespace(core_ready=core_ready)
    assert obj.start(0)['status']=='waiting'
    assert calls==['actual-locks','start-core']
    healthy[0]=True
    obj.start(0)
    assert calls.count('start-core')==1 and calls.count('start-official')==1
    ids['core']='replacement'
    with pytest.raises(RuntimeError,match='changed'):obj.start(0)


def test_start_waits_for_new_invocation_instead_of_stopped_unit_id(subject,monkeypatch):
    obj,calls,live=subject
    obj.plans={'core':obj.plans['core']};obj.metadata['units']={'core':{}}
    for name in live:live[name]='inactive'
    ids={'core':'old-core'};pending=[]
    obj.manager.ready=lambda name:{'ActiveState':live[name],'InvocationID':ids[name]}
    obj.manager.freeze=lambda names:{name:obj.manager.ready(name) for name in names}
    obj.manager.start=lambda name:pending.append(name)
    def activated(delay):
        calls.append('await-new-invocation');live['core']='active';ids['core']='new-core'
    monkeypatch.setattr(lifecycle.time,'sleep',activated)
    monkeypatch.setattr(lifecycle.activation,'current',lambda layout:{'setId':'fixed'})
    obj.installer=lambda:SimpleNamespace(core_ready=lambda:None)
    obj.start(1)
    from deploy.release import read_json
    assert pending==['core'] and 'await-new-invocation' in calls
    assert read_json(obj.path)['started']=={'core':'new-core'}


def test_units_stop_on_core_loss_without_automatic_restart():
    from pathlib import PurePosixPath
    layout=SimpleNamespace(root=PurePosixPath('/opt/r'),environments=PurePosixPath('/opt/r/envs'),
        state=PurePosixPath('/var/lib/r'),config=PurePosixPath('/etc/r'),core_user='core',agents_user='agents')
    plans=units.plans(layout,administrator=True,core={'bind':'127.0.0.1','port':8750,'public_url':'https://ioa.example'},trial={'config':'/etc/trial.json','controller_python':'/opt/trial/bin/python'})
    content=units.render(layout,'a'*32,plans)
    for key,row in plans.items():
        assert 'Restart=no' in content[row['unit']]
        if key!='core':assert 'BindsTo=ioalab-core.service' in content[row['unit']]
    assert plans['trial']['user']=='root'
