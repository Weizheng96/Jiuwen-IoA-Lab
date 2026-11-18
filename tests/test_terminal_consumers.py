"""Machine completion never substitutes for an error-free business result."""
from dataclasses import replace
import pytest

from ioalab_sdk.runtime import LocalClient,SubmissionError,MachineTerminal,RuntimeStore,RuntimeFault
from test_native_events import Transport,assemble,publish,finish
from test_resident_delegation import QuoteHost,participants,request
from test_resident_contracts import candidate
from conftest import wait_for


def test_recorded_quote_with_whole_submission_error_is_preserved_without_signing(platform,tmp_path):
    class ErroredDecision(QuoteHost):
        def submit(self,*args,**kwargs):
            result=super().submit(*args,**kwargs)
            return replace(result,error=SubmissionError('native_guard_failed','native_definition','accepted',
                'Original round guard failed'))
    host=ErroredDecision()
    with participants(platform,tmp_path,agent_host=host) as (provider,assistant,_,template,task):
        started,_=request(assistant,provider,template,task)
        assert started['status']=='accepted'
        snapshots=[]
        def stopped():
            items=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service'})['data']['items']
            snapshots[:]=[item for item in items if item['status']=='unknown' and item['phase']=='quote']
            return bool(snapshots)
        wait_for(stopped)
        assert len(host.turns)==1
        # This is a saved local business decision, not an unsigned or unexecuted claim.
        endpoint=next(iter(provider.modules[0].execution.contexts.values()))
        saved=list(endpoint.store.all('business').values())
        assert len(saved)==1 and 'acceptance' in saved[0]['decision']
        assert not endpoint.provider.store.all('cards')
        state=LocalClient(assistant.config.home).call('assistant.delegation.status',{'quoteRef':started['data']['quoteRef']})
        assert state['status']!='completed'
        # Public A2C recovery re-enters the same policy with its recorded decision.
        sdk_task=next(iter(endpoint.provider.tasks.values()))
        deferred=endpoint.provider.resume_request(sdk_task.id)
        with pytest.raises(RuntimeFault) as failure:
            endpoint.provider.finish(deferred)
        assert getattr(failure.value,'code',None)=='native_guard_failed'
        assert len(host.turns)==1 and not endpoint.provider.store.all('cards')
        assert list(endpoint.store.all('business').values())==saved


def test_verified_cached_quote_uses_original_submission_without_another_model_or_signature(platform,tmp_path):
    host=QuoteHost()
    with participants(platform,tmp_path,agent_host=host) as (provider,assistant,_,template,task):
        quote=candidate(assistant,provider,template,task)
        endpoint=next(iter(provider.modules[0].execution.contexts.values()))
        pending=next(iter(endpoint.store.all('pending_requests').values()))
        original_cards=endpoint.provider.store.all('cards')
        original_decisions=endpoint.store.all('business')
        proposal=endpoint.provider.policy.draft(template,pending['body']['request'],{})
        assert proposal.line_items=={'result':9}
        assert len(host.turns)==1 and endpoint.store.all('business')==original_decisions
        assert endpoint.provider.store.all('cards')==original_cards
        assert quote['card']['signatures']['2'] and quote['card']['signatures']['3']


def test_completed_errored_notice_releases_permit_without_reporting_success_or_resending(tmp_path):
    class ErroredNotice(Transport):
        def submit(self,*args,**kwargs):
            receipt=super().submit(*args,**kwargs)
            terminal=MachineTerminal(receipt.operation_id,receipt.attempt_id,receipt.session_id,
                receipt.request_id,'completed','actual-native-end')
            return replace(receipt,state='completed',terminal=terminal,delivery='handled',
                error=SubmissionError('host_event_error','event_callback','accepted','Original event reporting failed'))
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=ErroredNotice();registry,runtime,service=assemble(store,{'host':host},max_models=1)
    runtime.scheduler.start()
    try:
        _,op=publish(registry,service,tmp_path,'errored-notice')
        first=runtime.scheduler.reference('actor',service.handler,op)
        wait_for(lambda:runtime.scheduler.get(first)['status']=='unknown')
        assert runtime.scheduler.get(first)['data']['reason']=='native_submission_error'
        _,second=publish(registry,service,tmp_path,'independent-notice')
        wait_for(lambda:len(host.sent)==2 and runtime.scheduler.get(runtime.scheduler.reference('actor',service.handler,second))['status']=='unknown')
        service.recover()
        assert len(host.sent)==2
    finally:finish(runtime);store.close()
