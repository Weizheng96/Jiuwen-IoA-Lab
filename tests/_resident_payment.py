"""Obtain a real signed unpaid installment through resident IPC and owner review."""
from contextlib import contextmanager
import copy
import pytest

from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_delegation import participants
from test_resident_contracts import candidate,confirm,status
from test_resident_fulfillment import ProductionHost,VERIFIER,invoke,operation,accept_preview,configure_template
from test_assistant_role import user_decision
from conftest import wait_for


@contextmanager
def ready_for_accept(platform,folder,*,provider_factory=None):
    with pytest.MonkeyPatch.context() as patch:
        configure_template(patch)
        import test_resident_delegation as assembly
        configured=assembly.template_from_definition
        def installment_only(value,**kwargs):
            template=configured(value,**kwargs)
            # This fixture isolates the installment transaction. Keep the real
            # protected step, but do not create a prior deposit payment record.
            template['pricing']['depositFormula']='0'
            return template
        patch.setattr(assembly,'template_from_definition',installment_only)
        with participants(platform,folder,agent_host=ProductionHost(),verifier_source=VERIFIER,provider_factory=provider_factory) as (provider,assistant,host,template,task):
            selected,_,_=confirm(assistant,candidate(assistant,provider,template,task))
            ref=selected['data']['contractRef']
            wait_for(lambda:(status(assistant,ref) or {}).get('data',{}).get('formed'))
            key,_=invoke(assistant,ref,'start')
            wait_for(lambda:operation(assistant,ref,key))
            assert operation(assistant,ref,key)['status']=='completed'
            wait_for(lambda:status(assistant,ref)['data']['stepRequest'])
            step=status(assistant,ref)['data']['stepRequestDigest']
            key,pending=invoke(assistant,ref,'answer_step',stepRequestDigest=step)
            user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
            rpc=LocalClient(assistant.config.home)
            rpc.call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':step},operation_id=key)
            wait_for(lambda:operation(assistant,ref,key))
            wait_for(lambda:status(assistant,ref)['data']['availablePeriods']==1)
            key,_=invoke(assistant,ref,'fetch',period=1)
            wait_for(lambda:operation(assistant,ref,key))
            assert operation(assistant,ref,key)['status']=='completed'
            key,pending=accept_preview(assistant,ref)
            yield provider,assistant,ref,key,pending


@contextmanager
def signed_unpaid(platform,folder,monkeypatch):
    with ready_for_accept(platform,folder) as (provider,assistant,ref,key,pending):
        rpc=LocalClient(assistant.config.home)
        user_decision(assistant,pending['data']['confirmationRef'])
        captured=[];original=assistant.connection.call
        def stop_before_payment(actor,kind,payload,**kwargs):
            if kind=='ap2.pay' and payload['ap2'].get('card',{}).get('signatures',{}).get('7'):
                captured.append(copy.deepcopy(payload['ap2']))
                raise RuntimeFault('unknown_result','Isolated test stops before the payment transaction')
            return original(actor,kind,payload,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(assistant.connection,'call',stop_before_payment)
            rpc.call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)
            wait_for(lambda:operation(assistant,ref,key))
            assert captured and status(assistant,ref)['data']['card']['signatures']['7']
            assert not platform.db.one("SELECT 1 FROM payment_records WHERE ref=? AND purpose='installment'",ref)
            yield provider,assistant,ref,captured[0]
