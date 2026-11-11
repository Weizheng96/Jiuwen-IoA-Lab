"""Capacity is real, durable and only available on an actual Linux server."""
import sqlite3
import stat
from types import SimpleNamespace

import pytest
from ioalab_trial.profile import validate_environment
from ioalab_trial.store import Store
from ioalab_trial.coordinator import Coordinator,Denied
from ioalab_trial.sandbox import DockerSandbox,check_secret_permissions


@pytest.mark.parametrize('profile,system,release,ok',[
    ('linux-server','linux','6.8.0-generic',True),
    ('linux-server','linux','6.6-microsoft-standard-WSL2',False),('linux-server','linux','4.4-Microsoft',False),
    ('windows-wsl','linux','6.6-microsoft-standard-WSL2',False),('windows-wsl','linux','6.8-generic',False),
    ('windows-wsl','win32','10',False),('linux-server','win32','10',False),(None,'linux','6',False)])
def test_profile_matches_actual_environment(profile,system,release,ok):
    if ok:validate_environment(profile,system=system,release=release)
    else:
        with pytest.raises(ValueError):validate_environment(profile,system=system,release=release)


def old_single_seat_database(path):
    """An original windows-wsl control library holding a live epoch reference."""
    db=sqlite3.connect(path)
    db.executescript("""CREATE TABLE deployment(profile TEXT PRIMARY KEY,capacity INTEGER NOT NULL,resource_layout TEXT NOT NULL);
        INSERT INTO deployment VALUES('windows-wsl',1,'epoch-ingress/1');
        CREATE TABLE seats(id INTEGER PRIMARY KEY,state TEXT NOT NULL,epoch TEXT,owner TEXT UNIQUE,expires REAL,seen REAL,reason TEXT);
        INSERT INTO seats(id,state,owner,epoch) VALUES(1,'leased','old-user','old-contract-epoch');""")
    db.close()


def test_queue_reset_and_old_confirmation_session_cannot_follow_epoch(tmp_path):
    from test_trial_leases import ticket
    now=[1000.0];store=Store(tmp_path/'control.sqlite',deployment_profile='linux-server')
    controller=Coordinator(store,audience='https://trial.test',confirm_audience='https://confirm.test',
        admission_key='k'*40,lease_seconds=100,grace_seconds=20,clock=lambda:now[0])
    try:
        assert len(store.seats())==3
        with store.transaction() as db:
            for seat in (1,2,3):db.execute("UPDATE seats SET state='ready',epoch=? WHERE id=?",(str(seat)*32,seat))
        native,_=controller.consume(ticket(controller,owner='a'))
        assert [controller.enqueue(owner)['id'] for owner in 'acd']==[1,2,3] and controller.enqueue('b')['position']==1
        confirmed,_=controller.consume(ticket(controller,owner='a',aud='https://confirm.test',jti='confirmation'),audience='https://confirm.test')
        assert controller.status('b')['capacity']==3
        calls=[]
        class Lifecycle:
            def stop(self,*args):calls.append('stop')
            def wipe(self,*args):calls.append('wipe')
            def prepare(self,*args):calls.append('prepare')
            def healthy(self,*args):calls.append('healthy')
            def runtime_healthy(self,*args):pass
        controller.end('a');controller.reconcile(Lifecycle())
        assert calls==['stop','wipe','prepare','healthy'] and controller.status('b')['id']==1
        for token in (native,confirmed):
            with pytest.raises(Denied):controller.session(token)
        with pytest.raises(Denied):controller.access('a','1'*32)
    finally:store.close()


@pytest.mark.parametrize('old_profile',[None,'windows-wsl'])
def test_old_control_database_rejected_without_changing_any_bytes(tmp_path,old_profile):
    path=tmp_path/'control.sqlite'
    if old_profile:old_single_seat_database(path)
    else:
        db=sqlite3.connect(path);db.execute('CREATE TABLE seats(id INTEGER PRIMARY KEY,epoch TEXT)')
        db.execute("INSERT INTO seats VALUES(2,'old-contract-epoch')");db.commit();db.close()
    before=path.read_bytes()
    with pytest.raises(ValueError,match='preserve'):Store(path,deployment_profile='linux-server')
    assert path.read_bytes()==before


def test_windows_wsl_store_rejected_without_creating_control_database(tmp_path):
    path=tmp_path/'control.sqlite'
    with pytest.raises(ValueError,match='linux-server'):Store(path,deployment_profile='windows-wsl')
    assert not path.exists()


def test_windows_wsl_configuration_rejected_before_control_directory_exists(tmp_path,monkeypatch):
    import json
    import sys
    from ioalab_trial import __main__ as entry
    root=tmp_path/'control'
    config=tmp_path/'trial.json';config.write_text(json.dumps({'deployment_profile':'windows-wsl',
        'origin':'https://trial.test','control_directory':str(root)}))
    monkeypatch.setattr(sys,'argv',['ioalab-trial','--config',str(config)])
    class Forbidden:
        def __init__(self,*args,**kwargs):raise AssertionError('must not construct a sandbox')
    monkeypatch.setattr('ioalab_trial.sandbox.DockerSandbox',Forbidden)
    with pytest.raises(ValueError,match='linux-server'):entry.main()
    assert not root.exists()


def test_windows_wsl_launcher_argument_is_not_accepted(tmp_path,monkeypatch):
    import sys
    from ioalab_trial import __main__ as entry
    config=tmp_path/'trial.json';config.write_text('{}')
    monkeypatch.setattr(sys,'argv',['ioalab-trial','--config',str(config),'--deployment-profile','windows-wsl'])
    with pytest.raises(SystemExit):entry.main()


def test_entry_rejects_old_capacity_before_constructing_any_sandbox(tmp_path,monkeypatch):
    import json
    import sys
    from ioalab_trial import __main__ as entry
    root=tmp_path/'control';root.mkdir()
    old_single_seat_database(root/'control.sqlite')
    before=(root/'control.sqlite').read_bytes()
    config=tmp_path/'trial.json';config.write_text(json.dumps({'deployment_profile':'linux-server',
        'origin':'https://trial.test','control_directory':str(root)}))
    monkeypatch.setattr(sys,'argv',['ioalab-trial','--config',str(config)])
    monkeypatch.setattr('ioalab_trial.profile.validate_environment',lambda profile:None)
    class Forbidden:
        def __init__(self,*args,**kwargs):raise AssertionError('must not create resources or expose gateway')
    monkeypatch.setattr('ioalab_trial.sandbox.DockerSandbox',Forbidden)
    # Use a lock double only on Windows; the control-store rejection is real SQLite.
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(flock=lambda *a:None,LOCK_EX=1,LOCK_NB=2))
    with pytest.raises(ValueError,match='preserve'):entry.main()
    assert (root/'control.sqlite').read_bytes()==before


def test_old_or_mismatched_layout_preserves_database_and_business_references(tmp_path):
    for old in ('missing','wrong'):
        path=tmp_path/(old+'.sqlite')
        if old=='missing':
            db=sqlite3.connect(path)
            db.executescript("CREATE TABLE deployment(profile TEXT,capacity INTEGER); INSERT INTO deployment VALUES('linux-server',3); CREATE TABLE seats(id INTEGER,epoch TEXT); INSERT INTO seats VALUES(1,'old-contract-payment-reference');")
            db.close()
        else:
            original=Store(path,deployment_profile='linux-server')
            with original.transaction() as db:
                db.execute("UPDATE deployment SET resource_layout='two-container-old'")
                db.execute("UPDATE seats SET epoch='old-contract-payment-reference'")
            original.close()
        before=path.read_bytes()
        with pytest.raises(ValueError,match='preserve'):Store(path,deployment_profile='linux-server')
        assert path.read_bytes()==before


@pytest.mark.parametrize('seat',[0,4,True])
@pytest.mark.parametrize('method',['identity','ui','prepare'])
def test_invalid_seat_rejected_before_any_external_operation(monkeypatch,seat,method):
    from test_trial_sandbox import configuration,EPOCH
    sandbox=DockerSandbox(configuration(),deployment_profile='linux-server')
    sandbox.run=lambda *a,**kw:pytest.fail('Invalid seat reached a Docker command')
    monkeypatch.setattr('ioalab_trial.sandbox.httpx.post',lambda *a,**kw:pytest.fail('Invalid seat reached HTTP'))
    with pytest.raises(ValueError,match='ownership'):
        getattr(sandbox,method)(seat,EPOCH)
    assert sandbox.image_validated is False


@pytest.mark.parametrize('uid,gid,mode,ok',[(0,0,0o600,True),(1000,0,0o600,False),(0,1000,0o600,False),(0,0,0o644,False)])
def test_secret_permissions_are_not_relaxed(uid,gid,mode,ok):
    path=SimpleNamespace(stat=lambda:SimpleNamespace(st_uid=uid,st_gid=gid,st_mode=stat.S_IFREG|mode))
    if ok:check_secret_permissions(path)
    else:
        with pytest.raises(ValueError):check_secret_permissions(path)


def lock_double(monkeypatch,*,held=False):
    """fcntl is POSIX-only; the double reproduces only LOCK_NB contention."""
    import sys
    def flock(handle,flags):
        if held:raise BlockingIOError('coordinator.lock is held')
    monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(flock=flock,LOCK_EX=1,LOCK_NB=2))


def stopped_deployment(tmp_path,monkeypatch,*,running=False):
    """Real control store and real DockerSandbox checks against the Docker double of test_trial_sandbox."""
    import json
    from test_trial_sandbox import Docker,EPOCH,configuration
    root=tmp_path/'control';root.mkdir()
    store=Store(root/'control.sqlite',deployment_profile='linux-server')
    with store.transaction() as db:db.execute("UPDATE seats SET state='revoking',epoch=? WHERE id=1",(EPOCH,))
    store.close()
    (root/'coordinator.lock').write_bytes(b'')
    secret=tmp_path/'provider.json';secret.write_text('{"api_key":"private-provider-key"}')
    sandbox_config={**configuration(),'proxy_secret_file':str(secret)}
    config=tmp_path/'trial.json'
    config.write_text(json.dumps({'deployment_profile':'linux-server','origin':'https://trial.test',
        'control_directory':str(root),'sandbox':sandbox_config}))
    monkeypatch.setattr('ioalab_trial.sandbox.check_secret_permissions',lambda path:None)
    doubles=[]
    def sandbox(*args,**kwargs):
        value=DockerSandbox(*args,**kwargs);docker=Docker(value);docker.stopped=not running
        value.run=docker;doubles.append(docker);return value
    monkeypatch.setattr('ioalab_trial.stopped.DockerSandbox',sandbox)
    monkeypatch.setattr('ioalab_trial.profile.validate_environment',lambda profile:None)
    return root,config,doubles


def verify_stopped(monkeypatch,config):
    import sys
    from ioalab_trial import __main__ as entry
    monkeypatch.setattr(sys,'argv',['ioalab-trial','--config',str(config),'--verify-stopped'])
    entry.main()


def test_verify_stopped_reports_true_only_after_real_resource_checks(tmp_path,monkeypatch,capsys):
    import json
    root,config,doubles=stopped_deployment(tmp_path,monkeypatch)
    lock_double(monkeypatch)
    before=(root/'control.sqlite').read_bytes()
    verify_stopped(monkeypatch,config)
    assert json.loads(capsys.readouterr().out)=={'allEpochResourcesStopped':True,'capacity':3}
    inspected={call[2] for call in doubles[0].calls if call[1]=='inspect' and call[0]=='container'}
    assert {name.rsplit('-',1)[-1] for name in inspected}>={'model','ingress'} and len(inspected)==3
    assert not any(call[0] in ('stop','kill','rm','wait') for call in doubles[0].calls)
    assert (root/'control.sqlite').read_bytes()==before


@pytest.mark.parametrize('change',['layout','capacity','running','locked'])
def test_verify_stopped_fails_without_changing_control_store(tmp_path,monkeypatch,capsys,change):
    root,config,doubles=stopped_deployment(tmp_path,monkeypatch,running=change=='running')
    lock_double(monkeypatch,held=change=='locked')
    path=root/'control.sqlite'
    if change=='layout':
        db=sqlite3.connect(path);db.execute("UPDATE deployment SET resource_layout='two-container-old'");db.commit();db.close()
    elif change=='capacity':
        db=sqlite3.connect(path);db.execute('DELETE FROM seats WHERE id=3');db.commit();db.close()
    before=path.read_bytes()
    expected,message={'layout':(ValueError,'layout changed'),'capacity':(ValueError,'capacity changed'),
        'running':(RuntimeError,'unreleased'),'locked':(BlockingIOError,'held')}[change]
    with pytest.raises(expected,match=message):verify_stopped(monkeypatch,config)
    assert capsys.readouterr().out=='' and path.read_bytes()==before
    assert not any(call[0] in ('stop','kill','rm','wait') for docker in doubles for call in docker.calls)


@pytest.mark.parametrize('stopped',[True,False])
def test_shutdown_receipt_records_only_verified_stop(tmp_path,monkeypatch,stopped):
    import hashlib
    import json
    import sys
    import uvicorn
    from ioalab_trial import __main__ as entry
    root=tmp_path/'control';root.mkdir()
    store=Store(root/'control.sqlite',deployment_profile='linux-server')
    with store.transaction() as db:db.execute("UPDATE seats SET state='leased',owner='alice',epoch=? WHERE id=2",('2'*32,))
    store.close()
    (root/'shutdown.json').write_text('{"allEpochResourcesStopped":true}')
    config=tmp_path/'trial.json'
    config.write_text(json.dumps({'deployment_profile':'linux-server','origin':'https://trial.test',
        'confirm_origin':'https://confirm.trial.test','formal_origin':'https://ioa.test','control_directory':str(root),
        'admission_key':'k'*40,'lease_seconds':100,'grace_seconds':20,'sandbox':{}}))
    lock_double(monkeypatch)
    monkeypatch.setattr('ioalab_trial.profile.validate_environment',lambda profile:None)
    stops=[]
    class Sandbox:
        def __init__(self,config,*,deployment_profile):assert deployment_profile=='linux-server'
        def validate_image(self):pass
        def stop(self,seat,epoch):
            stops.append((seat,epoch))
            if not stopped:raise RuntimeError('seat did not exit')
    monkeypatch.setattr('ioalab_trial.sandbox.DockerSandbox',Sandbox)
    built={}
    def create_app(coordinator,sandbox,**kwargs):
        built.update(coordinator=coordinator,sandbox=sandbox);return object()
    monkeypatch.setattr('ioalab_trial.gateway.create_app',create_app)
    class Server:
        def __init__(self,config):pass
        def run(self):
            assert not (root/'shutdown.json').exists()
            built['coordinator'].shutdown(built['sandbox'])  # what the gateway lifespan runs on exit
    monkeypatch.setattr(uvicorn,'Server',Server)
    monkeypatch.setattr(sys,'argv',['ioalab-trial','--config',str(config)])
    if stopped:entry.main()
    else:
        with pytest.raises(RuntimeError,match='unverified'):entry.main()
    receipt=json.loads((root/'shutdown.json').read_text())
    assert receipt=={'configSha256':hashlib.sha256(config.read_bytes()).hexdigest(),'allEpochResourcesStopped':stopped}
    assert stops==[(2,'2'*32)]
    reopened=Store(root/'control.sqlite',deployment_profile='linux-server')
    try:
        seat=reopened.seats()[1]
        assert seat['epoch']=='2'*32 and seat['state']==('revoking' if stopped else 'quarantined')
    finally:reopened.close()
