"""Capacity is real, durable and independent of WSL reporting itself as Linux."""
import importlib.util
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace

import pytest
from ioalab_trial.profile import validate_environment
from ioalab_trial.store import Store
from ioalab_trial.coordinator import Coordinator,Denied
from ioalab_trial.sandbox import DockerSandbox,check_secret_permissions


@pytest.mark.parametrize('profile,system,release,ok',[
    ('linux-server','linux','6.8.0-generic',True),('windows-wsl','linux','6.6-microsoft-standard-WSL2',True),
    ('linux-server','linux','6.6-microsoft-standard-WSL2',False),('windows-wsl','linux','6.8-generic',False),
    ('windows-wsl','win32','10',False),('linux-server','win32','10',False),
    ('windows-wsl','linux','4.4-Microsoft',False),(None,'linux','6',False)])
def test_profile_matches_actual_environment(profile,system,release,ok):
    if ok:validate_environment(profile,system=system,release=release)
    else:
        with pytest.raises(ValueError):validate_environment(profile,system=system,release=release)


def test_single_seat_queue_reset_and_old_confirmation_session_cannot_follow_epoch(tmp_path):
    from test_trial_leases import ticket
    now=[1000.0];store=Store(tmp_path/'control.sqlite',deployment_profile='windows-wsl')
    controller=Coordinator(store,audience='https://trial.test',confirm_audience='https://confirm.test',
        admission_key='k'*40,lease_seconds=100,grace_seconds=20,clock=lambda:now[0])
    try:
        assert len(store.seats())==1
        with store.transaction() as db:db.execute("UPDATE seats SET state='ready',epoch=?",('a'*32,))
        native,_=controller.consume(ticket(controller,owner='a'))
        assert controller.enqueue('a')['id']==1 and controller.enqueue('b')['position']==1
        confirmed,_=controller.consume(ticket(controller,owner='a',aud='https://confirm.test',jti='confirmation'),audience='https://confirm.test')
        assert controller.status('b')['capacity']==1
        calls=[]
        class Lifecycle:
            def stop(self,*args):calls.append('stop')
            def wipe(self,*args):calls.append('wipe')
            def prepare(self,*args):calls.append('prepare')
            def healthy(self,*args):calls.append('healthy')
        controller.end('a');controller.reconcile(Lifecycle())
        assert calls==['stop','wipe','prepare','healthy'] and controller.status('b')['id']==1
        for token in (native,confirmed):
            with pytest.raises(Denied):controller.session(token)
        with pytest.raises(Denied):controller.access('a','a'*32)
    finally:store.close()


@pytest.mark.parametrize('old_profile',[None,'linux-server'])
def test_old_control_database_rejected_without_changing_any_bytes(tmp_path,old_profile):
    path=tmp_path/'control.sqlite'
    if old_profile:
        store=Store(path,deployment_profile=old_profile)
        with store.transaction() as db:db.execute("UPDATE seats SET state='leased',owner='old-user',epoch='old-contract-epoch' WHERE id=2")
        store.close()
    else:
        db=sqlite3.connect(path);db.execute('CREATE TABLE seats(id INTEGER PRIMARY KEY,epoch TEXT)')
        db.execute("INSERT INTO seats VALUES(2,'old-contract-epoch')");db.commit();db.close()
    before=path.read_bytes()
    with pytest.raises(ValueError,match='preserve'):Store(path,deployment_profile='windows-wsl')
    assert path.read_bytes()==before


def test_entry_rejects_old_capacity_before_constructing_any_sandbox(tmp_path,monkeypatch):
    import json
    import sys
    from ioalab_trial import __main__ as entry
    root=tmp_path/'control';root.mkdir()
    old=Store(root/'control.sqlite',deployment_profile='linux-server');old.close()
    before=(root/'control.sqlite').read_bytes()
    config=tmp_path/'trial.json';config.write_text(json.dumps({'deployment_profile':'windows-wsl',
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


def test_windows_sandbox_cannot_target_other_seats():
    from test_trial_sandbox import configuration,EPOCH
    sandbox=DockerSandbox(configuration(),deployment_profile='windows-wsl')
    for seat in (2,3,True):
        with pytest.raises(ValueError):sandbox.identity(seat,EPOCH)
        with pytest.raises(ValueError):sandbox.ui(seat)


@pytest.mark.parametrize('uid,gid,mode,ok',[(0,0,0o600,True),(1000,0,0o600,False),(0,1000,0o600,False),(0,0,0o644,False)])
def test_secret_permissions_are_not_relaxed(uid,gid,mode,ok):
    path=SimpleNamespace(stat=lambda:SimpleNamespace(st_uid=uid,st_gid=gid,st_mode=stat.S_IFREG|mode))
    if ok:check_secret_permissions(path)
    else:
        with pytest.raises(ValueError):check_secret_permissions(path)


def launcher():
    spec=importlib.util.spec_from_file_location('windows_trial_launcher',Path(__file__).parents[1]/'deploy/trial/start_windows.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_windows_launcher_passes_only_explicit_wsl_arguments_and_propagates_failure(monkeypatch):
    module=launcher();calls=[]
    monkeypatch.setattr(module.sys,'platform','win32')
    def run(argv,**kwargs):calls.append(argv);return SimpleNamespace(returncode=17)
    monkeypatch.setattr(module.subprocess,'run',run)
    args=['--distribution','IoA-Trial','--python','/opt/controller/bin/python','--config','/etc/trial/config.json']
    assert module.main(args)==17
    assert calls[0][-2:]==['--deployment-profile','windows-wsl'] and calls[0][0]=='wsl.exe'
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0))
    assert module.main(args)==0
    def missing(*a,**kw):raise FileNotFoundError('wsl.exe')
    monkeypatch.setattr(module.subprocess,'run',missing)
    assert module.main(args)==1
    with pytest.raises(ValueError):module.command('IoA-Trial','C:/python.exe','/etc/trial.json')
