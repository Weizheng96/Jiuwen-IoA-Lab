"""Native-origin event delivery over the existing Store/Scheduler/host receipt path."""
from dataclasses import asdict
import json
import time
import uuid
from .host_registry import NativeEventTarget, DeliveryNotStarted, source_value
from .protocol import RuntimeFault, encode
from .scheduling import JobHandler, JobOutcome


class NativeEvents:
    namespace='native-events'
    handler='runtime.native_event'

    def __init__(self,runtime,registry,*,poll_budget=30,poll_interval=1.):
        self.runtime,self.registry,self.store=runtime,registry,runtime.store
        self.poll_budget,self.poll_interval=poll_budget,poll_interval
        self.cursor=''
        self.monotonic=time.monotonic
        self.clock=time.time

    def handlers(self):
        return {self.handler:JobHandler(self.run,'native-event-v1',True,retain_model_on_unknown=True)}

    def publish(self,target,event_key,kind,facts,*,evidence_ref,business_operation,interaction=None):
        context=source_value(self.registry.origin_context(target,interaction=interaction))
        operation=str(uuid.uuid5(uuid.NAMESPACE_URL,encode(['native-event',target.actor,event_key])))
        notice={'version':1,'eventRef':operation,'eventKind':kind,'actor':target.actor,
                'businessRef':target.business_ref,'businessOperationRef':business_operation,
                'evidenceRef':evidence_ref,'facts':facts,'isAuthorization':False}
        record={'actor':target.actor,'operation':operation,'target':asdict(target),'context':context,'notice':notice}
        key=encode([target.actor,operation])
        with self.store.transaction() as conn:
            old=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,key)).fetchone()
            if old and old['value']!=encode(record):
                raise RuntimeFault('identity_conflict','Original notice facts or native source changed')
            conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',(self.namespace,key,encode(record)))
            conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',('native-event-pending',key,encode({'actor':target.actor,'operation':operation})))
        self._submit(record)
        return {'eventRef':operation,'state':'queued'}

    def _submit(self,record):
        reference=self.runtime.scheduler.reference(record['actor'],self.handler,record['operation'])
        try:self.runtime.scheduler.get(reference);return
        except RuntimeFault:pass
        if not self._claim_recovery(record):return
        host=self.registry.hosts[record['context']['hostRef']]
        # A missing transport has no side effect and cannot consume a model permit.
        if not callable(getattr(host,'deliver',None)) or not callable(getattr(host,'delivery_status',None)):
            self._diagnose(record,'not_ready');return
        try:self.registry.origin_readiness(NativeEventTarget(**record['target']))
        except RuntimeFault as exc:
            self._diagnose(record,exc.code);return
        self.runtime.scheduler.submit(record['actor'],record['operation'],self.handler,
                                      {'eventRef':record['operation']},operation_id=record['operation'],
                                      session=record['context']['sessionId'])
        self._clear_diagnostic(record)

    def _diagnose(self,record,code):
        # Adapter exception text and private transport identities are never public status.
        safe=code if code in ('not_ready','identity_conflict','id_conflict','unknown','invalid_input') else 'runtime_error'
        self.store.checkpoint_set('native-event-errors',encode([record['actor'],record['operation']]),
                                  {'eventRef':record['operation'],'code':safe})

    def _clear_diagnostic(self,record):
        self.store.checkpoint_delete('native-event-errors',encode([record['actor'],record['operation']]))

    def status(self):
        with self.store.transaction() as conn:
            pending=conn.execute('SELECT count(*) FROM runtime_checkpoints WHERE namespace=?',('native-event-pending',)).fetchone()[0]
        return {'pending':pending,'diagnosticSamples':[value for _,value in self.store.checkpoint_page('native-event-errors')]}

    def _record(self,actor,operation):
        record=self.store.checkpoint(self.namespace,encode([actor,operation]))
        if not record:raise RuntimeFault('not_ready','Restore the original event')
        target=NativeEventTarget(**record['target'])
        if source_value(self.registry.origin_context(target))!=record['context']:
            raise RuntimeFault('identity_conflict','Original notice native source changed')
        return record,target

    def run(self,job):
        record,target=self._record(job['actor'],job['payload']['eventRef'])
        try:receipt=self.registry.deliver_origin(target,record['operation'],encode(record['notice']))
        except RuntimeFault as exc:
            if exc.code!='not_ready' or self.store.checkpoint('host-deliveries',encode([record['actor'],record['operation']])):
                raise
            # _delivery_to swallows callback faults into unknown receipts; this path
            # is exclusively a readiness failure before the first durable claim/RPC.
            self.store.checkpoint_set('native-event-preparation',encode([record['actor'],record['operation']]),{'notStarted':True})
            self._diagnose(record,'not_ready')
            return JobOutcome('waiting',{'eventRef':record['operation'],'reason':'native_preparation'},record['context']['sessionId'])
        except DeliveryNotStarted:
            self._diagnose(record,'not_ready')
            return JobOutcome('waiting',{'eventRef':record['operation'],'reason':'native_not_started'},record['context']['sessionId'])
        self._clear_diagnostic(record)
        deadline=self.monotonic()+self.poll_budget
        # Do not return waiting after accepted: the native queue may start a model later.
        # Retain this Scheduler model permit through machine-only queries and uncertain exit.
        delay=self.poll_interval
        while receipt.status!='handled' or receipt.execution is None:
            remaining=deadline-self.monotonic()
            if remaining<=0 or self.runtime.closing.wait(min(delay,remaining)):
                return JobOutcome('unknown',{'eventRef':record['operation'],'receipt':asdict(receipt)},receipt.session_id)
            if self.monotonic()>=deadline:
                return JobOutcome('unknown',{'eventRef':record['operation'],'receipt':asdict(receipt)},receipt.session_id)
            receipt=self.registry.origin_delivery_status(target,record['operation'],receipt.reference)
            delay=min(30.,delay*2)
        return JobOutcome('completed',{'eventRef':record['operation'],'receipt':asdict(receipt)},receipt.session_id)

    def _claim_recovery(self,record):
        key=encode([record['actor'],record['operation']]);now=self.clock()
        with self.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('native-event-queries',key)).fetchone()
            timing=json.loads(row['value']) if row else {'nextAt':0,'delay':1.0}
            if timing['nextAt']>now+30:
                timing['nextAt']=now+30
                conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(timing),'native-event-queries',key))
            if timing['nextAt']>now:return False
            delay=min(30.,max(1.,timing['delay']))
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',
                         ('native-event-queries',key,encode({'nextAt':now+delay,'delay':min(30.,delay*2)})))
        return True

    def recover(self):
        cursor=self.store.checkpoint('native-event-recovery','cursor') or {}
        page=self.store.checkpoint_page('native-event-pending',after=cursor.get('after',''))
        if not page:page=self.store.checkpoint_page('native-event-pending')
        if not page:return
        selected=[self.store.checkpoint(self.namespace,key) for key,_ in page]
        self.store.checkpoint_set('native-event-recovery','cursor',{'after':page[-1][0]})
        for record in selected:
            try:self._recover_record(record)
            except RuntimeFault as exc:
                self._diagnose(record,exc.code)

    def _recover_record(self,record):
        ref=self.runtime.scheduler.reference(record['actor'],self.handler,record['operation'])
        try:job=self.runtime.scheduler.get(ref)
        except RuntimeFault:
            self._submit(record);return
        if job['status']=='completed':
            self.store.checkpoint_delete('native-event-pending',encode([record['actor'],record['operation']]))
            self._clear_diagnostic(record)
            return
        if job['status'] not in ('waiting','unknown') or self.runtime.scheduler.is_executing(ref):return
        if job['status']=='unknown':
            saved=self.store.checkpoint('host-deliveries',encode([record['actor'],record['operation']]))
            if saved and saved['receipt']['status']=='handled' and saved['receipt'].get('execution'):
                self.runtime.scheduler.resolve(ref,JobOutcome('completed',{'eventRef':record['operation'],'receipt':saved['receipt']},saved['receipt']['session_id']))
                return
        if not self._claim_recovery(record):return
        if job['status']=='waiting':
            saved=self.store.checkpoint('host-deliveries',encode([record['actor'],record['operation']]))
            preparation=self.store.checkpoint('native-event-preparation',encode([record['actor'],record['operation']]))
            if saved and saved.get('dispatchState')=='not_started' or not saved and preparation and preparation.get('notStarted'):
                try:self.registry.origin_readiness(NativeEventTarget(**record['target']))
                except RuntimeFault as exc:
                    self._diagnose(record,exc.code);return
                self._clear_diagnostic(record)
                self.runtime.scheduler.resume(ref,{'ready':True})
            return
        if job['status']!='unknown' or self.runtime.scheduler.is_executing(ref):return
        _,target=self._record(record['actor'],record['operation'])
        saved=self.store.checkpoint('host-deliveries',encode([record['actor'],record['operation']]))
        if not saved:return
        receipt=self.registry.origin_delivery_status(target,record['operation'],saved['receipt']['reference'])
        if receipt.status=='handled' and receipt.execution is not None:
            self.runtime.scheduler.resolve(ref,JobOutcome('completed',{'eventRef':record['operation'],'receipt':asdict(receipt)},receipt.session_id))

def flow_native_notice(journal,record,receipt):
    service=getattr(journal.runtime,'native_events',None)
    if not service or not record.get('nativeSource'):return
    data=receipt.get('data') or {}
    result=data.get('result') or {}
    def confirmation(value):
        if not isinstance(value,dict):return None
        if value.get('confirmationRef'):return value['confirmationRef']
        return next((found for key in ('result','evidence','data') if (found:=confirmation(value.get(key)))),None)
    ref=confirmation(data)
    decision=receipt['status']=='waiting' and ref is not None
    if decision and journal.runtime.confirmations.get(ref)['status']!='pending':return
    if receipt['status'] not in ('completed','rejected','failed','unknown') and not decision:return
    target=NativeEventTarget(journal.actor,record['flowRef'],journal.namespace,record['flowRef'])
    semantic='decision:'+ref if decision else 'terminal:'+receipt['status']
    service.publish(target,'flow:'+record['flowRef']+':'+record['operation']+':'+semantic,
                    'business.decision' if decision else 'business.result',
                    {'status':receipt['status'],'reason':data.get('reason'),'confirmationRef':ref,'required':data.get('required',[]),'needsUserDecision':bool(decision or data.get('required')),'result':result},
                    evidence_ref={'flowRef':record['flowRef']},business_operation=record['operation'])
