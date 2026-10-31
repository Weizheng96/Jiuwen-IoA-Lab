"""Public recovery of a stopped quote, using original HTTP/WS/A2C tasks and fees."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import copy
import json
import os
import sqlite3
import uuid

import httpx
import pytest
from a2c.codec import utcnow
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from conftest import wait_for
from test_task_verifiers import DynamicVerifierHost,body,contract_state
from test_resident_delegation import participants,request,terminal
from test_resident_contracts import confirm
from test_resident_fulfillment import invoke,operation,accept_preview
from test_assistant_role import user_decision


class ResumeHost(DynamicVerifierHost):
    def execute(self,turn):
        data=json.loads(turn.input)
        if data['phase']!='quote':return super().execute(turn)
        self.turns.append(turn);session=turn.session_id or 'resume-'+str(uuid.uuid4());turn.on_session(session)
        prepared=data['preparedVerifiers']
        if not prepared:
            value=turn.on_tool('provider.task.verifier.publish',self.package,'first-upload')
            assert value['status']=='unknown',value
            assert turn.on_tool('provider.task.quote',{'reason':'must stop'},'blocked')['status']=='unknown'
        else:
            reference=prepared[0]['reference'];self.created.append(reference)
            result=turn.on_tool('provider.task.quote',{'acceptance':{'result':{'verifiers':{'script':reference},'notCovered':[]}},
                'acceptWithin':{'result':'PT10M'},'lineItems':{'result':9}},'recovered-quote')
            assert result['status']=='recorded',result
        return HostResult('completed',session,{})


@contextmanager
def stopped(platform,tmp_path,monkeypatch,*,host=None,business=None,timeout=20):
    host=host or ResumeHost();uploads=[]
    with participants(platform,tmp_path,agent_host=host,business=business or {'resources':{}},preload_verifier=False) as values:
        provider,assistant,host,template,task=values;api=provider.account.api
        def lose_once(*args,**kwargs):
            result=api(*args,**kwargs)
            if args[:2]==('POST','/provider/packages'):
                uploads.append(copy.deepcopy(result))
                if len(uploads)==1:raise httpx.ReadTimeout('durable package reply lost',request=httpx.Request('POST','http://local.test'))
            return result
        monkeypatch.setattr(provider.account,'api',lose_once)
        started,_=request(assistant,provider,template,task);reference=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,reference),timeout=timeout)
        assert terminal(assistant,reference)['status']=='unknown'
        client=LocalClient(provider.config.home)
        wait_for(lambda:client.call('provider.tasks.get',{'profile':'service'})['data']['items'])
        snapshot=client.call('provider.tasks.get',{'profile':'service'})['data']['items'][0]
        assert snapshot['phase']=='quote' and snapshot['status']=='unknown'
        wait_for(lambda:provider.scheduler.status()['active']==0)
        yield (*values,reference,snapshot,uploads)


def resume(provider,snapshot,operation_id=None):
    return LocalClient(provider.config.home).call('provider.task.resume',{'profile':'service','taskRef':snapshot['taskRef']},
        operation_id=operation_id or str(uuid.uuid4()))


def recovered_quote(assistant,reference,*,timeout=20):
    wait_for(lambda:terminal(assistant,reference) and terminal(assistant,reference)['status']=='completed',timeout=timeout)
    return terminal(assistant,reference)['data']


@pytest.mark.parametrize('restart',[False,True])
def test_public_resume_preserves_a2c_task_fee_session_and_completes_payment(platform,tmp_path,monkeypatch,restart):
    with stopped(platform,tmp_path,monkeypatch) as (provider,assistant,host,template,task,quote_ref,snapshot,uploads):
        assert snapshot['canResume']
        original_session=snapshot['sessionId'];original_interaction=snapshot['interactionId']
        endpoint=next(iter(provider.modules[0].execution.contexts.values()))
        original_tasks=set(endpoint.provider.tasks)
        fresh=None
        if restart:
            config=provider.config;provider.close();role=ProviderRole(host=host)
            fresh=ResidentRuntime(config,modules=(role,)).start();provider=fresh
            wait_for(lambda:provider.connection.connected)
            actual=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service','taskRef':snapshot['taskRef']})
            assert actual['data']['items'][0]['canResume'],actual
        try:
            accepted=resume(provider,snapshot);assert accepted['status']=='accepted',accepted
            quote=recovered_quote(assistant,quote_ref)
            assert quote['interactionId']==original_interaction and len(uploads)<=2 and len(host.turns)==2
            current=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service','taskRef':snapshot['taskRef']})['data']['items'][0]
            assert current['status']=='completed' and current['sessionId']==original_session
            endpoint=next(iter(provider.modules[0].execution.contexts.values()))
            assert set(endpoint.provider.tasks)==original_tasks
            selected,_,_=confirm(assistant,quote);reference=selected['data']['contractRef']
            wait_for(lambda:contract_state(assistant,reference).get('formed'))
            key,_=invoke(assistant,reference,'start');wait_for(lambda:operation(assistant,reference,key))
            wait_for(lambda:contract_state(assistant,reference).get('availablePeriods')==1)
            key,_=invoke(assistant,reference,'fetch',period=1);wait_for(lambda:operation(assistant,reference,key))
            key,pending=accept_preview(assistant,reference)
            assert assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']['report']['status']=='pass'
            user_decision(assistant,pending['data']['confirmationRef'])
            LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':reference,'period':1},operation_id=key)
            wait_for(lambda:operation(assistant,reference,key));wait_for(lambda:contract_state(assistant,reference).get('closed'))
            assert contract_state(assistant,reference)['payment']=='paid'
            assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
            protocol=assistant.modules[0].trading.protocol(assistant.modules[0].contracts.record(reference),original_interaction,quote['admissionId'])
            # The original client retains one proposal fee voucher; no second request was sent.
            assert len(protocol.store.all('requests'))==1
            assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))==1
            calls=[value for value in provider.store.checkpoints(endpoint.store.prefix+'calls').values()
                   if value.get('fingerprint')]
            assert calls and len([turn for turn in host.turns if json.loads(turn.input)['phase']=='quote'])==2
        finally:
            if fresh:fresh.close()


def test_lost_resume_reply_and_parallel_retransmission_keep_one_queue_receipt(platform,tmp_path,monkeypatch):
    with stopped(platform,tmp_path,monkeypatch) as (provider,assistant,host,_,_,reference,snapshot,_):
        original=provider.scheduler.requeue;calls=[];operation_id=str(uuid.uuid4())
        def lost(*args,**kwargs):
            result=original(*args,**kwargs);calls.append(result)
            raise RuntimeFault('unknown_result','resume acknowledgement lost')
        monkeypatch.setattr(provider.scheduler,'requeue',lost)
        assert resume(provider,snapshot,operation_id)['status']=='unknown'
        with ThreadPoolExecutor(max_workers=5) as pool:
            values=list(pool.map(lambda _:resume(provider,snapshot,operation_id),range(5)))
        assert all(value['status']=='accepted' for value in values),values
        assert all(value['data']['resumeReceipt']==calls[0] for value in values) and len(calls)==1
        recovered_quote(assistant,reference)
        assert len(host.turns)==2
        assert provider.store.db.execute('SELECT COUNT(*) FROM runtime_job_resumptions').fetchone()[0]==1


def test_resume_queue_and_receipt_rollback_together(platform,tmp_path,monkeypatch):
    with stopped(platform,tmp_path,monkeypatch) as (provider,assistant,host,_,_,reference,snapshot,_):
        service=provider.modules[0].execution.tasks;identity=provider.identities[0]
        raw=service.records(identity,snapshot['taskRef'])[0];allowed,_,proof=service.eligible(identity,raw);assert allowed
        operation_id=str(uuid.uuid4())
        provider.store.db.execute("CREATE TRIGGER interrupt_resume BEFORE UPDATE OF state ON runtime_jobs WHEN NEW.id='"+snapshot['taskRef']+"' AND NEW.state='queued' BEGIN SELECT RAISE(ABORT,'test atomic resume interruption'); END")
        try:
            with pytest.raises(sqlite3.IntegrityError):provider.scheduler.requeue(snapshot['taskRef'],operation_id,{'kind':'provider.verifier','proof':proof})
            assert provider.scheduler.get(snapshot['taskRef'])['status']=='unknown'
            assert provider.scheduler.resumption(identity.agent_id,operation_id,snapshot['taskRef']) is None
            assert len(host.turns)==1
        finally:provider.store.db.execute('DROP TRIGGER interrupt_resume')
        assert resume(provider,snapshot,operation_id)['status']=='accepted'
        recovered_quote(assistant,reference);assert len(host.turns)==2


def test_exception_without_a_completed_host_round_never_gets_a_resume_permit(platform,tmp_path,monkeypatch):
    host=ResumeHost();execute=host.execute
    def lost_result(turn):
        execute(turn);raise RuntimeError('original host outcome unknown')
    host.run=lost_result
    with stopped(platform,tmp_path,monkeypatch,host=host) as (provider,assistant,host,_,_,reference,snapshot,_):
        assert not snapshot['canResume'] and snapshot['resumeReason']=='original_model_result_unknown'
        assert resume(provider,snapshot)['status']=='unknown'
        for _ in range(3):provider.modules[0].execution.scan()
        assert len(host.turns)==1 and terminal(assistant,reference)['status']=='unknown'


def test_public_resume_checks_original_deadline_before_requeue(platform,tmp_path,monkeypatch):
    with stopped(platform,tmp_path,monkeypatch) as (provider,_,host,_,_,_,snapshot,_):
        endpoint=next(iter(provider.modules[0].execution.contexts.values()))
        monkeypatch.setattr(endpoint.provider,'now',lambda:utcnow()+timedelta(days=60))
        current=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service','taskRef':snapshot['taskRef']})
        assert not current['data']['items'][0]['canResume'] and current['data']['items'][0]['resumeReason']=='deadline-passed'
        assert resume(provider,snapshot)['status']=='unknown' and len(host.turns)==1


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_COMMAND'),reason='Opt-in original Jiuwen model invocation')
def test_original_jiuwen_resumes_the_original_quote_after_an_unknown_upload(platform,tmp_path,monkeypatch):
    from ioalab_jiuwen.process import ProcessHost
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],
        model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    host.turns=[];run=host.run
    def recorded(turn):host.turns.append(turn);return run(turn)
    host.run=recorded
    business={'resources':{},'instructions':
        '提供结构化摘要，实际交付文件固定名为result.json。业务要求的完整JSON示例是{"delivered":"actual file bytes"}；其中delivered字段的期望值是固定字符串字面量"actual file bytes"，不是文件内容本身。验收程序按真实文件名result.json读取JSON，检查字段值等于该固定字符串，不预置验收引用，不得只检查文件存在。'
        '交付报价固定9虚拟币，验收10分钟。输入信息完整，无须用户补充或外部操作；生产的files使用同一文件名result.json作为键，text中提供真实JSON字节。必须核对自测报告，修正成果后通过再交付。'}
    with stopped(platform,tmp_path,monkeypatch,host=host,business=business,timeout=180) as (provider,assistant,host,template,_,quote_ref,snapshot,uploads):
        assert snapshot['canResume'],snapshot
        original_session=snapshot['sessionId'];original_interaction=snapshot['interactionId']
        original_tasks=set(next(iter(provider.modules[0].execution.contexts.values())).provider.tasks)
        operation_id=str(uuid.uuid4());accepted=resume(provider,snapshot,operation_id)
        assert accepted['status']=='accepted',accepted
        quote=recovered_quote(assistant,quote_ref,timeout=180)
        current=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service','taskRef':snapshot['taskRef']})['data']['items'][0]
        assert current['sessionId']==original_session and current['status']=='completed'
        assert set(next(iter(provider.modules[0].execution.contexts.values())).provider.tasks)==original_tasks
        replay=resume(provider,snapshot,operation_id)
        assert replay['status']=='accepted' and replay['data']['resumeReceipt']==accepted['data']['resumeReceipt']
        selected,_,_=confirm(assistant,quote);reference=selected['data']['contractRef']
        wait_for(lambda:contract_state(assistant,reference).get('formed'),timeout=180)
        key,_=invoke(assistant,reference,'start');wait_for(lambda:operation(assistant,reference,key),timeout=180)
        wait_for(lambda:contract_state(assistant,reference).get('availablePeriods')==1,timeout=180)
        key,_=invoke(assistant,reference,'fetch',period=1);wait_for(lambda:operation(assistant,reference,key))
        key,pending=accept_preview(assistant,reference)
        report=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']['report']
        assert report['status']=='pass',report
        user_decision(assistant,pending['data']['confirmationRef'])
        LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':reference,'period':1},operation_id=key)
        wait_for(lambda:operation(assistant,reference,key));wait_for(lambda:contract_state(assistant,reference).get('closed'))
        actual=contract_state(assistant,reference)
        assert actual['payment']=='paid' and actual['interactionId']==original_interaction
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
        fees=platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id'])
        assert len(fees)==1
        quote_turns=[turn for turn in host.turns if json.loads(turn.input)['phase']=='quote']
        assert len(quote_turns)==2 and len({turn.session_id for turn in host.turns if turn.session_id})==1
        evidence={'scope':'Original Jiuwen public Process: actual unknown package upload, explicit public task continuation, same A2C task/request/fee/UUID/Session through WASM/AP2 close; isolated user confirmations',
            'model':host.capabilities()['model'],'taskRef':snapshot['taskRef'],'interactionId':original_interaction,
            'sessionId':original_session,'contractRef':reference,'quoteRounds':2,'resumeReceipt':accepted['data']['resumeReceipt'],
            'wasm':report,'proposalFeeCount':len(fees),'payment':'paid','closed':True,'reputationCompleted':1,
            'firstUnknownUpload':uploads[0],'limits':['Backend model and isolated owner actions; not native frontend onboarding or new film','No external mail or Linux deployment proof']}
        if os.environ.get('IOALAB_REAL_TASK_RECOVERY_REPORT'):
            from pathlib import Path
            from ioalab_sdk.runtime.files import private_json
            private_json(Path(os.environ['IOALAB_REAL_TASK_RECOVERY_REPORT']),evidence)
