"""P34 public coordination with real registry/store and boundary-only host/clock."""
import pytest

from ioalab_sdk.runtime import HostRegistry,RuntimeStore,RuntimeFault
from ioalab_sdk.runtime.health import RuntimeHealth
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.configuration import configure,inspect_config
from ioalab_sdk.runtime.files import private_json,read_json


class Clock:
    def __init__(self):self.now=0
    def __call__(self):return self.now


class Host:
    def __init__(self,state):self.state=state;self.checks=0
    def check(self):
        self.checks+=1
        return {'state':self.state,'basis':'Controlled machine evidence; no inference'}


@pytest.fixture
def health_scope(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='owner')
    first,second=Host('unavailable'),Host('available')
    registry=HostRegistry({'first':first,'second':second},'first');registry.bind(store)
    yield registry,first,second,Clock()
    store.close()


def test_confirmed_default_unavailability_requests_drain_at_600_seconds(health_scope):
    registry,first,second,clock=health_scope;stops=[]
    monitor=RuntimeHealth(registry,clock=clock,request_stop=lambda:stops.append('drain'))
    assert first.checks==second.checks==0
    assert monitor.poll()['continuousUnavailableSeconds']==0
    clock.now=599;assert monitor.poll()['shutdownRequested'] is False
    clock.now=600;assert monitor.poll()['shutdownRequested'] is True
    assert stops==['drain'] and second.checks==0
    before=first.checks
    assert registry.status_snapshot()['state']=='unavailable'
    assert monitor.snapshot()['shutdownRequested'] is True and first.checks==before


def test_short_shutdown_threshold_rechecks_recovery_before_requesting_drain(health_scope):
    registry,first,second,clock=health_scope;stops=[]
    monitor=RuntimeHealth(registry,shutdown_seconds=2,interval=5,clock=clock,
                          request_stop=lambda:stops.append('drain'))
    assert monitor.poll()['state']=='unavailable'
    clock.now=1.5;first.state='available'
    clock.now=2;observed=monitor.poll()
    assert observed['state']=='available'
    assert observed['shutdownRequested'] is False and stops==[]
    assert registry.status_snapshot()['state']=='available'


@pytest.mark.parametrize('transient',['available','busy','unknown'])
def test_busy_or_uncertain_observation_breaks_continuous_failure(health_scope,transient):
    registry,first,second,clock=health_scope;monitor=RuntimeHealth(registry,clock=clock,interval=1)
    monitor.poll();clock.now=590;first.state=transient
    assert monitor.poll()['shutdownRequested'] is False
    first.state='unavailable';clock.now=599;monitor.poll()
    clock.now=600
    assert monitor.poll()['continuousUnavailableSeconds']==1


def test_business_authentication_failure_survives_catalog_success_and_restart(health_scope):
    registry,first,second,clock=health_scope;first.state='available'
    monitor=RuntimeHealth(registry,clock=clock)
    monitor.business_feedback('first',{'state':'unavailable','scope':'host',
        'failureKind':'authentication_revoked','basis':'Native machine explicitly revoked authentication'})
    assert monitor.poll()['state']=='unavailable'
    restarted=RuntimeHealth(registry,clock=clock)
    assert restarted.poll()['state']=='unavailable'
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
