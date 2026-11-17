"""P35 AdmissionPolicy guards: real RuntimeStore, boundary-only platform policy ledger.

The ledger double reproduces the platform admission semantics the client relies on:
revisions increase by one, expectedRevision conflicts reject, writes are idempotent by
wire operation ID and operation.get returns the original result. The whole-chain cases in
test_provider_admission stay the end-to-end evidence (chain layer).
"""
import copy
from types import SimpleNamespace

import pytest

from ioalab_sdk.provider.admission import AdmissionPolicy
from ioalab_sdk.runtime import RuntimeFault,RuntimeStore


class Ledger:
    """Platform boundary: admission.status/configure and operation.get over the local outbox."""
    def __init__(self,store):
        self.store=store;self.connected=True
        self.policy={'revision':0,'maxActiveDelegations':0,'accepting':False}
        self.results={};self.calls=[];self.writes=[]
        self.lose=set();self.forget=False;self.before_configure=None

    def view(self,actor):return {'service':actor,**self.policy,'active':0}

    def write(self,actor,payload):
        if payload['expectedRevision']!=self.policy['revision']:
            return None,{'code':'revision_conflict','message':'Read the current admission policy first','recovery':None}
        self.policy={'revision':payload['expectedRevision']+1,'maxActiveDelegations':payload['maxActiveDelegations'],
                     'accepting':payload['accepting']}
        return self.view(actor),None

    def competing(self,actor,**changes):
        """Another writer (user/other device) commits a newer revision directly on the platform."""
        current=self.view(actor)
        return self.write(actor,{'expectedRevision':current['revision'],'maxActiveDelegations':current['maxActiveDelegations'],
                                 'accepting':current['accepting'],**changes})[0]

    def call(self,actor,kind,payload,*,operation_id=None,timeout=10):
        if not self.connected:raise AssertionError('No policy request is sent without a platform connection')
        operation=self.store.prepare(actor,kind,payload,operation_id)
        self.calls.append(kind)
        if kind=='admission.status':result,error=self.view(actor),None
        elif kind=='admission.configure':
            if operation not in self.results:
                if self.before_configure:self.before_configure(actor,payload)
                self.results[operation]=self.write(actor,payload);self.writes.append(payload['accepting'])
            result,error=self.results[operation]
        elif kind=='operation.get':
            original=None if self.forget else self.results.get(payload['operationId'])
            result,error=original if original else (None,{'code':'not_found','message':'Original operation is not visible','recovery':None})
        else:raise AssertionError('Unexpected platform operation '+kind)
        if kind in self.lose:
            self.lose.discard(kind)
            raise RuntimeFault('unknown_result','Original operation is saved; resume it without changing its identity','resume_original',operation_id=operation)
        frame={'agentId':actor,'operation':kind,'operationId':operation,'ok':error is None}
        frame.update({'result':result} if error is None else {'error':error})
        self.store.complete(frame)
        if error:raise RuntimeFault(error['code'],error['message'],error['recovery'],operation_id=operation)
        return copy.deepcopy(result)


IDENTITY=SimpleNamespace(profile='service',agent_id='agent-service')


class Scope:
    def __init__(self,path):
        self.path=path;self.ready=True;self.open()

    def open(self):
        self.store=RuntimeStore(self.path,'http://localhost',[IDENTITY.agent_id],owner_id='owner')
        ledger=getattr(self,'ledger',None)
        self.ledger=ledger or Ledger(self.store);self.ledger.store=self.store
        runtime=SimpleNamespace(store=self.store,connection=self.ledger,require_execution=self.require)
        execution=SimpleNamespace(runtime=runtime,role=SimpleNamespace(identities={'service':IDENTITY}),
                                  configs={'service':{'max_active_delegations':2,'accepting':True}},
                                  ready=lambda profile:True)
        self.policy=AdmissionPolicy(execution)

    def restart(self):
        self.store.close();self.open()

    def require(self):
        if not self.ledger.connected:raise RuntimeFault('not_ready','A current execution connection is required','restore_connection')

    def health(self,state,host='default'):
        return self.policy.system_health(IDENTITY,host,state,recovery_ready=lambda profile:{'ready':self.ready,'missing':[]})

    def pause_state(self):return self.policy.system_pause(IDENTITY)


@pytest.fixture
def scope(tmp_path):
    value=Scope(tmp_path/'runtime.sqlite')
    value.policy.initialize(IDENTITY)
    assert value.ledger.policy=={'revision':1,'maxActiveDelegations':2,'accepting':True}
    value.ledger.calls.clear();value.ledger.writes.clear()
    yield value
    value.store.close()


def test_confirmed_unavailability_pauses_once_and_fence_gates_cas_resume(scope):
    assert scope.health('unavailable')['status']=='paused'
    assert scope.ledger.policy=={'revision':2,'maxActiveDelegations':2,'accepting':False}
    assert scope.pause_state()=={'state':'paused','reason':'default_host_unavailable'}
    scope.ready=False;scope.ledger.calls.clear()
    assert scope.health('available')=={'status':'waiting','reason':'restore_original_definition_and_resources'}
    assert scope.ledger.calls==[] and scope.ledger.policy['revision']==2
    assert scope.pause_state()=={'state':'paused','reason':'restore_original_definition_and_resources'}
    scope.ready=True
    assert scope.health('available')['status']=='resumed'
    assert scope.ledger.policy=={'revision':3,'maxActiveDelegations':2,'accepting':True}
    assert scope.pause_state() is None and scope.ledger.writes==[False,True]


def test_sampling_without_possible_change_sends_no_policy_request(scope):
    for state in ('available','busy','unknown'):assert scope.health(state)['status'] in ('preserved','waiting')
    assert scope.ledger.calls==[]
    scope.health('unavailable');scope.ledger.calls.clear()
    assert scope.health('unavailable')=={'status':'preserved','reason':'original_cycle'}
    scope.ledger.connected=False
    assert scope.health('available')=={'status':'waiting','reason':'restore_connection'}
    assert scope.ledger.calls==[] and scope.ledger.policy['accepting'] is False


def test_other_host_does_not_resume_original_cycle(scope):
    scope.health('unavailable',host='default')
    assert scope.health('available',host='other')['reason']=='restore_original_host'
    assert scope.ledger.policy['accepting'] is False


@pytest.mark.parametrize('manual_when',['before','after'])
def test_manual_pause_before_or_after_is_never_resumed_over(scope,manual_when):
    if manual_when=='before':scope.policy.change(IDENTITY,'manual-pause',False)
    result=scope.health('unavailable')
    if manual_when=='before':assert result=={'status':'preserved','reason':'already_paused'}
    else:
        scope.policy.change(IDENTITY,'manual-pause',False)
        assert scope.pause_state() is None
    manual=dict(scope.ledger.policy)
    scope.health('available');scope.health('available')
    assert scope.ledger.policy==manual and manual['accepting'] is False


def test_user_resume_supersedes_cycle_and_replay_does_not(scope):
    scope.policy.change(IDENTITY,'pause-a',False);scope.policy.change(IDENTITY,'resume-a',True)
    scope.health('unavailable');paused=dict(scope.ledger.policy)
    scope.policy.change(IDENTITY,'resume-a',True)
    assert scope.pause_state()=={'state':'paused','reason':'default_host_unavailable'}
    assert scope.ledger.policy==paused
    scope.policy.change(IDENTITY,'resume-b',True)
    assert scope.pause_state() is None and scope.ledger.policy['accepting'] is True
    assert scope.health('unavailable')=={'status':'preserved','reason':'original_cycle'}
    assert scope.health('available')=={'status':'preserved','reason':'policy_changed'}
    assert scope.health('unavailable')['status']=='paused'


def test_pause_revision_race_is_not_rebased_and_rejected_cycle_finishes(scope):
    scope.ledger.before_configure=lambda actor,payload:scope.ledger.competing(actor,maxActiveDelegations=3)
    with pytest.raises(RuntimeFault) as error:scope.health('unavailable')
    assert error.value.code=='revision_conflict'
    scope.ledger.before_configure=None
    assert scope.ledger.policy=={'revision':2,'maxActiveDelegations':3,'accepting':True}
    assert scope.health('unavailable')=={'status':'preserved','reason':'original_policy_rejected'}
    assert scope.pause_state() is None
    assert scope.health('available')=={'status':'preserved','reason':'policy_changed'}
    assert scope.health('unavailable')['status']=='paused' and scope.ledger.policy['revision']==3


def test_resume_revision_race_never_rebases_over_user_change(scope):
    scope.health('unavailable')
    scope.ledger.before_configure=lambda actor,payload:scope.ledger.competing(actor,accepting=False,maxActiveDelegations=3)
    with pytest.raises(RuntimeFault) as error:scope.health('available')
    assert error.value.code=='revision_conflict'
    scope.ledger.before_configure=None;competing=dict(scope.ledger.policy)
    assert scope.health('available')=={'status':'preserved','reason':'original_resume_rejected'}
    scope.health('available')
    assert scope.ledger.policy==competing=={'revision':3,'maxActiveDelegations':3,'accepting':False}
    assert scope.ledger.writes==[False,True]


def test_restart_reopens_same_store_and_resumes_only_original_cycle(scope):
    scope.health('unavailable');runtime_id=scope.store.runtime_id
    scope.restart()
    assert scope.store.runtime_id==runtime_id
    assert scope.pause_state()=={'state':'paused','reason':'default_host_unavailable'}
    assert scope.health('available')['status']=='resumed'
    assert scope.ledger.policy=={'revision':3,'maxActiveDelegations':2,'accepting':True}


def test_lost_pause_response_is_unknown_then_recovered_without_rewrite(scope):
    scope.ledger.lose={'admission.configure'}
    with pytest.raises(RuntimeFault,match='original admission policy operation') as error:scope.health('unavailable')
    assert error.value.code=='unknown_result'
    assert scope.pause_state()=={'state':'unknown','reason':'unknown_result'}
    assert scope.ledger.policy['accepting'] is False and scope.ledger.policy['revision']==2
    assert scope.health('available')['status']=='resumed'
    assert scope.ledger.writes==[False,True] and scope.pause_state() is None


def test_missing_operation_query_keeps_unknown_pending_and_original_policy(scope):
    scope.ledger.lose={'admission.configure'};scope.ledger.forget=True
    with pytest.raises(RuntimeFault):scope.health('unavailable')
    for state in ('available','unavailable'):
        with pytest.raises(RuntimeFault) as error:scope.health(state)
        assert error.value.code=='unknown_result'
    namespace=scope.policy.namespace(IDENTITY)
    assert scope.store.checkpoint(namespace,'pending')
    assert scope.pause_state()['state']=='unknown' and scope.ledger.writes==[False]
    scope.ledger.forget=False
    assert scope.health('unavailable')=={'status':'preserved','reason':'original_cycle'}
    assert scope.pause_state()=={'state':'paused','reason':'default_host_unavailable'}
    assert scope.store.checkpoint(namespace,'pending') is None and scope.ledger.writes==[False]


def test_platform_commit_with_failed_local_completion_continues_from_pausing(scope,monkeypatch):
    update=scope.store.checkpoint_update
    def crash(namespace,values,**kwargs):
        if 'configuration' in values:raise OSError('Test crash before atomic local completion')
        return update(namespace,values,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(scope.store,'checkpoint_update',crash)
        with pytest.raises(OSError):scope.health('unavailable')
    assert scope.pause_state()['state']=='pausing' and scope.ledger.policy['revision']==2
    assert scope.health('unavailable')=={'status':'preserved','reason':'original_cycle'}
    assert scope.pause_state()['state']=='paused' and scope.ledger.writes==[False]
    assert scope.health('available')['status']=='resumed' and scope.ledger.writes==[False,True]


def test_cycle_owned_by_another_instance_is_identity_conflict(scope):
    scope.health('unavailable');namespace=scope.policy.namespace(IDENTITY)
    record=scope.store.checkpoint(namespace,'system-health-pause')
    scope.store.checkpoint_set(namespace,'system-health-pause',{**record,'state':'resuming','owner':'other-runtime','resumeOperation':'x'})
    with pytest.raises(RuntimeFault) as error:scope.health('available')
    assert error.value.code=='identity_conflict' and scope.ledger.policy['accepting'] is False
