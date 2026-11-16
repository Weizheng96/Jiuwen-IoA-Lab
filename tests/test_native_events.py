"""Native event public Store/Scheduler/registry seams; no model or shared ports."""
from pathlib import Path
import tempfile,time,threading
from types import SimpleNamespace
from ioalab_sdk.runtime import native_events as events, scheduling, host_registry as hosts, store as store_module
from ioalab_sdk.runtime.protocol import RuntimeFault,encode

def until(check):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        if check():return
        time.sleep(.01)
    raise AssertionError('Boundary condition did not converge')

class Transport:
    def __init__(self,ref='host'):self.ref=ref;self.sent=[];self.handled=set();self.queries={};self.execution_attempts={}
    def prepare_delivery(self,sid):return {'ready':True,'receiptStoreId':'boundary-store'}
    def deliver(self,op,sid,message,*,attempt=None):
        self.sent.append((op,sid));self.execution_attempts[op]=attempt
        return hosts.DeliveryReceipt(op,self.ref,sid,'accepted','queue-'+op)
    def delivery_status(self,op,sid,reference,*,expected_receipt_store_id=None):
        self.queries[op]=self.queries.get(op,0)+1
        attempt=self.execution_attempts.get(op)
        evidence=hosts.NativeExecutionEvidence(op,self.ref,sid,'request-'+op,attempt.nonce,attempt.receipt_store_id,'completed','machine-event-'+op) if op in self.handled and attempt else None
        return hosts.DeliveryReceipt(op,self.ref,sid,'handled' if op in self.handled else 'accepted',reference,evidence)


def test_permits_survive_restart_and_origin_target_does_not_become_iid(tmp_path):
    work=tmp_path
    with tempfile.TemporaryDirectory(dir=work) as directory:
        directory=Path(directory)
        store=store_module.RuntimeStore(directory/'runtime.sqlite','http://local',['actor'],owner_id='owner')
        transport=Transport();registry=hosts.HostRegistry({'host':transport},'host');registry.bind(store)
        runtime=SimpleNamespace(store=store,closing=threading.Event())
        service=events.NativeEvents(runtime,registry,poll_budget=.06,poll_interval=.01)
        runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=2).start()
        targets=[]
        try:
            for index in range(4):
                mid='mid-'+str(index)
                registry.record_origin('actor',mid,hosts.HostContext('host','sid-'+str(index),str(directory)))
                target=hosts.NativeEventTarget('actor',mid,'host-origins',encode(['actor',mid]));targets.append(target)
                service.publish(target,'result:'+mid,'message.result',{'outcome':'completed'},evidence_ref=mid,business_operation='original-'+mid)
            until(lambda:len(transport.sent)==2)
            until(lambda:runtime.scheduler.status()['states'].get('unknown')==2)
            time.sleep(.1)
            assert len(transport.sent)==2,'Unknown accepted native work released global permits'
            runtime.closing.set();runtime.scheduler.close();store.close()
            store=store_module.RuntimeStore(directory/'runtime.sqlite','http://local',['actor'],owner_id='owner')
            registry.bind(store);runtime.store=store;runtime.closing=threading.Event()
            service=events.NativeEvents(runtime,registry,poll_budget=.06,poll_interval=.01)
            runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=2).start()
            time.sleep(.1)
            assert len(transport.sent)==2,'Restart released uncertain native model permits'
            recovery_clock=[time.time()+60];service.clock=lambda:recovery_clock[0]
            service.recover();query_counts=dict(transport.queries);service.recover()
            assert transport.queries==query_counts,'Unknown recovery ignored durable machine-query backoff'
            recovery_clock[0]+=60
            first=transport.sent[0][0];transport.handled.add(first);service.recover()
            until(lambda:len(transport.sent)==3)
            service.publish(targets[0],'result:mid-0','message.result',{'outcome':'completed'},evidence_ref='mid-0',business_operation='original-mid-0')
            assert sum(op==first for op,sid in transport.sent)==1,'Replay sent again'
            # Origin targets have no protocol interaction and cannot drift to the current default.
            target_first=next(target for target in targets if store.checkpoint('native-events',encode(['actor',first]))['target']['business_ref']==target.business_ref)
            original_mid=target_first.business_ref
            assert store.interaction(origin=original_mid) is None
            store.prepare('actor','message.send',{'messageId':original_mid,'recipient':'peer','kind':'message.request','body':{'interfaceName':'echo','revision':1}},'business-op')
            store.recover_link('actor',{'interactionId':'real-iid','sender':'actor','recipient':'peer','originMessageId':original_mid,'interfaceName':'echo','revision':1,'deadline':'2099-01-01T00:00:00Z','maxRequests':8})
            assert registry.origin_delivery_status(target_first,first,'queue-'+first,interaction='real-iid').status=='handled'
            assert sum(op==first for op,sid in transport.sent)==1
            bad=hosts.NativeEventTarget('another','mid-0','host-origins',encode(['actor','mid-0']))
            try:registry.origin_context(bad)
            except RuntimeFault:pass
            else:raise AssertionError('Cross-actor source accepted')
            external='external'
            store.record_host_origin('actor',external,{'hostRef':'host','sessionId':'external-sid','workspace':str(directory),'source':'external'})
            try:registry.origin_context(hosts.NativeEventTarget('actor',external,'host-origins',encode(['actor',external])))
            except RuntimeFault:pass
            else:raise AssertionError('External source double-delivered')
        finally:
            runtime.closing.set();runtime.scheduler.close();store.close()

def test_same_sid_unknown_blocks_without_blocking_other_hosts(tmp_path):
    work=tmp_path
    with tempfile.TemporaryDirectory(dir=work) as directory:
        directory=Path(directory)
        store=store_module.RuntimeStore(directory/'runtime.sqlite','http://local',['actor'],owner_id='owner')
        first,other=Transport(),Transport('other')
        registry=hosts.HostRegistry({'host':first,'other':other},'host');registry.bind(store)
        runtime=SimpleNamespace(store=store,closing=threading.Event())
        service=events.NativeEvents(runtime,registry,poll_budget=.05,poll_interval=.01)
        runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=4).start()
        try:
            for index,host in enumerate(['host','host','other']):
                mid='same-'+str(index)
                registry.record_origin('actor',mid,hosts.HostContext(host,'same-sid',str(directory)))
                target=hosts.NativeEventTarget('actor',mid,'host-origins',encode(['actor',mid]))
                service.publish(target,'terminal:'+mid,'message.result',{'outcome':'completed'},evidence_ref=mid,business_operation=mid)
            until(lambda:len(first.sent)==1 and len(other.sent)==1)
            until(lambda:runtime.scheduler.status()['states'].get('unknown')==2)
            time.sleep(.1)
            assert len(first.sent)==1,'Same native session bypassed its unresolved event'
            assert len(other.sent)==1,'Other host with equal SID was incorrectly blocked'
        finally:runtime.closing.set();runtime.scheduler.close();store.close()

def test_not_started_resumes_original_operation(tmp_path):
    work=tmp_path
    with tempfile.TemporaryDirectory(dir=work) as directory:
        directory=Path(directory)
        store=store_module.RuntimeStore(directory/'runtime.sqlite','http://local',['actor'],owner_id='owner')
        class Preparing(Transport):
            ready=False
            reject_once=True
            attempts=[]
            preparations=0
            def prepare_delivery(self,sid):
                self.preparations+=1
                return {'ready':self.ready,'receiptStoreId':'boundary-store'}
            def deliver(self,op,sid,message,*,attempt=None):
                self.attempts.append(attempt)
                if self.reject_once:
                    self.reject_once=False
                    raise hosts.DeliveryNotStarted(op,sid,attempt)
                self.handled.add(op)
                receipt=super().deliver(op,sid,message,attempt=attempt)
                return self.delivery_status(op,sid,receipt.reference,expected_receipt_store_id=attempt.receipt_store_id)
        transport=Preparing();registry=hosts.HostRegistry({'host':transport},'host');registry.bind(store)
        runtime=SimpleNamespace(store=store,closing=threading.Event())
        service=events.NativeEvents(runtime,registry,poll_budget=.05,poll_interval=.005)
        runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=1).start()
        try:
            registry.record_origin('actor','prepare-mid',hosts.HostContext('host','sid',str(directory)))
            target=hosts.NativeEventTarget('actor','prepare-mid','host-origins',encode(['actor','prepare-mid']))
            event=service.publish(target,'prepare-terminal','message.result',{'outcome':'completed'},evidence_ref='mid',business_operation='business')
            assert not transport.attempts and not transport.sent
            preparation_count=transport.preparations
            service.recover();assert transport.preparations==preparation_count,'Readiness scan ignored persisted backoff'
            assert not transport.attempts
            transport.ready=True;clock=[time.time()+60];service.clock=lambda:clock[0];service.recover()
            until(lambda:runtime.scheduler.status()['states'].get('waiting')==1)
            assert not transport.sent,'Machine-proven not_started was dispatched'
            original=event['eventRef'];reference=runtime.scheduler.reference('actor',service.handler,original)
            clock[0]+=60;service.recover()
            until(lambda:runtime.scheduler.get(reference)['status']=='completed')
            assert len(transport.sent)==1 and transport.sent[0][0]==original
            assert transport.attempts[0].nonce!=transport.attempts[1].nonce
            assert transport.attempts[0].target_digest==transport.attempts[1].target_digest
        finally:runtime.closing.set();runtime.scheduler.close();store.close()

def test_changed_receipt_store_cannot_wash_unknown(tmp_path):
    work=tmp_path
    with tempfile.TemporaryDirectory(dir=work) as directory:
        directory=Path(directory)
        store=store_module.RuntimeStore(directory/'runtime.sqlite','http://local',['actor'],owner_id='owner')
        class Uncertain(Transport):
            identity='boundary-store'
            calls=0
            def prepare_delivery(self,sid):return {'ready':True,'receiptStoreId':self.identity}
            def deliver(self,op,sid,message,*,attempt=None):
                self.calls+=1
                raise TimeoutError('Queue write result is uncertain')
        transport=Uncertain();registry=hosts.HostRegistry({'host':transport},'host');registry.bind(store)
        runtime=SimpleNamespace(store=store,closing=threading.Event())
        service=events.NativeEvents(runtime,registry,poll_budget=.03,poll_interval=.005)
        runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=1).start()
        try:
            registry.record_origin('actor','uncertain-mid',hosts.HostContext('host','sid',str(directory)))
            target=hosts.NativeEventTarget('actor','uncertain-mid','host-origins',encode(['actor','uncertain-mid']))
            event=service.publish(target,'uncertain-terminal','message.result',{'outcome':'completed'},evidence_ref='mid',business_operation='business')
            until(lambda:runtime.scheduler.status()['states'].get('unknown')==1)
            transport.identity='new-empty-store';service.clock=lambda:time.time()+60
            try:service.recover()
            except RuntimeFault:pass
            assert transport.calls==1,'New empty receipt store washed an uncertain claim'
            service.publish(target,'uncertain-terminal','message.result',{'outcome':'completed'},evidence_ref='mid',business_operation='business')
            assert transport.calls==1
        finally:runtime.closing.set();runtime.scheduler.close();store.close()

# Host receipt ACK and native machine termination are deliberately distinct facts.
def test_handled_without_machine_terminal_keeps_model_permit(tmp_path):
    from dataclasses import replace
    class AckOnly(Transport):
        terminal=False
        def deliver(self,op,sid,message,*,attempt=None):
            value=super().deliver(op,sid,message,attempt=attempt);self.handled.add(op)
            return hosts.DeliveryReceipt(op,self.ref,sid,'handled',value.reference)
        def delivery_status(self,op,sid,reference,*,expected_receipt_store_id=None):
            value=super().delivery_status(op,sid,reference,expected_receipt_store_id=expected_receipt_store_id)
            return value if self.terminal else replace(value,execution=None)
    store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=AckOnly();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store)
    runtime=SimpleNamespace(store=store,closing=threading.Event())
    service=events.NativeEvents(runtime,registry,poll_budget=.03,poll_interval=.005)
    runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=1).start()
    try:
        for index in range(2):
            mid='mid-'+str(index);registry.record_origin('actor',mid,hosts.HostContext('host','sid-'+str(index),str(tmp_path)))
            service.publish(hosts.NativeEventTarget('actor',mid,'host-origins',encode(['actor',mid])),mid,'message.result',{'outcome':'completed'},evidence_ref=mid,business_operation=mid)
        until(lambda:runtime.scheduler.status()['states'].get('unknown')==1)
        assert len(host.sent)==1
        op=host.sent[0][0]
        receipt=store.checkpoint('host-deliveries',encode(['actor',op]))['receipt']
        assert receipt['status']=='handled' and receipt['execution'] is None
        runtime.closing.set();runtime.scheduler.close();store.close()
        store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
        registry.bind(store);runtime.store=store;runtime.closing=threading.Event()
        service=events.NativeEvents(runtime,registry,poll_budget=.03,poll_interval=.005)
        runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=1).start()
        service.clock=lambda:time.time()+60;service.recover()
        assert len(host.sent)==1
        host.terminal=True;service.clock=lambda:time.time()+120;service.recover()
        until(lambda:len(host.sent)==2)
    finally:runtime.closing.set();runtime.scheduler.close();store.close()


def test_machine_queries_use_bounded_exponential_backoff(tmp_path):
    class Clock:
        now=0.
        waits=[]
        def wait(self,seconds):self.waits.append(seconds);self.now+=seconds;return False
    clock=Clock();store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Transport();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store)
    runtime=SimpleNamespace(store=store,closing=clock)
    service=events.NativeEvents(runtime,registry,poll_budget=70)
    service.monotonic=lambda:clock.now;service.clock=lambda:clock.now
    runtime.scheduler=scheduling.Scheduler(store,service.handlers(),max_models=1)
    try:
        registry.record_origin('actor','mid',hosts.HostContext('host','sid',str(tmp_path)))
        target=hosts.NativeEventTarget('actor','mid','host-origins',encode(['actor','mid']))
        event=service.publish(target,'terminal','message.result',{'outcome':'completed'},evidence_ref='mid',business_operation='mid')
        outcome=service.handlers()[service.handler].run({'actor':'actor','payload':{'eventRef':event['eventRef']}})
        assert outcome.state=='unknown'
        assert clock.waits==[1.,2.,4.,8.,16.,30.,9.]
        assert host.queries[event['eventRef']]==6
        assert len(host.sent)==1
    finally:runtime.scheduler.close();store.close()

import pytest
from dataclasses import replace

@pytest.mark.parametrize('field',['nonce','receipt_store_id','target_digest'])
def test_wrong_not_started_attempt_never_permits_retry(tmp_path,field):
    class InvalidProof(Transport):
        attempts=0
        def deliver(self,op,sid,message,*,attempt=None):
            self.attempts+=1
            raise hosts.DeliveryNotStarted(op,sid,replace(attempt,**{field:'another-attempt'}))
    store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=InvalidProof();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store)
    try:
        registry.record_origin('actor','mid',hosts.HostContext('host','sid',str(tmp_path)))
        target=hosts.NativeEventTarget('actor','mid','host-origins',encode(['actor','mid']))
        with pytest.raises(RuntimeFault):registry.deliver_origin(target,'original','facts')
        assert registry.deliver_origin(target,'original','facts').status=='unknown'
        assert host.attempts==1
    finally:store.close()


@pytest.mark.parametrize('field',['attempt_nonce','receipt_store_id','session_id'])
def test_wrong_machine_execution_proof_never_upgrades_receipt(tmp_path,field):
    class InvalidMachineProof(Transport):
        calls=0
        def deliver(self,op,sid,message,*,attempt=None):
            self.calls+=1
            proof=hosts.NativeExecutionEvidence(op,self.ref,sid,'request',attempt.nonce,attempt.receipt_store_id,'completed','machine-event')
            return hosts.DeliveryReceipt(op,self.ref,sid,'handled','queue',replace(proof,**{field:'unrelated'}))
    store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=InvalidMachineProof();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store)
    try:
        registry.record_origin('actor','mid',hosts.HostContext('host','sid',str(tmp_path)))
        target=hosts.NativeEventTarget('actor','mid','host-origins',encode(['actor','mid']))
        with pytest.raises(RuntimeFault):registry.deliver_origin(target,'original','facts')
        assert registry.deliver_origin(target,'original','facts').status=='unknown'
        assert host.calls==1
    finally:store.close()


def test_machine_request_reference_is_immutable(tmp_path):
    class Finished(Transport):
        def deliver(self,op,sid,message,*,attempt=None):
            super().deliver(op,sid,message,attempt=attempt);self.handled.add(op)
            return self.delivery_status(op,sid,'queue',expected_receipt_store_id=attempt.receipt_store_id)
    store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Finished();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store)
    try:
        registry.record_origin('actor','mid',hosts.HostContext('host','sid',str(tmp_path)))
        target=hosts.NativeEventTarget('actor','mid','host-origins',encode(['actor','mid']))
        original=registry.deliver_origin(target,'original','facts')
        query=host.delivery_status
        def changed(op,sid,reference,*,expected_receipt_store_id):
            receipt=query(op,sid,reference,expected_receipt_store_id=expected_receipt_store_id)
            return replace(receipt,execution=replace(receipt.execution,request_ref='new-request'))
        host.delivery_status=changed
        with pytest.raises(RuntimeFault):registry.origin_delivery_status(target,'original','queue')
        assert registry.deliver_origin(target,'original','facts').execution==original.execution
    finally:store.close()

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
    host=Transport();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store)
    runtime=SimpleNamespace(store=store,closing=threading.Event())
    service=events.NativeEvents(runtime,registry,poll_budget=.02,poll_interval=.01)
    runtime.scheduler=scheduling.Scheduler(store,service.handlers())
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
        registry.bind(store);service=events.NativeEvents(runtime,registry,poll_budget=.02,poll_interval=.01)
        runtime.scheduler=scheduling.Scheduler(store,service.handlers())
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


def test_preparation_failure_is_visible_without_claim_and_recovers_original_event(tmp_path):
    class Preparing(Transport):
        ready=False
        def prepare_delivery(self,sid):
            if not self.ready:raise OSError('fixture private transport failure must not appear in status')
            return super().prepare_delivery(sid)
    store=store_module.RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Preparing();registry=hosts.HostRegistry({'host':host},'host');registry.bind(store)
    runtime=SimpleNamespace(store=store,closing=threading.Event())
    service=events.NativeEvents(runtime,registry,poll_budget=.02,poll_interval=.01)
    runtime.scheduler=scheduling.Scheduler(store,service.handlers()).start()
    try:
        registry.record_origin('actor','mid',hosts.HostContext('host','sid',str(tmp_path)))
        target=hosts.NativeEventTarget('actor','mid','host-origins',encode(['actor','mid']))
        result=service.publish(target,'original-result','message.result',{'outcome':'completed'},evidence_ref='mid',business_operation='original')
        operation=result['eventRef']
        assert service.status()=={'pending':1,'diagnosticSamples':[{'eventRef':operation,'code':'not_ready'}]}
        assert store.checkpoint('host-deliveries',encode(['actor',operation])) is None and not host.sent
        host.ready=True;service.clock=lambda:time.time()+60;service.recover()
        until(lambda:len(host.sent)==1)
        assert host.sent[0][0]==operation and not service.status()['diagnosticSamples']
    finally:runtime.closing.set();runtime.scheduler.close();store.close()
