"""Public local operations, isolated real user channel, actual WS/AP2 settlement."""
import copy
import pytest

from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.provider.policy import HostPolicy
from test_assistant_role import user_decision
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import participants,uid
from test_resident_fulfillment import operation
import test_resident_delegation as assembly
from conftest import wait_for


@pytest.mark.parametrize('formation',['proposal','countersign'])
@pytest.mark.parametrize('settlement',['positive','zero','refund','unilateral'])
def test_public_termination_requires_own_user_and_automatically_settles_original_contract(platform,tmp_path,monkeypatch,formation,settlement):
    original=assembly.template_from_definition
    def deposit(value,**kwargs):
        value=copy.deepcopy(value);value['pricing']['deposit']='1'
        return original(value,**kwargs)
    monkeypatch.setattr(assembly,'template_from_definition',deposit)
    if settlement in ('zero','refund'):monkeypatch.setattr(HostPolicy,'termination_fee',lambda *args:0)
    with participants(platform,tmp_path,formation=formation,fee=0) as (provider,assistant,host,template,task):
        selected=candidate(assistant,provider,template,task)
        formed,_,_=confirm(assistant,selected);ref=formed['data']['contractRef']
        wait_for(lambda:status(assistant,ref));initial=status(assistant,ref)['data']
        f=assistant.modules[0].fulfillment;client,_=f.client(ref)
        if settlement=='refund':
            # Pay the actual deposit but fail before submitting work.start.
            # This setup proves the financial boundary, not native UI operation.
            class BeforeStart(Exception):pass
            send=client.transport.send
            def before_work(to,op,body,*args,**kwargs):
                if op=='work.start':raise BeforeStart()
                return send(to,op,body,*args,**kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(client.transport,'send',before_work)
                with client.transport.operation(uid()):
                    with pytest.raises(BeforeStart):client.start(initial['card'])
        rpc=LocalClient(assistant.config.home)
        args={'contractRef':ref,'mode':'unilateral' if settlement=='unilateral' else 'agreed','reason':'用户取消原请求'}
        if settlement!='unilateral':
            key=uid();quoted=rpc.call('assistant.delegation.termination_quote',{'contractRef':ref},operation_id=key)
            assert quoted['status']=='accepted',quoted
            wait_for(lambda:operation(assistant,ref,key))
            result=operation(assistant,ref,key);assert result['status']=='completed',result
            args['terminationQuoteRef']=result['data']['terminationQuoteRef']
            assert not client.card(ref)['fulfillment'].get('termination')
        key=uid()
        assert rpc.call('assistant.delegation.terminate',{**args,'confirmed':True},operation_id=uid())['status']=='rejected'
        pending=rpc.call('assistant.delegation.terminate',args,operation_id=key)
        assert pending['status']=='waiting',pending
        assert not f.client(ref)[0].card(ref)['fulfillment'].get('termination')
        preview=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']
        assert preview['contractRef']==ref and preview['mode']==args['mode']
        user_decision(assistant,pending['data']['confirmationRef'])
        submitted=rpc.call('assistant.delegation.terminate',args,operation_id=key)
        assert submitted['status']=='accepted',submitted
        wait_for(lambda:operation(assistant,ref,key))
        result=operation(assistant,ref,key);assert result['status']=='completed',result
        try:wait_for(lambda:platform.market.market.store.get('ledger',ref))
        except AssertionError:
            raise AssertionError({'provider':provider.modules[0].execution.errors,'jobs':provider.scheduler.status(),
                'assistantError':assistant.modules[0].error,'assistantJobs':assistant.scheduler.status()})
        wait_for(lambda:status(assistant,ref)['data']['closed'])
        ending=status(assistant,ref)['data']['terminationStatus']
        expected={'positive':'paid','zero':'not_required','refund':'refunded','unilateral':'unagreed'}[settlement]
        assert ending['terminated'] and ending['notification']=='acknowledged' and ending['settlement']==expected and ending['closed'],ending
        payments=platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='termination'",ref)
        assert len(payments)==int(settlement in ('positive','refund'))
        if settlement=='refund':
            assert float(payments[0]['amount'])==1
            assert payments[0]['payer_id']==provider.account.account['id'] and payments[0]['payee_id']==assistant.account.account['id']
            view=next(row for row in platform.contract_views.list(assistant.account.account['id'],private=True) if row['card_digest']==ref)
            assert float(view['paid'])==0 and any(p['direction']=='refund' for p in view['payments'])
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',initial['admissionId'])['state']=='released'
        assert len(host.turns)==1
        assert not platform.db.rows("SELECT 1 FROM delegation_protocol_calls WHERE admission=? AND op='work.start'",initial['admissionId'])
        assert rpc.call('assistant.delegation.terminate',args,operation_id=key)['data']==result['data']


@pytest.mark.parametrize('unknown',[False,True])
def test_termination_does_not_release_an_original_running_tool_or_hide_its_unknown_effect(platform,tmp_path,monkeypatch,unknown):
    import json
    import threading
    from ioalab_sdk.runtime.host import HostResult,ToolBinding
    from ioalab_sdk.runtime.protocol import RuntimeFault
    from test_resident_delegation import QuoteHost
    from test_resident_fulfillment import invoke
    entered=threading.Event();release=threading.Event();denied=[]
    def effect(args,context):
        entered.set();assert release.wait(20)
        if unknown:raise RuntimeFault('unknown_result','Isolated external effect has no known receipt')
        return {'effect':'actual original receipt'}
    class Host(QuoteHost):
        def run(self,turn):
            data=json.loads(turn.input)
            if data['phase']!='production':return super().run(turn)
            self.turns.append(turn);turn.on_session(turn.session_id)
            try:turn.on_tool('original_effect',{},'effect')
            except RuntimeFault:pass
            denied.append(turn.on_tool('provider.task.request_step',{'step':'not-configured'},'late'))
            return HostResult('completed',turn.session_id,{})
    tool=ToolBinding('original_effect','Isolated original effect',{'type':'object','additionalProperties':False},effect,'reservation')
    with participants(platform,tmp_path,fee=0,agent_host=Host(),provider_tools={'service':{'original_effect':tool}}) as (provider,assistant,host,template,task):
        selected=candidate(assistant,provider,template,task);formed,_,_=confirm(assistant,selected);ref=formed['data']['contractRef']
        wait_for(lambda:status(assistant,ref));initial=status(assistant,ref)['data']
        started,_=invoke(assistant,ref,'start')
        wait_for(lambda:operation(assistant,ref,started))
        assert operation(assistant,ref,started)['status']=='completed'
        wait_for(entered.is_set)
        rpc=LocalClient(assistant.config.home);key=uid();args={'contractRef':ref,'mode':'unilateral'}
        pending=rpc.call('assistant.delegation.terminate',args,operation_id=key);assert pending['status']=='waiting'
        user_decision(assistant,pending['data']['confirmationRef'])
        assert rpc.call('assistant.delegation.terminate',args,operation_id=key)['status']=='accepted'
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed'
        execution=provider.modules[0].execution;endpoint=execution.contexts[initial['interactionId']]
        try:
            writers=execution.termination.writers(endpoint)
            assert any(row['executing'] for row in writers)
            assert not platform.market.market.store.get('ledger',ref)
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',initial['admissionId'])['state']=='committed'
        finally:release.set()
        wait_for(lambda:denied)
        assert denied[0]['status']=='rejected'
        if unknown:
            wait_for(lambda:any(row['state']=='unknown' and not row['executing'] for row in execution.termination.writers(endpoint)))
            execution.termination.advance(endpoint)
            assert not platform.market.market.store.get('ledger',ref)
            assert any('result' not in item for item in endpoint.store.all('production_calls').values())
        else:
            wait_for(lambda:platform.market.market.store.get('ledger',ref))
            assert all(not row['executing'] for row in execution.termination.writers(endpoint))
            assert all('result' in item for item in endpoint.store.all('production_calls').values())


@pytest.mark.parametrize('lost',['termination.notify','payment.notify'])
def test_lost_termination_ack_resumes_original_signed_notice_and_single_payment(platform,tmp_path,monkeypatch,lost):
    from ioalab_sdk.runtime.protocol import RuntimeFault
    with participants(platform,tmp_path,fee=0) as (provider,assistant,host,template,task):
        selected=candidate(assistant,provider,template,task);formed,_,_=confirm(assistant,selected);ref=formed['data']['contractRef']
        wait_for(lambda:status(assistant,ref));rpc=LocalClient(assistant.config.home)
        quote_key=uid();rpc.call('assistant.delegation.termination_quote',{'contractRef':ref},operation_id=quote_key)
        wait_for(lambda:operation(assistant,ref,quote_key));assert operation(assistant,ref,quote_key)['status']=='completed'
        args={'contractRef':ref,'mode':'agreed','terminationQuoteRef':quote_key};key=uid()
        pending=rpc.call('assistant.delegation.terminate',args,operation_id=key)
        user_decision(assistant,pending['data']['confirmationRef'])
        from ioalab_sdk.adapters.transport import SessionTransport
        send=SessionTransport.send;dropped=[]
        def lose(transport,to,op,body,*items,**kwargs):
            reply=send(transport,to,op,body,*items,**kwargs)
            if op==lost and transport.identity.agent_id==assistant.identities[0].agent_id and not dropped:
                original_client,_=assistant.modules[0].fulfillment.client(ref)
                dropped.append({'notice':original_client.card(ref)['fulfillment']['termination']['termination'],'voucher':body.get('voucher')})
                raise RuntimeFault('unknown_result','Isolated lost original acknowledgement')
            return reply
        monkeypatch.setattr(SessionTransport,'send',lose)
        rpc.call('assistant.delegation.terminate',args,operation_id=key)
        try:wait_for(lambda:(current:=operation(assistant,ref,key)) and current['status']=='completed')
        except AssertionError:
            actual_client,_=assistant.modules[0].fulfillment.client(ref)
            pending_state=actual_client.store.get('cards',ref).get('terminationPending',{})
            raise AssertionError({'originalOperation':operation(assistant,ref,key),'roleError':assistant.modules[0].error,
                'providerErrors':provider.modules[0].execution.errors,'connected':assistant.connection.connected,
                'notificationFlags':{name:pending_state.get(name) for name in ('notified','paymentNotified')}})
        wait_for(lambda:platform.market.market.store.get('ledger',ref))
        client,_=assistant.modules[0].fulfillment.client(ref)
        assert len(dropped)==1 and client.card(ref)['fulfillment']['termination']['termination']==dropped[0]['notice']
        if dropped[0]['voucher']:assert client.store.get('cards',ref)['terminationVoucher']==dropped[0]['voucher']
        original=assistant.modules[0].fulfillment.record(key)
        assert original['approval']==pending['data']['confirmationRef']
        assert len(platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='termination'",ref))==1
        assert len([row for row in assistant.confirmations.list() if row['reference']==pending['data']['confirmationRef']])==1


@pytest.mark.parametrize('failure',['restart','lost_close'])
def test_service_settlement_recovers_original_context_and_committed_close(platform,tmp_path,monkeypatch,failure):
    from ioalab_sdk.provider import ProviderRole
    from ioalab_sdk.runtime.resident import ResidentRuntime
    from ioalab_sdk.adapters.transport import SessionTransport
    from ioalab_sdk.runtime.protocol import RuntimeFault
    with participants(platform,tmp_path,fee=0) as (provider,assistant,host,template,task):
        selected=candidate(assistant,provider,template,task);formed,_,_=confirm(assistant,selected);ref=formed['data']['contractRef']
        wait_for(lambda:status(assistant,ref));initial=status(assistant,ref)['data']
        if failure=='restart':monkeypatch.setattr(provider.modules[0].execution.termination,'advance',lambda *_:None)
        control=SessionTransport._control;dropped=[]
        def lose(transport,kind,payload):
            result=control(transport,kind,payload)
            if failure=='lost_close' and kind=='a2c.market' and payload.get('op')=='card.close' and not dropped:
                dropped.append(payload['body']['card']);raise RuntimeFault('unknown_result','Isolated lost committed close')
            return result
        monkeypatch.setattr(SessionTransport,'_control',lose)
        rpc=LocalClient(assistant.config.home);quote_key=uid()
        rpc.call('assistant.delegation.termination_quote',{'contractRef':ref},operation_id=quote_key)
        wait_for(lambda:operation(assistant,ref,quote_key));assert operation(assistant,ref,quote_key)['status']=='completed'
        key=uid();args={'contractRef':ref,'mode':'agreed','terminationQuoteRef':quote_key}
        pending=rpc.call('assistant.delegation.terminate',args,operation_id=key)
        user_decision(assistant,pending['data']['confirmationRef'])
        rpc.call('assistant.delegation.terminate',args,operation_id=key)
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed'
        original=assistant.modules[0].fulfillment.client(ref)[0].card(ref)['fulfillment']['termination']
        resumed=None
        try:
            if failure=='restart':
                assert not platform.market.market.store.get('ledger',ref)
                config=provider.config;provider.close()
                role=ProviderRole(host);resumed=ResidentRuntime(config,modules=(role,),host_status=host.capabilities).start()
                wait_for(lambda:resumed.connection.connected)
                current=resumed
            else:current=provider
            wait_for(lambda:platform.market.market.store.get('ledger',ref))
            execution=current.modules[0].execution
            wait_for(lambda:initial['interactionId'] in execution.contexts and execution.contexts[initial['interactionId']].store.get('cards',ref)['state']=='closed')
            endpoint=execution.contexts[initial['interactionId']]
            assert endpoint.provider.card(ref)['fulfillment']['termination']==original
            assert len(platform.db.rows("SELECT 1 FROM payment_records WHERE ref=? AND purpose='termination'",ref))==1
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',initial['admissionId'])['state']=='released'
            assert len(host.turns)==1
            if failure=='lost_close':assert dropped==[endpoint.provider.card(ref)]
        finally:
            if resumed:resumed.close()
