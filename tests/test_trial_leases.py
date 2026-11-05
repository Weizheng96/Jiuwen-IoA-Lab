"""Control invariants; Linux container and real native acceptance remain separate."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import threading

import pytest

from ioalab_trial.coordinator import Coordinator, Denied
from ioalab_trial.store import Store


@pytest.fixture
def trial(tmp_path):
    now = [1000.0]
    store = Store(tmp_path/'control.sqlite',deployment_profile='linux-server')
    coordinator = Coordinator(store,audience='https://trial.test',admission_key='k'*40,
                              lease_seconds=100,grace_seconds=20,clock=lambda:now[0])
    yield coordinator,now
    store.close()


def ready(coordinator):
    with coordinator.store.transaction() as db:
        for seat in range(1,4):
            db.execute("UPDATE seats SET state='ready',epoch=? WHERE id=?", (str(seat)*32,seat))


def test_runtime_failure_revokes_then_quarantines_without_wiping(trial):
    from types import SimpleNamespace
    coordinator,now=trial;ready(coordinator)
    token,_=coordinator.consume(ticket(coordinator));lease=coordinator.enqueue('alice')
    revoked=[];coordinator.listeners.add(revoked.append)
    def health(seat,epoch):
        if seat==lease['id']:raise RuntimeError('actual runtime exited')
    sandbox=SimpleNamespace(runtime_healthy=health,stop=lambda *a:pytest.fail('Unverified runtime must be preserved'),
        wipe=lambda *a:pytest.fail('Unverified runtime must not be destroyed'))
    coordinator.reconcile(sandbox)
    assert coordinator.status('alice')['state']=='quarantined' and revoked==[lease['id']]
    with pytest.raises(Denied):coordinator.session(token)
    assert coordinator.status('alice')['epoch']==lease['epoch']
    assert coordinator.store.seats()[1]['state']=='ready'


def test_runtime_liveness_is_throttled_and_not_based_on_initial_cleanliness(trial):
    from types import SimpleNamespace
    from ioalab_trial.probe import runtime_services_ready
    coordinator,now=trial;ready(coordinator);calls=[]
    sandbox=SimpleNamespace(runtime_healthy=lambda seat,epoch:calls.append(seat))
    coordinator.reconcile(sandbox);coordinator.reconcile(sandbox)
    assert calls==[1,2,3]
    now[0]+=15;coordinator.reconcile(sandbox)
    assert calls==[1,2,3,1,2,3]
    busy={'accounts':[{'runtime':{'platformConnected':True,'ipcReady':True,'host':{'available':True}},
        'services':[{'current':{'localExecution':{'executionReady':False}}}]}]}
    assert runtime_services_ready(busy)


def test_deployment_shutdown_stops_without_wiping_and_fences_admission(trial):
    from types import SimpleNamespace
    coordinator,_=trial;ready(coordinator);calls=[]
    coordinator.enqueue('alice')
    sandbox=SimpleNamespace(stop=lambda seat,epoch:calls.append((seat,epoch)),
                            wipe=lambda *a:pytest.fail('Deployment shutdown must retain volumes'))
    coordinator.shutdown(sandbox)
    assert len(calls)==3 and coordinator.shutdown_verified
    assert all(row['state']=='revoking' and row['epoch'] for row in coordinator.store.seats())
    with pytest.raises(Denied):coordinator.enqueue('bob')
    coordinator.reconcile(sandbox)
    assert len(calls)==3


def test_deployment_shutdown_failure_keeps_epoch_quarantined(trial):
    from types import SimpleNamespace
    coordinator,_=trial;ready(coordinator)
    def failure(*args):raise RuntimeError('unverified resources')
    with pytest.raises(RuntimeError,match='unverified'):
        coordinator.shutdown(SimpleNamespace(stop=failure))
    assert not coordinator.shutdown_verified
    assert all(row['state']=='quarantined' and row['epoch'] for row in coordinator.store.seats())


def ticket(coordinator, **updates):
    value = {'v':1,'owner':'alice','aud':'https://trial.test','iat':1000,'exp':1060,'jti':'one'}
    value.update(updates)
    payload = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return payload+'.'+hmac.new(coordinator.key.encode(),payload.encode(),hashlib.sha256).hexdigest()


def test_admission_consumed_once_and_bound(trial):
    coordinator,now = trial
    token,csrf = coordinator.consume(ticket(coordinator))
    assert coordinator.session(token)['owner'] == 'alice'
    assert coordinator.session(token)['csrf'] == csrf
    with pytest.raises(Denied):
        coordinator.consume(ticket(coordinator))
    for updates in ({'aud':'https://other.test'},{'iat':1001},{'exp':1000},{'exp':1061}):
        with pytest.raises(Denied):
            coordinator.consume(ticket(coordinator,**updates))


def test_three_occupants_one_queue_atomic_and_duplicate_join(trial):
    coordinator,_ = trial
    ready(coordinator)
    threads = [threading.Thread(target=coordinator.enqueue,args=(owner,)) for owner in ('a','b','c','d')]
    for thread in threads:thread.start()
    for thread in threads:thread.join()
    rows = coordinator.store.seats()
    assert len({row['owner'] for row in rows}) == 3
    queued = [owner for owner in ('a','b','c','d') if coordinator.status(owner)['state']=='queued']
    assert len(queued) == 1
    assert coordinator.enqueue(queued[0])['position'] == 1
    coordinator.enqueue(rows[0]['owner'])
    assert len(coordinator.store.seats()) == 3


def test_reconnect_retains_deadline_then_old_epoch_denied(trial):
    coordinator,now = trial
    ready(coordinator)
    original = coordinator.enqueue('a')
    now[0] += 11
    coordinator.tick()
    assert coordinator.status('a')['state'] == 'reconnect_grace'
    coordinator.access('a',original['epoch'],touch=True)
    assert coordinator.status('a')['expires'] == original['expires']
    with pytest.raises(Denied):coordinator.access('other',original['epoch'])
    with pytest.raises(Denied):coordinator.access('a','x'*32)
    now[0] += 21
    coordinator.tick()
    with pytest.raises(Denied):coordinator.access('a',original['epoch'])


def test_revoke_before_stop_and_wipe_failure_quarantines(trial):
    coordinator,_ = trial
    ready(coordinator)
    lease = coordinator.enqueue('a')
    coordinator.end('a')
    calls = []
    class Sandbox:
        def stop(self,seat,epoch):
            calls.append('stop')
            with pytest.raises(Denied):coordinator.access('a',epoch)
        def wipe(self,seat,epoch):
            calls.append('wipe')
            raise OSError('busy')
        def prepare(self,*args):pytest.fail('Failed reset must never prepare')
    coordinator.reconcile(Sandbox())
    assert calls == ['stop','wipe']
    assert coordinator.status('a')['state'] == 'quarantined'
    assert coordinator.status('a')['epoch'] == lease['epoch']


def test_reset_prepares_new_epoch_only_after_wipe(trial):
    coordinator,_ = trial
    ready(coordinator)
    old = coordinator.enqueue('a')
    coordinator.enqueue('b');coordinator.enqueue('c');coordinator.enqueue('d')
    coordinator.end('a')
    calls=[]
    class Sandbox:
        def stop(self,seat,epoch):calls.append(('stop',epoch))
        def wipe(self,seat,epoch):calls.append(('wipe',epoch))
        def prepare(self,seat,epoch):calls.append(('prepare',epoch))
        def healthy(self,seat,epoch):calls.append(('healthy',epoch))
    coordinator.reconcile(Sandbox())
    assert [name for name,_ in calls] == ['stop','wipe','prepare','healthy']
    current = coordinator.status('d')
    assert current['state'] == 'leased' and current['epoch'] != old['epoch']
    assert calls[-1][1] == current['epoch']
    with pytest.raises(Denied):coordinator.access('a',old['epoch'])


def test_old_cookie_cannot_reenter_same_owner_new_epoch(trial):
    coordinator,_ = trial
    ready(coordinator)
    token,_ = coordinator.consume(ticket(coordinator))
    coordinator.enqueue('alice')
    coordinator.end('alice')
    with pytest.raises(Denied):coordinator.session(token)
    with coordinator.store.transaction() as db:
        db.execute("UPDATE seats SET owner=NULL,state='ready',epoch=? WHERE id=1", ('f'*32,))
    coordinator.enqueue('alice')
    with pytest.raises(Denied):coordinator.session(token)
    fresh,_ = coordinator.consume(ticket(coordinator,jti='fresh'))
    assert coordinator.session(fresh)['owner'] == 'alice'


def test_model_arguments_keep_original_id_and_reject_secrets(tmp_path):
    from ioalab_trial.model_bridge import arguments
    path=tmp_path/'input.json'
    path.write_text('{"budget":10}',encoding='utf-8')
    first,operation=arguments(path,root=tmp_path)
    assert first == {'budget':10}
    assert arguments(path,root=tmp_path) == (first,operation)
    secret=tmp_path/'model-bridge.json'
    secret.write_text('{}')
    with pytest.raises(ValueError):arguments(secret,root=tmp_path)
    with pytest.raises(ValueError):arguments(path,root=tmp_path/'other')
