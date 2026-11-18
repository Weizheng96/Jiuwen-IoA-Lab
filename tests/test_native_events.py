"""Original-source notices through the one S01/P20 Store/Scheduler path."""
from dataclasses import replace
from pathlib import Path
import threading,time
from types import SimpleNamespace
import pytest
from ioalab_sdk.runtime import (NativeEvents,NativeEventTarget,HostRegistry,HostContext,HostRegistration,
    SubmissionResult,SubmissionEvent,SubmissionError,MachineTerminal,BindingAck,RuntimeStore,RuntimeFault,encode)
from ioalab_sdk.runtime.scheduling import Scheduler
from conftest import wait_for


class Transport:
    registration=HostRegistration('fixture','1',('get_request',))
    def __init__(self,ref='host'):
        self.ref=ref;self.sent=[];self.receipts={};self.queries={};self.finished={};self.ack_only=False;self.not_ready=False;self.attempts=[]
    def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        assert execution is None and input.attempt.actor=='actor' and input.attempt.profile=='personal'
        self.attempts.append(input.attempt)
        if self.not_ready:return SubmissionResult(op,input.attempt.nonce,'waiting',session_id,error=SubmissionError('not_ready','native_transport','not_submitted','fixture not dispatched'))
        self.sent.append((op,session_id))
        receipt=SubmissionResult(op,input.attempt.nonce,'accepted',session_id,'request-'+op,'handled' if self.ack_only else 'accepted')
        assert on_event(SubmissionEvent(input.attempt.nonce+':session','session',receipt))==BindingAck(session_id,receipt.request_id)
        self.receipts[receipt.request_id]=receipt
        return receipt
    def get_request(self,query):
        request=query.request_id
        self.queries[request]=self.queries.get(request,0)+1
        receipt=self.receipts[request]
        state=self.finished.get(receipt.operation_id)
        if not state:return receipt
        terminal=MachineTerminal(receipt.operation_id,receipt.attempt_id,receipt.session_id,request,state,'machine-end-'+receipt.operation_id)
        return replace(receipt,state=state,terminal=terminal,delivery='handled' if state=='completed' else receipt.delivery)


def assemble(store,hosts,*,default='host',max_models=2,budget=.04):
    registry=HostRegistry(hosts,default);registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='personal')])
    runtime=SimpleNamespace(store=store,closing=threading.Event(),native_entry_required=False)
    service=NativeEvents(runtime,registry,poll_budget=budget,poll_interval=.01)
    runtime.scheduler=Scheduler(store,service.handlers(),max_models=max_models)
    registry.scheduler=runtime.scheduler
    return registry,runtime,service


def publish(registry,service,root,mid,*,host='host',sid=None):
    registry.record_origin('actor',mid,HostContext(host,sid or 'sid-'+mid,str(root)))
    target=NativeEventTarget('actor',mid,'host-origins',encode(['actor',mid]))
    result=service.publish(target,'result:'+mid,'business.result',{'outcome':'completed'},evidence_ref=mid,business_operation='original-'+mid)
    return target,result['eventRef']


def finish(runtime):runtime.closing.set();runtime.scheduler.close()


def test_permits_survive_restart_and_origin_target_does_not_become_iid(tmp_path):
    path=tmp_path/'runtime.sqlite';store=RuntimeStore(path,'http://local',['actor'],owner_id='owner');host=Transport()
    registry,runtime,service=assemble(store,{'host':host});runtime.scheduler.start()
    try:
        targets=[publish(registry,service,tmp_path,'mid-'+str(index)) for index in range(4)]
        wait_for(lambda:runtime.scheduler.status()['states'].get('unknown')==2)
        assert len(host.sent)==2
        finish(runtime);store.close();store=RuntimeStore(path,'http://local',['actor'],owner_id='owner')
        registry,runtime,service=assemble(store,{'host':host});runtime.scheduler.start()
        first=host.sent[0][0];host.finished[first]='completed'
        service.clock=lambda:time.time()+60;service.recover()
        # A new process has no actual old callback drain proof; machine end alone
        # does not recreate the missing scope or allow a second wave of models.
        assert len(host.sent)==2 and runtime.scheduler.status()['states'].get('unknown')==2
        target=next(t for t,op in targets if op==first)
        original=store.checkpoint('native-events',encode(['actor',first]))
        service.publish(target,'result:'+target.business_ref,'business.result',{'outcome':'completed'},evidence_ref=target.business_ref,business_operation='original-'+target.business_ref)
        assert store.interaction(origin=target.business_ref) is None
        assert store.checkpoint('native-events',encode(['actor',first]))==original
        assert sum(op==first for op,_ in host.sent)==1
    finally:finish(runtime);store.close()


def test_same_sid_unknown_blocks_without_blocking_other_hosts(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');first,other=Transport(),Transport('other')
    registry,runtime,service=assemble(store,{'host':first,'other':other},max_models=4);runtime.scheduler.start()
    try:
        for index,ref in enumerate(('host','host','other')):publish(registry,service,tmp_path,'mid-'+str(index),host=ref,sid='same-sid')
        wait_for(lambda:len(first.sent)==len(other.sent)==1)
        wait_for(lambda:runtime.scheduler.status()['states'].get('unknown')==2)
        assert runtime.scheduler.status()['states'].get('queued')==1
    finally:finish(runtime);store.close()


def test_not_submitted_resumes_original_operation_and_attempt_history(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');host=Transport();host.not_ready=True
    registry,runtime,service=assemble(store,{'host':host});runtime.scheduler.start()
    try:
        _,op=publish(registry,service,tmp_path,'mid')
        job=runtime.scheduler.reference('actor',service.handler,op)
        wait_for(lambda:runtime.scheduler.get(job)['status']=='waiting')
        assert not host.sent and len(host.attempts)==1
        service.recover();assert len(host.attempts)==1
        host.not_ready=False;service.clock=lambda:time.time()+60;service.recover()
        wait_for(lambda:len(host.sent)==1)
        assert host.sent[0][0]==op and len(host.attempts)==2
        assert host.attempts[0].nonce!=host.attempts[1].nonce
        assert host.attempts[0].scope_digest==host.attempts[1].scope_digest
    finally:finish(runtime);store.close()


def test_handled_without_machine_terminal_keeps_model_permit(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');host=Transport();host.ack_only=True
    registry,runtime,service=assemble(store,{'host':host},max_models=1);runtime.scheduler.start()
    try:
        _,first=publish(registry,service,tmp_path,'first');publish(registry,service,tmp_path,'second')
        job=runtime.scheduler.reference('actor',service.handler,first)
        wait_for(lambda:runtime.scheduler.get(job)['status']=='unknown')
        assert len(host.sent)==1
        receipt=runtime.scheduler.get(job)['data']['receipt']
        assert receipt['delivery']=='handled' and receipt['terminal'] is None
        host.finished[first]='completed';service.clock=lambda:time.time()+60;service.recover()
        wait_for(lambda:len(host.sent)==2)
        assert runtime.scheduler.get(job)['status']=='completed'
    finally:finish(runtime);store.close()


@pytest.mark.parametrize('terminal',['failed','cancelled'])
def test_machine_failure_releases_notice_permit_without_claiming_handled_or_business_success(tmp_path,terminal):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');host=Transport()
    registry,runtime,service=assemble(store,{'host':host},max_models=1);runtime.scheduler.start()
    try:
        _,op=publish(registry,service,tmp_path,'first');publish(registry,service,tmp_path,'second')
        job=runtime.scheduler.reference('actor',service.handler,op)
        wait_for(lambda:runtime.scheduler.get(job)['status']=='unknown')
        host.finished[op]=terminal;service.clock=lambda:time.time()+60;service.recover()
        wait_for(lambda:len(host.sent)==2)
        result=runtime.scheduler.get(job)
        assert result['status']=='failed' and result['data']['reason']=='native_'+terminal
        assert result['data']['receipt']['delivery']=='accepted'
        assert store.checkpoint('native-events',encode(['actor',op]))['notice']['facts']=={'outcome':'completed'}
        service.recover();assert sum(sent==op for sent,_ in host.sent)==1
    finally:finish(runtime);store.close()


def test_machine_queries_use_bounded_exponential_backoff(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');host=Transport()
    registry,runtime,service=assemble(store,{'host':host},budget=.08);runtime.scheduler.start()
    try:
        _,op=publish(registry,service,tmp_path,'mid');job=runtime.scheduler.reference('actor',service.handler,op)
        wait_for(lambda:runtime.scheduler.get(job)['status']=='unknown')
        assert 1<=sum(host.queries.values())<=3 and len(host.sent)==1
        service.clock=lambda:time.time()+60;service.recover();count=dict(host.queries)
        service.recover();assert host.queries==count
    finally:finish(runtime);store.close()


def test_missing_adapter_is_visible_without_claim_and_recovers_original_event(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    registry,runtime,service=assemble(store,{'host':object()});runtime.scheduler.start()
    try:
        _,op=publish(registry,service,tmp_path,'mid')
        assert service.status()=={'pending':1,'diagnosticSamples':[{'eventRef':op,'code':'not_ready'}]}
        assert store.checkpoint('host-deliveries',encode(['actor',op])) is None
        host=Transport();registry.hosts['host']=host;service.clock=lambda:time.time()+60;service.recover()
        wait_for(lambda:len(host.sent)==1)
        assert host.sent[0][0]==op and not service.status()['diagnosticSamples']
    finally:finish(runtime);store.close()


from ioalab_sdk.runtime import native_events as events, scheduling, host_registry as hosts, store as store_module
until=wait_for

def test_request_evidence_and_checkpoint_pages_are_bounded(tmp_path):
    store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    try:
        for seq in range(1,801):
            store.receive({'type':'message.deliver','agentId':'actor','recipientSeq':seq,
                           'message':{'messageId':'noise-'+str(seq),'recipient':'actor','recipientSeq':seq,
                                      'kind':'message.request','body':{}}})
        store.receive({'type':'message.deliver','agentId':'actor','recipientSeq':801,
                       'message':{'messageId':'result','recipient':'actor','recipientSeq':801,'kind':'message.result',
                                  'inReplyTo':'original','body':{'outcome':'completed','output':{'answer':42}}}})
        work=[0]
        def bounded():work[0]+=1;return work[0]>100
        # A public indexed read must remain cheap even with a large unrelated inbox.
        with store.transaction() as connection:
            connection.set_progress_handler(bounded,50)
            try:evidence=store.request_evidence('actor','original','relation')
            finally:connection.set_progress_handler(None,0)
        assert evidence['reply']['body']['output']=={'answer':42} and not evidence['closed']
        store.checkpoint_update('page',{str(i).zfill(4):{'index':i} for i in range(300)})
        page=store.checkpoint_page('page',after='0099',limit=32)
        assert len(page)==32 and page[0][1]['index']==100 and page[-1][1]['index']==131
        assert [frame['recipientSeq'] for frame in store.messages_after('actor',799,limit=1)]==[800]
        with pytest.raises(RuntimeFault):store.messages_after('foreign',0)
        with pytest.raises(RuntimeFault):store.checkpoint_page('page',limit=1000)
    finally:store.close()


@pytest.mark.parametrize('interruption',['before_publish','after_publish'])
def test_inactive_flow_receipt_recovers_after_callback_interruption_without_history_scan(tmp_path,interruption):
    from ioalab_sdk.runtime import FlowJournal
    path=tmp_path/'runtime.sqlite'
    store=store_module.RuntimeStore(path,'http://local',['actor'],owner_id='owner')
    host=Transport();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="personal")])
    runtime=SimpleNamespace(store=store,closing=threading.Event(),native_entry_required=False)
    service=events.NativeEvents(runtime,registry,poll_budget=.02,poll_interval=.01)
    runtime.scheduler=scheduling.Scheduler(store,service.handlers());registry.scheduler=runtime.scheduler
    registry.record_origin('actor','mid',hosts.HostContext('host','sid',str(tmp_path)))
    target=hosts.NativeEventTarget('actor','mid','host-origins',encode(['actor','mid']))
    state=['waiting'];seen=[]
    def advance(journal,record):
        record['active']=state[0]=='waiting'
        return journal.receipt(record,state[0],'fixture_real_receipt')
    def project(journal,record,result):
        event=service.publish(target,'original-result','message.result',{'outcome':'completed'},evidence_ref=record['flowRef'],business_operation='original')
        seen.append(event['eventRef'])
    def interrupted(*args):
        if interruption=='after_publish':project(*args)
        raise RuntimeError('fixture process interruption around projection')
    journal=FlowJournal(runtime,'actor','messaging',advance,on_async_receipt=interrupted)
    try:
        initial=journal.call('original',{'action':'begin'},{'begin'})
        ref=initial['data']['flowRef'];state[0]='completed'
        with pytest.raises(RuntimeError):journal.scan()
        assert store.checkpoint(journal.namespace,ref)['active'] is False
        store.checkpoint_update(journal.namespace,{'history-'+str(i):{'active':False} for i in range(800)})
        runtime.scheduler.close();store.close()
        store=store_module.RuntimeStore(path,'http://local',['actor'],owner_id='owner');runtime.store=store
        registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="personal")]);service=events.NativeEvents(runtime,registry,poll_budget=.02,poll_interval=.01)
        runtime.scheduler=scheduling.Scheduler(store,service.handlers());registry.scheduler=runtime.scheduler
        recovered=FlowJournal(runtime,'actor','messaging',advance,on_async_receipt=project)
        work=[0]
        def bounded():work[0]+=1;return work[0]>100
        store.db.set_progress_handler(bounded,50)
        try:recovered.scan()
        finally:store.db.set_progress_handler(None,0)
        assert seen and len(set(seen))==1
        count=len(seen);recovered.scan();assert len(seen)==count
        runtime.scheduler.start();until(lambda:len(host.sent)==1)
        assert host.sent[0][0]==seen[0]
    finally:runtime.closing.set();runtime.scheduler.close();store.close()



def test_early_wakeup_does_not_advance_machine_queries_or_reset_capped_backoff(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');now=[0.];queried=[]
    class Machine(Transport):
        def get_request(self,query):queried.append(now[0]);return super().get_request(query)
    class EarlyWake:
        early=True
        def wait(self,duration):
            if self.early:self.early=False;now[0]+=duration/2
            else:now[0]+=duration
            return False
        def set(self):pass
    host=Machine();registry,runtime,service=assemble(store,{'host':host})
    runtime.closing=EarlyWake();service.monotonic=lambda:now[0];service.poll_budget=90;service.poll_interval=1.
    runtime.scheduler.start()
    try:
        _,op=publish(registry,service,tmp_path,'mid');job=runtime.scheduler.reference('actor',service.handler,op)
        wait_for(lambda:runtime.scheduler.get(job)['status']=='unknown')
        assert queried==[1.,3.,7.,15.,31.,61.]
        assert len(host.sent)==1 and now[0]==90.
    finally:finish(runtime);store.close()
