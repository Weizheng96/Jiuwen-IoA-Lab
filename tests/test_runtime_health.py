"""P34 public coordination with real registry/store and boundary-only host/clock."""
import threading

import pytest

from ioalab_sdk.runtime import HostRegistry,RuntimeStore,RuntimeFault
from ioalab_sdk.runtime.health import RuntimeHealth,SAMPLE_INTERVAL
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.configuration import configure,inspect_config
from ioalab_sdk.runtime.files import private_json,read_json
from conftest import wait_for


class Clock:
    def __init__(self):self.now=0
    def __call__(self):return self.now


class Host:
    def __init__(self,state):self.state=state;self.checks=0;self.gate=None
    def check(self):
        self.checks+=1
        if self.gate is not None:self.gate.wait(10)
        return {'state':self.state,'basis':'Controlled machine evidence; no inference'}


@pytest.fixture
def health_scope(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='owner')
    first,second=Host('unavailable'),Host('available')
    registry=HostRegistry({'first':first,'second':second},'first');registry.bind(store)
    yield registry,first,second,Clock()
    store.close()


def sample(monitor):
    """One resident-loop poll that starts a due S02, then the poll that consumes it."""
    monitor.poll()
    wait_for(lambda:not monitor.sampling,timeout=5)
    return monitor.poll()


def test_confirmed_default_unavailability_requests_drain_at_600_seconds(health_scope):
    registry,first,second,clock=health_scope;stops=[]
    monitor=RuntimeHealth(registry,clock=clock,request_stop=lambda:stops.append('drain'))
    assert monitor.interval==SAMPLE_INTERVAL==30 and first.checks==second.checks==0
    assert sample(monitor)['continuousUnavailableSeconds']==0
    clock.now=570;assert sample(monitor)['shutdownRequested'] is False
    clock.now=599;assert monitor.poll()['shutdownRequested'] is False and not monitor.sampling
    clock.now=600;assert sample(monitor)['shutdownRequested'] is True
    assert stops==['drain'] and second.checks==0
    before=first.checks
    assert registry.status_snapshot()['state']=='unavailable'
    clock.now=700;monitor.poll()
    assert monitor.snapshot()['shutdownRequested'] is True and first.checks==before


def test_sampling_interval_is_internal_and_status_never_checks(health_scope):
    registry,first,second,clock=health_scope;monitor=RuntimeHealth(registry,clock=clock)
    sample(monitor);assert first.checks==1
    for now in (1,10,29.9):
        clock.now=now;monitor.poll();monitor.snapshot();registry.status_snapshot()
    assert first.checks==1 and not monitor.sampling
    clock.now=30;sample(monitor);assert first.checks==2


def test_unfinished_check_never_blocks_poll_and_is_only_unknown(health_scope):
    registry,first,second,clock=health_scope;first.gate=threading.Event()
    monitor=RuntimeHealth(registry,clock=clock,shutdown_seconds=45)
    assert monitor.poll()['state']=='unknown' and monitor.sampling
    clock.now=29;assert monitor.poll()['state']=='unknown'
    clock.now=30;timed_out=monitor.poll()
    assert timed_out['state']=='unknown' and 'bound' in timed_out['basis'] and timed_out['continuousUnavailableSeconds']==0
    clock.now=31;monitor.poll();assert first.checks==1,'no second S02 while the bounded unit still runs'
    first.gate.set();wait_for(lambda:not monitor.sampling,timeout=5)
    late=monitor.poll()
    assert late['state']=='unknown' and registry.status_snapshot()['state']=='unknown'
    assert not monitor.sampling and first.checks==1
    clock.now=60;assert sample(monitor)['state']=='unavailable' and first.checks==2
    assert monitor.close() is True and monitor.poll()['state']=='unavailable' and first.checks==2


def test_short_shutdown_threshold_rechecks_recovery_before_requesting_drain(health_scope):
    registry,first,second,clock=health_scope;stops=[]
    monitor=RuntimeHealth(registry,shutdown_seconds=2,clock=clock,
                          request_stop=lambda:stops.append('drain'))
    assert sample(monitor)['state']=='unavailable'
    clock.now=1.5;first.state='available'
    clock.now=2;observed=sample(monitor)
    assert observed['state']=='available' and first.checks==2
    assert observed['shutdownRequested'] is False and stops==[]
    assert registry.status_snapshot()['state']=='available'


def test_threshold_recheck_still_unavailable_requests_drain_before_next_interval(health_scope):
    registry,first,second,clock=health_scope;stops=[]
    monitor=RuntimeHealth(registry,shutdown_seconds=45,clock=clock,request_stop=lambda:stops.append('drain'))
    sample(monitor);clock.now=30;assert sample(monitor)['shutdownRequested'] is False
    clock.now=45;observed=sample(monitor)
    assert observed['shutdownRequested'] is True and observed['continuousUnavailableSeconds']==45
    assert first.checks==3 and stops==['drain']


@pytest.mark.parametrize('transient',['available','busy','unknown'])
def test_busy_or_uncertain_observation_breaks_continuous_failure(health_scope,transient):
    registry,first,second,clock=health_scope;monitor=RuntimeHealth(registry,clock=clock)
    sample(monitor);clock.now=570;first.state=transient
    assert sample(monitor)['shutdownRequested'] is False
    first.state='unavailable';clock.now=600;sample(monitor)
    clock.now=601
    assert monitor.poll()['continuousUnavailableSeconds']==1


def test_policy_error_is_recorded_once_per_sample_without_hiding_state(health_scope):
    registry,first,second,clock=health_scope;calls=[]
    def policy(ref,state):
        calls.append((ref,state,registry.status_snapshot()['state']))
        raise RuntimeFault('unknown_result','Original pause receipt is not verified','resume_original')
    monitor=RuntimeHealth(registry,clock=clock,on_health=policy)
    observed=sample(monitor)
    assert calls==[('first','unavailable','unavailable')]
    assert observed['policyError']=={'code':'unknown_result','recovery':'resume_original'}
    assert registry.status_snapshot()['observation']['policyError']['code']=='unknown_result'


def test_business_authentication_failure_survives_catalog_success_and_restart(health_scope):
    registry,first,second,clock=health_scope;first.state='available'
    monitor=RuntimeHealth(registry,clock=clock)
    monitor.business_feedback('first',{'state':'unavailable','scope':'host',
        'failureKind':'authentication_revoked','basis':'Native machine explicitly revoked authentication'})
    assert sample(monitor)['state']=='unavailable'
    restarted=RuntimeHealth(registry,clock=clock)
    assert sample(restarted)['state']=='unavailable'
    restarted.business_feedback('second',{'state':'available','scope':'host','basis':'Other host succeeded'})
    assert restarted.snapshot()['state']=='unavailable'
    restarted.business_feedback('first',{'state':'available','scope':'session','basis':'Old session ended'})
    assert restarted.snapshot()['state']=='unavailable'
    assert restarted.business_feedback('first',{'state':'available','scope':'host',
        'basis':'Trusted owner received a successful real default-host machine result'})['state']=='available'


def settings(tmp_path):
    return {'platform_url':'http://localhost','username':'health-user','password':'private-test-value',
            'data_dir':str(tmp_path/'home'),'identities':[{'key':'p','role':'assistant','name':'P'}]}


@pytest.mark.parametrize('invalid',[True,0,-1,float('nan'),float('inf'),'600'])
def test_shutdown_duration_rejects_nonpositive_nonfinite_or_nonnumber(tmp_path,invalid):
    with pytest.raises(RuntimeFault,match='positive finite'):
        RuntimeConfig.from_mapping(tmp_path/'config.json',{**settings(tmp_path),'host_unavailable_shutdown_seconds':invalid})


def test_original_configuration_merge_preserves_account_and_changes_health_digest(tmp_path):
    path=tmp_path/'config.json';original=settings(tmp_path);private_json(path,original)
    assert RuntimeConfig.load(path).host_unavailable_shutdown_seconds==600
    before=inspect_config(path);args=tmp_path/'arguments.json'
    private_json(args,{'expectedDigest':before['configurationDigest'],
                       'changes':{'host_unavailable_shutdown_seconds':2.5}})
    after=configure(path,args)
    assert after['configurationDigest']!=before['configurationDigest']
    assert read_json(path)=={**original,'host_unavailable_shutdown_seconds':2.5}
    assert RuntimeConfig.load(path).host_unavailable_shutdown_seconds==2.5
