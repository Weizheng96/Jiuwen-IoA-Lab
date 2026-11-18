"""Host selection and durable bindings; business state remains in the original runtime."""
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable

@dataclass(frozen=True)
class PendingSource:
    resolve: Callable[[], "HostContext"]

_native_source = ContextVar("ioalab_native_source", default=None)

def pending_source():
    return _native_source.get()

def native_source(actor=None):
    value=_native_source.get()
    value=value.resolve() if isinstance(value,PendingSource) else value
    if value is not None and actor is not None and value.actor!=actor:
        raise RuntimeFault('identity_conflict','The native entry belongs to another SDK actor')
    return value

@contextmanager
def source_scope(context):
    token = _native_source.set(context)
    try: yield
    finally: _native_source.reset(token)

def source_value(context):
    return {"hostRef":context.host_ref,"sessionId":context.session_id,"workspace":context.workspace} if context else None

def source_context(value,*,actor=None):
    # Only the owner of the original flow/task/interaction may restore old three-field data.
    return HostContext(value["hostRef"],value["sessionId"],value["workspace"],actor) if value else None

import json
import uuid
import hashlib
import copy
import threading
import time
from .host_submission import (HostRegistration, HostInput, HostExecution, SubmissionAttempt,
    SubmissionResult, SubmissionQuery, SubmissionEvent, SubmissionError, MachineTerminal, HostResource,
    BindingAck, HostToolCall)
from .host_submission import HostToolResult,NativeSubmissionPending,native_tools_restricted
from .sdk_tool_callbacks import SdkToolCallbacks
from contextvars import copy_context

from .protocol import RuntimeFault, encode


@dataclass(frozen=True)
class HostContext:
    host_ref: str
    session_id: str
    workspace: str
    actor: str|None=None
    profile: str|None=None


@dataclass(frozen=True)
class NativeEventTarget:
    actor: str
    business_ref: str
    namespace: str
    key: str



def _submission_result(value):
    value=copy.deepcopy(value)
    if value.get('terminal'):value['terminal']=MachineTerminal(**value['terminal'])
    if value.get('error'):value['error']=SubmissionError(**value['error'])
    value['resources']=tuple(HostResource(**item) for item in value.get('resources',()))
    return SubmissionResult(**value)



class HostRegistry:
    """One S01 submission owner, backed by the account-owned RuntimeStore."""
    namespace = 'host-bindings'

    def __init__(self, registrations, default, *, tools=None):
        if not registrations or default not in registrations:
            raise RuntimeFault('not_ready', 'Select a registered default host', 'configure_host')
        self.hosts = dict(registrations)
        self.default = default
        self.tools = dict(tools or {})
        self.store = None
        self.identities={}
        self._event_locks={}
        self._event_locks_guard=threading.Lock()
        self.scheduler=None
        self.callback_pool=None
        self._submission_scopes={}
        self._resource_controls_closed=False
        self._boot_id=str(uuid.uuid4())
        self._owner_lease=None
        self._reconcile_after=0
        self._reconcile_cursor=""
        self._reconcile_worker=None
        self._reconcile_closed=False

    def bind(self, store, *, identities=None):
        self.store = store
        if identities is not None:
            mapping={item.agent_id:item.profile for item in identities}
            if len(mapping)!=len(identities):raise RuntimeFault('identity_conflict','SDK profiles must have unambiguous actors')
            self.identities=mapping

    def recover_under_lease(self,lease):
        """The resident supplies its acquired directory lease, never an empty scope proof."""
        from .lease import Lease
        if not isinstance(lease,Lease) or lease.file.closed:
            raise RuntimeFault('not_ready','Acquire the original instance lease before recovery')
        if self.store is None or lease.file.name!=str(self.store.path.parent/'client.lock'):
            raise RuntimeFault('identity_conflict','Recovery lease belongs to another runtime')
        self._owner_lease=lease
        with self.store.lock:
            self.store.db.execute("CREATE INDEX IF NOT EXISTS runtime_unreleased_native ON runtime_checkpoints(key) WHERE namespace='host-deliveries' AND json_extract(value,'$.format')=2 AND json_extract(value,'$.released')=0")

    def reconcile_submissions(self):
        """Bounded original-query work; changes native occupancy only, never role outcomes."""
        if self._reconcile_closed or time.monotonic()<self._reconcile_after:return
        if self._reconcile_worker is not None and self._reconcile_worker.is_alive():return
        self._reconcile_after=time.monotonic()+1
        self._reconcile_worker=threading.Thread(target=self._reconcile_one,daemon=True)
        self._reconcile_worker.start()

    def close_reconciliation(self):
        self._reconcile_closed=True
        worker=self._reconcile_worker
        if worker is not None:worker.join(.1)
        return worker is None or not worker.is_alive()

    def _reconcile_one(self):
        with self._store().lock:
            rows=self.store.db.execute("SELECT key,value FROM runtime_checkpoints WHERE namespace='host-deliveries' AND json_extract(value,'$.format')=2 AND json_extract(value,'$.released')=0 AND key>? ORDER BY key LIMIT 1",(self._reconcile_cursor,)).fetchall()
        if not rows:self._reconcile_cursor='';return
        self._reconcile_cursor=rows[-1]['key']
        for row in rows:
            record=json.loads(row['value'])
            if record.get('legacyUnverified'):continue
            retry=self.store.checkpoint('host-query-backoff',row['key']) or {}
            if retry.get('due',0)>time.time():continue
            try:self.get_submission(*record['target'][:2],record['receipt']['operation_id'])
            except Exception:pass # Uncertainty never releases or replays a business job.
            delay=min(30,max(1,retry.get('delay',0)*2))
            self.store.checkpoint_set('host-query-backoff',row['key'],{'due':time.time()+delay,'delay':delay})

    def _store(self):
        if self.store is None:
            raise RuntimeFault('not_ready', 'The account runtime has not acquired host ownership')
        return self.store

    def context(self, actor, interaction):
        return self._store().checkpoint(self.namespace, encode([actor, interaction]))

    def _bind(self, actor, interaction, *, source=None):
        if source and source.actor is not None and source.actor!=actor:
            raise RuntimeFault('identity_conflict','Native source actor cannot override the SDK identity')
        store = self._store()
        key = encode([actor, interaction])
        with store.transaction() as conn:
            if not conn.execute('SELECT 1 FROM runtime_cursors WHERE actor=?', (actor,)).fetchone():
                raise RuntimeFault('forbidden', 'Host context belongs to another identity')
            # Use the store transaction for first binding, even when multiple jobs race.
            row = conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?', (self.namespace, key)).fetchone()
            value = json.loads(row['value']) if row else None
            if value:
                if source:
                    if (value['hostRef'],value['sessionId'])!=(source.host_ref,source.session_id):
                        raise RuntimeFault('identity_conflict','Keep the original host and native session')
                    if value['workspace'] is None and source.actor==actor and source.profile==self.identities.get(actor):
                        if not isinstance(source.workspace,str) or not Path(source.workspace).is_absolute():
                            raise RuntimeFault('invalid_input','Use the authenticated native API workspace')
                        # An execution's requested material directory is not native cwd evidence.
                        # Only P10's authenticated source may fill this formerly unknown fact.
                        value['workspace']=source.workspace
                        conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(value),self.namespace,key))
                    elif value['workspace']!=source.workspace:
                        raise RuntimeFault('identity_conflict','Keep the original native workspace')
            else:
                relation = conn.execute('SELECT session FROM runtime_interactions WHERE id=? AND actor=?', (interaction, actor)).fetchone()
                if relation and relation['session'] and source is None:
                    raise RuntimeFault('identity_conflict', 'Migrate the original host of this session explicitly', 'migrate_host')
                if source and (not source.session_id or not Path(source.workspace).is_absolute()):
                    raise RuntimeFault('invalid_input', 'A verified native session and absolute workspace are required')
                value = {'hostRef': source.host_ref if source else self.default,
                         'sessionId': source.session_id if source else None,
                         'workspace': source.workspace if source else None,
                         'source': 'native' if source else 'external'}
                if value['hostRef'] not in self.hosts:
                    raise RuntimeFault('not_ready', 'The bound host is not registered', 'configure_host')
                conn.execute('INSERT INTO runtime_checkpoints VALUES(?,?,?)', (self.namespace, key, encode(value)))
        if value['hostRef'] not in self.hosts:
            raise RuntimeFault('not_ready', 'Restore the original registered host', 'configure_host')
        return value

    def bind_context(self, actor, interaction, context):
        """Trusted adapter entry: callers must first authenticate the native source."""
        if not isinstance(context, HostContext):
            raise RuntimeFault('invalid_input', 'Use the native adapter context')
        return self._bind(actor, interaction, source=context)

    def record_origin(self, actor, origin_message_id, context):
        """Save verified source before the original message can enter the outbox."""
        if not isinstance(context,HostContext) or context.host_ref not in self.hosts:
            raise RuntimeFault('not_ready','A verified registered source is required')
        if context.actor is not None and context.actor!=actor:
            raise RuntimeFault('identity_conflict','Native source actor cannot override the original business owner')
        if not context.session_id or not Path(context.workspace).is_absolute():
            raise RuntimeFault('invalid_input','A real native session and workspace are required')
        self._store().record_host_origin(actor,origin_message_id,
            {'hostRef':context.host_ref,'sessionId':context.session_id,'workspace':context.workspace,'source':'native'})

    def record_application_origin(self,actor,origin_message_id,*,host_ref,interaction=None):
        """Explicit SDK scope: pin its selected application before the first outbox write."""
        if actor not in self.identities or host_ref not in self.hosts:
            raise RuntimeFault('identity_conflict','Restore the original SDK application and actor binding')
        value=self.context(actor,interaction) if interaction is not None else None
        if interaction is not None and (not value or value['hostRef']!=host_ref or value['source']!='external'):
            raise RuntimeFault('identity_conflict','Keep the original SDK application source')
        self._store().record_host_origin(actor,origin_message_id,value or
            {'hostRef':host_ref,'sessionId':None,'workspace':None,'source':'external'})

    def attach_origin(self, actor, origin_message_id, interaction):
        value=self._store().checkpoint('host-origins',encode([actor,origin_message_id]))
        if not value:
            raise RuntimeFault('not_ready','Recover the verified original source before sending', 'resume_original')
        return self.bind_context(actor,interaction,HostContext(value['hostRef'],value['sessionId'],value['workspace']))

    def tools_for(self, actor, interaction, profile):
        ref = self._bind(actor, interaction)['hostRef']
        resource = self.tools.get(ref)
        return resource.for_profile(profile) if resource else {}

    def can_submit(self,actor=None,interaction=None):
        ref=self._bind(actor,interaction)['hostRef'] if actor is not None and interaction is not None else self.default
        host=self.hosts.get(ref)
        return isinstance(getattr(host,'registration',None),HostRegistration) and callable(getattr(host,'submit',None))

    def status_snapshot(self):
        """P12 static/recorded projection; never discovers capabilities or probes a host."""
        entries={}
        for ref,host in self.hosts.items():
            registration=getattr(host,'registration',None)
            recorded=self.store.checkpoint('host-health',ref) if self.store else None
            state=recorded.get('state','unknown') if recorded else 'unknown'
            entries[ref]={'configured':isinstance(registration,HostRegistration) and callable(getattr(host,'submit',None)),
                          'adapterId':registration.adapter_id if isinstance(registration,HostRegistration) else None,
                          'version':registration.version if isinstance(registration,HostRegistration) else None,
                          'state':state,'available':state=='available','observation':recorded}
        return {**entries[self.default],'default':self.default,'registrations':entries}

    def check_default(self):
        """P34 only. Normal startup/status does not invoke this S02 coordination."""
        host=self.hosts[self.default]
        result=host.check()
        if not isinstance(result,dict) or result.get('state') not in ('available','busy','unavailable','unknown'):
            raise RuntimeFault('unknown_result','Host check did not return a verifiable runtime state')
        self._store().checkpoint_set('host-health',self.default,copy.deepcopy(result))
        return copy.deepcopy(result)

    def _migrate_submission(self,actor,interaction,operation_id):
        """One-way receipt projection; never execute a legacy backend or invent IDs."""
        store=self._store();key=encode([actor,operation_id])
        previous=store.checkpoint('host-deliveries',key)
        if previous and previous.get('format')==2:return
        legacy_run=store.checkpoint('host-executions',operation_id) if not previous else None
        if not previous and not legacy_run:return
        profile=self.identities.get(actor)
        if not profile:raise RuntimeFault('not_ready','Restore the original SDK identity before receipt migration')
        origin=None
        if previous:
            target=previous.get('target')
            if not isinstance(target,list) or len(target)!=4 or target[:2]!=[actor,interaction]:
                raise RuntimeFault('identity_conflict','Legacy receipt belongs to another business source')
            try:locator=json.loads(interaction)
            except (ValueError,TypeError):locator=None
            if isinstance(locator,list) and len(locator)==3 and locator[0]=='native-origin':
                origin=NativeEventTarget(**locator[1]);context=self.origin_context(origin)
                if (context.host_ref,context.session_id,context.workspace)!=(target[2],target[3],locator[2]):
                    raise RuntimeFault('identity_conflict','Legacy notice source changed before migration')
                self.bind_context(actor,interaction,context)
        binding=self.context(actor,interaction)
        if not binding:raise RuntimeFault('unknown_result','Restore the original host binding before migrating','migrate_host')
        if previous and previous['target']!=[actor,interaction,binding['hostRef'],binding['sessionId']]:
            raise RuntimeFault('identity_conflict','Legacy receipt changed its bound host/session')
        if legacy_run and legacy_run!={'actor':actor,'interaction':interaction,'hostRef':binding['hostRef']}:
            raise RuntimeFault('identity_conflict','Legacy execution belongs to another original owner')
        record={'format':2,'target':[actor,interaction,binding['hostRef']],
                'legacy':copy.deepcopy(previous if previous else {'execution':legacy_run}),
                'legacyUnverified':True,'released':False,'jobId':None,'bootId':None}
        if previous:
            old=previous.get('receipt',{});nonce=previous.get('attemptNonce');store_id=previous.get('receiptStoreId')
            digest=hashlib.sha256(encode(previous['target']).encode()).hexdigest()
            try:text=json.loads(previous['parameters'])
            except (ValueError,TypeError,KeyError):text=None
            verified=(isinstance(nonce,str) and bool(nonce) and isinstance(store_id,str) and bool(store_id)
                      and previous.get('targetDigest')==digest and isinstance(text,str)
                      and (old.get('operation_id'),old.get('host_ref'),old.get('session_id'))==(operation_id,binding['hostRef'],binding['sessionId']))
            if verified:
                attempt=SubmissionAttempt(nonce,digest,actor,profile)
                receipt=SubmissionResult(operation_id,nonce,'unknown',binding['sessionId'],delivery=old['status'],
                    error=SubmissionError('legacy_unverified','migration','unknown','Preserve the original receipt references; no replay is allowed','reconcile_original_request'))
                evidence=old.get('execution')
                if evidence:
                    fields=('operation_id','host_ref','session_id','request_ref','attempt_nonce','receipt_store_id','terminal','evidence_ref')
                    if (not isinstance(evidence,dict) or set(evidence)!=set(fields) or
                        not all(isinstance(evidence[name],str) and 1<=len(evidence[name])<=8192 for name in fields) or
                        evidence['terminal'] not in ('completed','failed','cancelled') or
                        tuple(evidence[name] for name in ('operation_id','host_ref','session_id','attempt_nonce','receipt_store_id'))!=
                        (operation_id,binding['hostRef'],binding['sessionId'],nonce,store_id)):
                        raise RuntimeFault('identity_conflict','Legacy machine evidence changed its original request/store/attempt')
                    terminal=MachineTerminal(operation_id,nonce,binding['sessionId'],evidence['request_ref'],evidence['terminal'],evidence['evidence_ref'])
                    receipt=replace(receipt,state=terminal.state,request_id=terminal.request_id,terminal=terminal,error=None)
                record.update({'attempt':asdict(attempt),'receipt':asdict(receipt),'legacyUnverified':False,
                    'parameters':encode({'input':{'text':text,'resources':[]},'execution':None,
                                         **({'origin':asdict(origin)} if origin else {})})})
                # 193 deliver_origin had no SDK callback/observer argument or execution
                # definition. This evidence is exclusive to that old notice scope.
                event=store.checkpoint('native-events',key)
                no_callbacks=bool(origin and event and event.get('actor')==actor and event.get('operation')==operation_id
                                  and event.get('target')==asdict(origin) and event.get('context')==source_value(context))
                if no_callbacks:
                    from .scheduling import Scheduler
                    record['jobId']=Scheduler.reference(actor,'runtime.native_event',operation_id)
                    if receipt.terminal and receipt.terminal.state in ('failed','cancelled'):
                        record['released']=True
                        record['settledEvidence']={'kind':'193_notice_without_sdk_callbacks','terminal':asdict(receipt.terminal)}
        with store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()
            current=json.loads(row['value']) if row else None
            if current!=previous:
                if current and current.get('format')==2:return
                raise RuntimeFault('identity_conflict','Original receipt changed during migration')
            if legacy_run:
                row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-executions',operation_id)).fetchone()
                if not row or json.loads(row['value'])!=legacy_run:raise RuntimeFault('identity_conflict','Original execution changed during migration')
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',('host-deliveries',key,encode(record)))

    def submit(self,actor,interaction,operation_id,input,*,execution=None,on_event=None,on_tool=None,origin=None,resource_io=None):
        """P20: claim once, then invoke only S01. No legacy adapter fallback."""
        if not isinstance(input,HostInput) or execution is not None and not isinstance(execution,HostExecution):
            raise RuntimeFault('invalid_input','Use the neutral submission contract')
        if resource_io is not None and (not isinstance(resource_io,tuple) or len(resource_io)!=2 or not all(callable(item) for item in resource_io)):
            raise RuntimeFault('invalid_input','Inject the original SDK resource reader and sink')
        profile=self.identities.get(actor)
        if not profile:raise RuntimeFault('not_ready','Bind the original SDK actor/profile before submitting')
        if execution and (execution.actor,execution.interaction_id)!=(actor,interaction):
            raise RuntimeFault('identity_conflict','Execution changed its original business owner')
        if execution and execution.profile!=profile:raise RuntimeFault('identity_conflict','Execution changed the original SDK profile')
        if origin is not None:
            if execution is not None or not isinstance(origin,NativeEventTarget) or origin.actor!=actor:
                raise RuntimeFault('identity_conflict','A notice must use its original role-owned source')
            context=self.origin_context(origin)
            expected=encode(['native-origin',asdict(origin),context.workspace])
            if interaction!=expected:raise RuntimeFault('identity_conflict','Keep the original notice source locator')
            self.bind_context(actor,interaction,context)
        if execution and execution.kind=='observation':
            from .observations import SCHEMA
            if (len(execution.tools)!=1 or execution.tools[0].name!='verifier.observe' or execution.tools[0].input_schema!=SCHEMA
                or execution.authorization.get('nativeTools',{})!={} or type(execution.authorization.get('nativeToolsEnabled',True)) is not bool
                or set(execution.authorization)-{'nativeTools','nativeToolsEnabled'} or execution.proposal_schema is not None):
                raise RuntimeFault('forbidden','Observations expose only the frozen SDK read-only result channel')
            parent=self.context(actor,execution.business_ref)
            if not parent:raise RuntimeFault('not_ready','Restore the original parent host binding before observing')
            registration=getattr(self.hosts.get(parent['hostRef']),'registration',None)
            if not isinstance(registration,HostRegistration) or registration.readonly_context=='unavailable':
                raise RuntimeFault('not_ready','The original adapter does not provide a read-only context','configure_readonly_context')
            mode=registration.readonly_context
            key=encode([actor,interaction])
            with self._store().transaction() as conn:
                row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,key)).fetchone()
                if row:
                    original=json.loads(row['value'])
                    if (original.get('parent')!=execution.business_ref or original['hostRef']!=parent['hostRef']
                        or original.get('readonlyContext')!=mode):
                        raise RuntimeFault('identity_conflict','Observation changed its original parent host')
                else:
                    conn.execute('INSERT INTO runtime_checkpoints VALUES(?,?,?)',(self.namespace,key,encode(
                        {'hostRef':parent['hostRef'],'sessionId':parent['sessionId'] if mode=='business_session' else None,
                         'workspace':None,'source':'observation','parent':execution.business_ref,'readonlyContext':mode})))
        binding=self._bind(actor,interaction)
        if binding['sessionId'] is not None and (not isinstance(binding['sessionId'],str) or not binding['sessionId'].strip()):
            raise RuntimeFault('identity_conflict','Recover the original valid session; an empty ID is not a new session')
        host=self.hosts[binding['hostRef']]
        registration=getattr(host,'registration',None)
        if not isinstance(registration,HostRegistration) or not callable(getattr(host,'submit',None)):
            raise RuntimeFault('not_ready','Install the registered S01 adapter; legacy execution is not a fallback')
        # Independent snapshots protect both the durable parameters and the adapter input.
        input,execution=copy.deepcopy(input),copy.deepcopy(execution)
        parameters=encode({'input':{'text':input.text,'resources':[asdict(item) for item in input.resources]},
                           'execution':asdict(execution) if execution else None,
                           **({'origin':asdict(origin)} if origin is not None else {})})
        self._migrate_submission(actor,interaction,operation_id)
        key=encode([actor,operation_id]);target=[actor,interaction,binding['hostRef']]
        channel=None
        if on_tool and execution and execution.kind!='observation':
            if self.callback_pool is None:raise RuntimeFault('not_ready','SDK callback resources are not ready')
            channel=SdkToolCallbacks(tools=execution.tools,on_tool=lambda call:invoke_callback(call),pool=self.callback_pool)
        job_id=None
        if self.scheduler is not None:
            parent=operation_id if origin is not None else execution.business_ref if execution and execution.kind=='observation' else interaction
            job_id=self.scheduler.require_model(actor,parent)
            if execution and execution.job_id!=job_id:
                raise RuntimeFault('identity_conflict','Execution changed its original scheduler permit')
            if operation_id!=job_id:
                # Initial native operations used the original job ID. Qualifying a
                # later child must not bypass an unresolved old-format initial run.
                self._migrate_submission(actor,interaction,job_id)
        with self._store().transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()
            old=json.loads(row['value']) if row else None
            if old:
                if old.get('format')!=2:raise RuntimeFault('unknown_result','Migrate the original receipt without dispatching it','migrate_host_submission')
                if old['target']!=target:raise RuntimeFault('identity_conflict','Submission belongs to another business')
                if old.get('legacyUnverified'):raise RuntimeFault('unknown_result','Original execution lacks attributable request/attempt evidence; it was not resubmitted','reconcile_original_request')
                if old['parameters']!=parameters:raise RuntimeFault('id_conflict','Keep the original submission snapshot')
                previous_receipt=_submission_result(old['receipt'])
                undispatched=(previous_receipt.error is not None and previous_receipt.error.dispatch=='not_submitted'
                              and previous_receipt.request_id is None and previous_receipt.delivery=='unknown'
                              and previous_receipt.terminal is None and not old.get('toolEntered'))
                if not undispatched:return previous_receipt
                conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',
                    ('host-submit-attempts',encode([actor,operation_id,previous_receipt.attempt_id]),encode(old)))
            previous=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-executions',operation_id)).fetchone()
            if previous and not old:raise RuntimeFault('unknown_result','Reconcile the original execution before migrating its reference','migrate_host_submission')
            if job_id:
                pending=conn.execute("SELECT key,value FROM runtime_checkpoints WHERE namespace='host-deliveries' AND json_extract(value,'$.format')=2 AND json_extract(value,'$.released')=0 AND json_extract(value,'$.jobId')=? AND key!=?",(job_id,key)).fetchall()
                initial=conn.execute('SELECT key,value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',encode([actor,job_id]))).fetchone()
                if initial and initial['key']!=key and all(row['key']!=initial['key'] for row in pending):
                    value=json.loads(initial['value'])
                    if not value.get('released'):pending.append(initial)
                from .scheduling import _CALLBACK_MODEL_SCOPE
                callback=_CALLBACK_MODEL_SCOPE.get()
                for row in pending:
                    saved=json.loads(row['value'])
                    nested=(execution is not None and execution.kind=='observation' and saved.get('kind')=='business'
                        and callback and callback[0] is self and callback[1][0]==row['key']
                        and self._callback_permit(callback[1],job_id,actor,execution.business_ref))
                    if not nested:raise RuntimeFault('not_ready','The original native execution still owns this model permit; reconcile it before another submission')
            attempt=SubmissionAttempt(str(uuid.uuid4()),hashlib.sha256(encode([target,profile,parameters]).encode()).hexdigest(),actor,profile)
            receipt=SubmissionResult(operation_id,attempt.nonce,'unknown',binding['sessionId'])
            # Execution fact from the static declaration only (D37); never a gate.
            record={'format':2,'target':target,'parameters':parameters,'attempt':asdict(attempt),'receipt':asdict(receipt),
                    'jobId':job_id,'bootId':self._boot_id,'released':False,'kind':execution.kind if execution else 'notice',
                    'nativeToolsRestricted':native_tools_restricted(registration,execution)}
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',('host-deliveries',key,encode(record)))
            if execution and execution.kind=='observation':
                namespace='model-observations:'+actor
                row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(namespace,operation_id)).fetchone()
                observation=json.loads(row['value']) if row else None
                if observation and observation.get('materialOwner'):
                    owner=observation['materialOwner']
                    if (owner.get('actor'),owner.get('profile'),owner.get('jobId'),owner.get('interaction'),owner.get('hostRef'))!=(actor,profile,job_id,execution.business_ref,binding['hostRef']):
                        raise RuntimeFault('identity_conflict','Material observation claim changed its original owner')
                    if observation.get('execution')!=json.loads(encode(asdict(execution))):
                        raise RuntimeFault('identity_conflict','Material claim changed its frozen execution snapshot')
                    observation['submissionAttempt']=attempt.nonce
                    conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(observation),namespace,operation_id))
        scope={'attempt':attempt.nonce,'attempt_value':attempt,'job_id':job_id,'channel':channel,'active':set(),
               'returned':False,'context':copy_context(),'resource_io':resource_io}
        with self._event_locks_guard:self._submission_scopes[key]=scope
        def invoke_callback(value):
            from .scheduling import _CALLBACK_MODEL_SCOPE
            marker=object()
            with self._event_locks_guard:
                if scope.get('sealed'):raise RuntimeFault('closed','The original callback has ended')
                scope['active'].add(marker)
            parent=execution.business_ref if execution and execution.kind=='observation' else interaction
            proof=(key,attempt.nonce,self._boot_id,marker,value.session_id,value.request_id,parent)
            token=_CALLBACK_MODEL_SCOPE.set((self,proof))
            try:return on_tool(value)
            finally:
                _CALLBACK_MODEL_SCOPE.reset(token)
                with self._event_locks_guard:scope['active'].discard(marker)
        def event(value):
            if not isinstance(value,SubmissionEvent):raise RuntimeFault('invalid_input','Use a neutral submission event')
            with self._event_locks_guard:lock=self._event_locks.setdefault(key,threading.RLock())
            with lock:
                result,consume=self._accept_submission(actor,interaction,operation_id,value.result,event=value)
                if consume and on_event:on_event(replace(copy.deepcopy(value),result=result))
                with self.store.transaction() as conn:
                    saved=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()['value'])
                    if result.session_id and result.request_id:saved['bound']=True
                    conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(saved),'host-deliveries',key))
                    event_key=encode([actor,operation_id,value.event_id])
                    conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',
                        (encode({'event':asdict(value),'consumed':True}),'host-submit-events',event_key))
            self._settle_submission(actor,interaction,operation_id,attempt.nonce)
            return BindingAck(result.session_id,result.request_id) if value.kind=='session' else None
        def tool(value):
            if not isinstance(value,HostToolCall):raise RuntimeFault('invalid_input','Use an original SDK tool call')
            saved=self.store.checkpoint('host-deliveries',key)
            receipt=_submission_result(saved['receipt'])
            if (value.operation_id,value.attempt_id,value.session_id,value.request_id)!=(operation_id,attempt.nonce,receipt.session_id,receipt.request_id):
                raise RuntimeFault('identity_conflict','Tool call changed its original submission scope')
            if not receipt.session_id or not receipt.request_id or not saved.get('bound'):
                raise RuntimeFault('not_ready','Persist the real session/request binding before SDK tools')
            if not on_tool:raise RuntimeFault('not_ready','This submission has no SDK tool callback')
            with self.store.transaction() as conn:
                saved=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()['value'])
                saved['toolEntered']=True
                conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(saved),'host-deliveries',key))
            try:
                if channel is not None:
                    future=channel.start(copy.deepcopy(value))
                    future.add_done_callback(lambda _:self._settle_submission(actor,interaction,operation_id,attempt.nonce))
                    return HostToolResult(value=future.result())
                # The SDK's restricted observer may run inside its parent's callback.
                # It reuses that model permit and must not wait for the parent's pool slot.
                if not execution or execution.kind!='observation':
                    raise RuntimeFault('forbidden','SDK tools require the frozen execution channel')
                from jsonschema import Draft202012Validator
                if value.name!='verifier.observe' or next(Draft202012Validator(execution.tools[0].input_schema).iter_errors(value.arguments),None):
                    raise RuntimeFault('invalid_input','Return only the frozen read-only observation')
                return HostToolResult(value=scope['context'].copy().run(invoke_callback,copy.deepcopy(value)))
            except Exception as exc:
                code=exc.code if isinstance(exc,RuntimeFault) else 'unknown_result'
                return HostToolResult(error=SubmissionError(code,'sdk_tool','accepted','Inspect the original SDK tool receipt'))
        try:
            result=host.submit(operation_id,replace(input,attempt=attempt),binding['sessionId'],
                               execution=execution,on_event=event,on_tool=tool)
            result=self._accept_submission(actor,interaction,operation_id,result)
        except Exception as exc:
            # A thrown callback/transport exception after claim cannot prove no dispatch.
            self.store.checkpoint_set('host-submit-errors',key,{'phase':'submit','type':type(exc).__name__[:128],'code':'unknown_result'})
            if isinstance(exc,RuntimeFault) and exc.code=='identity_conflict':raise
            result=_submission_result(self.store.checkpoint('host-deliveries',key)['receipt'])
        finally:
            scope['returned']=True
            self._settle_submission(actor,interaction,operation_id,attempt.nonce)
        if execution and execution.kind=='business':
            reference={'actor':actor,'interaction':interaction,'operation':operation_id,'attempt':attempt.nonce}
            if self.pending_retry(reference,job_id):raise NativeSubmissionPending(self,reference,error=result.error)
        return result

    def _resources_for(self,host_ref,operation_id,attempt):
        """SDK constructor port delegate; every I/O joins the original live scope."""
        if not isinstance(attempt,SubmissionAttempt):raise RuntimeFault('invalid_input','Use the full SDK submission attempt')
        key=encode([attempt.actor,operation_id])
        def locate(conn):
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()
            record=json.loads(row['value']) if row else None
            if not record or record.get('format')!=2 or record.get('legacyUnverified'):
                raise RuntimeFault('not_ready','Restore the original SDK resource scope','restore_original_resource_scope')
            if (record['attempt']!=asdict(attempt) or record['target'][2]!=host_ref or record.get('bootId')!=self._boot_id
                or self.identities.get(attempt.actor)!=attempt.profile):
                raise RuntimeFault('identity_conflict','Resource access changed its original SDK scope')
            if record.get('released') or record['receipt'].get('terminal'):
                raise RuntimeFault('closed','The original native collection has ended')
            with self._event_locks_guard:
                if self._resource_controls_closed:raise RuntimeFault('closed','Original resource controls have closed')
                scope=self._submission_scopes.get(key)
                if (not scope or scope.get('attempt_value')!=attempt or scope.get('job_id')!=record.get('jobId')
                    or scope.get('sealed') or scope.get('resource_io') is None):
                    raise RuntimeFault('not_ready','Original resource authorization is not available','restore_original_resource_scope')
            return record,scope
        with self._store().transaction() as conn:locate(conn)
        def controlled(index,*args):
            marker=object()
            # Claim actual control work under the same receipt transaction. A
            # terminal that wins first cannot admit a later collection call.
            with self.store.transaction() as conn:
                record,scope=locate(conn)
                with self._event_locks_guard:
                    if scope.get('sealed') or self._resource_controls_closed:raise RuntimeFault('closed','The original native collection has ended')
                    scope['active'].add(marker)
            try:return scope['context'].copy().run(scope['resource_io'][index],*args)
            finally:
                with self._event_locks_guard:scope['active'].discard(marker)
                self._settle_submission(attempt.actor,record['target'][1],operation_id,attempt.nonce)
        return (lambda ref:controlled(0,ref),lambda name,media_type,data,expected_digest:controlled(1,name,media_type,data,expected_digest))

    def _close_resource_controls(self):
        """After original workers/native drain, refuse new I/O before closing IPC."""
        with self._event_locks_guard:
            self._resource_controls_closed=True
            return not any(scope['active'] for scope in self._submission_scopes.values())

    def pending_retry(self,reference,job_id,*,claim=False):
        """Original role-only retry eligibility, not readiness inferred from text."""
        if not isinstance(reference,dict) or set(reference)!={'actor','interaction','operation','attempt'}:return False
        if not all(isinstance(value,str) and value for value in reference.values()):return False
        key=encode([reference['actor'],reference['operation']])
        with self._store().transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()
            saved=json.loads(row['value']) if row else None
            if not saved or saved.get('format')!=2 or saved.get('legacyUnverified'):return False
            receipt=saved.get('receipt') or {};error=receipt.get('error') or {}
            binding=self.context(reference['actor'],reference['interaction'])
            if (saved['target'][:2]!=[reference['actor'],reference['interaction']] or saved.get('jobId')!=job_id or
                not binding or saved['target'][2]!=binding['hostRef'] or receipt.get('session_id')!=binding['sessionId'] or
                saved['attempt']['profile']!=self.identities.get(reference['actor']) or
                saved['attempt']['nonce']!=reference['attempt'] or not saved.get('released') or saved.get('toolEntered') or
                receipt.get('request_id') or receipt.get('terminal') or receipt.get('delivery')!='unknown' or error.get('dispatch')!='not_submitted'):
                return False
            if not claim:return True
            timing=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-submit-retry',key)).fetchone()
            timing=json.loads(timing['value']) if timing else {'nextAt':0,'delay':1.}
            now=time.time()
            if timing['nextAt']>now+30:timing['nextAt']=now+30
            if timing['nextAt']>now:return False
            delay=min(30.,max(1.,timing['delay']))
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',('host-submit-retry',key,encode({'nextAt':now+delay,'delay':min(30.,delay*2)})))
        return True

    def pending_for_job(self,job_id,actor,interaction):
        """Recover only a recorded no-dispatch fact after the job-outcome crash gap."""
        with self._store().lock:
            rows=self.store.db.execute("SELECT value FROM runtime_checkpoints WHERE namespace='host-deliveries' AND json_extract(value,'$.format')=2 AND json_extract(value,'$.jobId')=?",(job_id,)).fetchall()
        records=[json.loads(row['value']) for row in rows]
        if any(not record.get('released') for record in records):return None
        candidates=[]
        for record in records:
            if record.get('kind')!='business' or record['target'][:2]!=[actor,interaction]:continue
            receipt=record.get('receipt') or {}
            reference={'actor':actor,'interaction':interaction,'operation':receipt.get('operation_id'),'attempt':record['attempt']['nonce']}
            if self.pending_retry(reference,job_id):candidates.append(reference)
        return candidates[0] if len(candidates)==1 else None

    def _callback_permit(self,proof,job_id,actor,parent):
        """A non-exported live callback token, never a model/IPC supplied context."""
        if not isinstance(proof,tuple) or len(proof)!=7:return False
        key,nonce,boot,marker,session,request,original_parent=proof
        if type(marker) is not object or not all(isinstance(value,str) and value for value in (key,nonce,boot,session,request,original_parent)):return False
        if boot!=self._boot_id or original_parent!=parent:return False
        with self._event_locks_guard:
            scope=self._submission_scopes.get(key)
            active=bool(scope and scope['attempt']==nonce and marker in scope['active'])
        if not active:return False
        saved=self.store.checkpoint('host-deliveries',key)
        receipt=(saved.get('receipt') or {}) if saved else {}
        return bool(saved and saved['target'][0]==actor and saved.get('jobId')==job_id and saved.get('bootId')==boot
                    and not saved.get('released') and saved['attempt']['nonce']==nonce
                    and (receipt.get('session_id'),receipt.get('request_id'))==(session,request))

    def _settle_submission(self,actor,interaction,operation_id,attempt_id):
        """Release only this attempt after real machine end and live callback drain."""
        key=encode([actor,operation_id])
        record=self.store.checkpoint('host-deliveries',key)
        if not record or record.get('legacyUnverified'):return False
        if record['target'][:2]!=[actor,interaction] or record['attempt']['nonce']!=attempt_id:
            return False
        receipt=_submission_result(record['receipt'])
        not_started=receipt.error and receipt.error.dispatch=='not_submitted' and not receipt.request_id and not record.get('toolEntered')
        if not receipt.terminal and not not_started:return False
        with self._event_locks_guard:
            scope=self._submission_scopes.get(key)
            if not scope:
                lease=self._owner_lease
                # An acquired OS lease excludes the old owner. Its process-local callbacks
                # cannot survive; the native request must independently be proven ended.
                if (not (receipt.terminal or not_started) or not record.get('bootId') or record['bootId']==self._boot_id or
                        lease is None or lease.file.closed):return False
                with self.store.transaction() as conn:
                    current=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()['value'])
                    if current['attempt']['nonce']!=attempt_id or current.get('bootId')!=record['bootId']:return False
                    current['released']=True
                    current['settledEvidence']={'kind':'prior_owner_lease_ended','recoveredBy':self._boot_id,'terminal':asdict(receipt.terminal) if receipt.terminal else None,'notSubmitted':bool(not_started)}
                    conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(current),'host-deliveries',key))
                return True
            if scope['attempt']!=attempt_id or not scope['returned']:return False
            scope['sealed']=True
            active=bool(scope['active'])
        # No scope/global/store lock spans callback draining. In particular a
        # callback may be returning from an observation that persists its result.
        channel=scope['channel']
        if channel is not None:
            channel.seal()
            if not channel.drain(0):return False
        if active:return False
        with self.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()
            current=json.loads(row['value']) if row else None
            if (not current or current['target'][:2]!=[actor,interaction] or
                current['attempt']['nonce']!=attempt_id or current.get('bootId')!=self._boot_id):
                raise RuntimeFault('identity_conflict','Original submission generation changed before drain')
            # Once sealed no new observer callback can enter; an observed zero
            # therefore remains zero. Existing callbacks only move towards drain.
            current['released']=True
            conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(current),'host-deliveries',key))
        return True

    def _submission_record(self,actor,interaction,operation_id):
        self._migrate_submission(actor,interaction,operation_id)
        record=self._store().checkpoint('host-deliveries',encode([actor,operation_id]))
        if not record or record.get('format')!=2:
            raise RuntimeFault('not_ready','Restore or explicitly migrate the original submission','migrate_host_submission')
        if record.get('legacyUnverified'):raise RuntimeFault('unknown_result','Original execution remains unverified; no replay is allowed','reconcile_original_request')
        binding=self.context(actor,interaction)
        if not binding or record['target']!=[actor,interaction,binding['hostRef']]:
            raise RuntimeFault('identity_conflict','The original submission belongs to another business')
        receipt=_submission_result(record['receipt'])
        if receipt.session_id and receipt.session_id!=binding['sessionId']:
            raise RuntimeFault('identity_conflict','Restore the original session binding')
        return record,binding,receipt

    def native_tools_restricted(self,actor,operation_id):
        """Recorded per-claim fact; None before any claim, False for pre-D37 records."""
        record=self._store().checkpoint('host-deliveries',encode([actor,operation_id]))
        if not record or record.get('format')!=2:return None
        return record.get('nativeToolsRestricted') is True

    def original_execution_ended(self,actor,interaction,operation_id,job_id):
        """Local proof for an owner-authorized next native child, never a retry grant."""
        try:record,_,receipt=self._submission_record(actor,interaction,operation_id)
        except RuntimeFault:return False
        return bool(record.get('jobId')==job_id and record.get('kind')=='business'
                    and record.get('released') and receipt.terminal)

    def get_submission(self,actor,interaction,operation_id):
        """P22 exception recovery; absence of S04 or an ID never starts another turn."""
        record,binding,receipt=self._submission_record(actor,interaction,operation_id)
        if receipt.terminal or (receipt.error and receipt.error.dispatch=='not_submitted' and not receipt.request_id and not record.get('toolEntered')):
            self._settle_submission(actor,interaction,operation_id,receipt.attempt_id)
            return receipt
        # Receipt migration preserves historical references, but cannot create
        # the original submission provenance needed for an ID-less S04 query.
        if not receipt.request_id and 'legacy' in record:
            return receipt
        host=self.hosts[binding['hostRef']];registration=getattr(host,'registration',None)
        if not isinstance(registration,HostRegistration) or 'get_request' not in registration.optional_methods:
            return receipt
        try:result=host.get_request(SubmissionQuery(operation_id,receipt.attempt_id,receipt.session_id,receipt.request_id))
        except Exception as exc:
            self.store.checkpoint_set('host-submit-errors',encode([actor,operation_id]),{'phase':'get_request','type':type(exc).__name__[:128],'code':'unknown_result'})
            # A lost query proves nothing; return the strongest receipt persisted meanwhile, not this call's stale snapshot.
            return self._submission_record(actor,interaction,operation_id)[2]
        if result is None:return self._submission_record(actor,interaction,operation_id)[2]
        result=self._accept_submission(actor,interaction,operation_id,result)
        self._settle_submission(actor,interaction,operation_id,result.attempt_id)
        return result

    def cancel_submission(self,actor,interaction,operation_id):
        """P21 only requests native interruption; P22 remains terminal authority."""
        record,binding,receipt=self._submission_record(actor,interaction,operation_id)
        if receipt.terminal:return {'supported':True,'accepted':False,'ended':True}
        host=self.hosts[binding['hostRef']];registration=getattr(host,'registration',None)
        if not isinstance(registration,HostRegistration) or 'cancel' not in registration.optional_methods:
            return {'supported':False,'accepted':False,'ended':False}
        if not receipt.request_id:return {'supported':True,'accepted':None,'ended':False}
        try:accepted=host.cancel(receipt.request_id)
        except Exception as exc:
            self.store.checkpoint_set('host-submit-errors',encode([actor,operation_id]),{'phase':'cancel','type':type(exc).__name__[:128],'code':'unknown_result'})
            accepted=None
        # Neither a native bool nor a textual cancel receipt proves machine termination.
        return {'supported':True,'accepted':accepted if type(accepted) is bool else None,'ended':False}

    def _accept_submission(self,actor,interaction,operation_id,result,*,event=None):
        if not isinstance(result,SubmissionResult):raise RuntimeFault('unknown_result','Preserve the original uncertain submission')
        key=encode([actor,operation_id])
        with self.store.transaction() as conn:
            record=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()['value'])
            known=_submission_result(record['receipt'])
            if (result.operation_id,result.attempt_id)!=(operation_id,record['attempt']['nonce']):
                raise RuntimeFault('identity_conflict','Execution result changed the original attempt')
            for field in ('session_id','request_id'):
                before,after=getattr(known,field),getattr(result,field)
                if before and after and before!=after:raise RuntimeFault('identity_conflict','Execution changed its original '+field)
                if before and not after:result=replace(result,**{field:before})
            if result.error and result.error.dispatch=='not_submitted':
                if (known.request_id or result.request_id or known.delivery!='unknown' or result.delivery!='unknown'
                    or record.get('toolEntered') or known.terminal or result.terminal or result.state not in ('waiting','failed','unknown')):
                    raise RuntimeFault('identity_conflict','An accepted submission cannot become undispatched')
            if result.terminal:
                terminal=result.terminal
                if (terminal.operation_id,terminal.attempt_id,terminal.session_id,terminal.request_id)!=(operation_id,result.attempt_id,result.session_id,result.request_id):
                    raise RuntimeFault('identity_conflict','Machine terminal belongs to another request')
                if terminal.state!=result.state:
                    raise RuntimeFault('identity_conflict','Result state contradicts its machine terminal')
            if known.terminal and result.terminal and known.terminal!=result.terminal:
                raise RuntimeFault('identity_conflict','The original machine terminal changed')
            if known.terminal:
                for field in ('output','proposal','error'):
                    before,after=getattr(known,field),getattr(result,field)
                    if before is not None and after is not None and before!=after:
                        raise RuntimeFault('identity_conflict','The original final '+field+' changed')
                if known.resources and result.resources and known.resources!=result.resources:
                    raise RuntimeFault('identity_conflict','The original final resources changed')
                result=replace(result,terminal=known.terminal,state=known.state,usage={**result.usage,**known.usage})
            if result.state in ('completed','failed','cancelled') and not result.terminal:
                result=replace(result,state='unknown')
            progress={'unknown':0,'accepted':1,'running':2,'waiting':2,'completed':3,'failed':3,'cancelled':3}
            if progress[result.state]<progress[known.state]:result=replace(result,state=known.state)
            for field in ('output','proposal','error'):
                if getattr(result,field) is None and getattr(known,field) is not None:
                    result=replace(result,**{field:getattr(known,field)})
            if not result.resources and known.resources:result=replace(result,resources=known.resources)
            result=replace(result,usage={**known.usage,**result.usage})
            rank={'unknown':0,'accepted':1,'delivered':2,'handled':3}
            if rank[result.delivery]<rank[known.delivery]:result=replace(result,delivery=known.delivery)
            if event:
                event_key=encode([actor,operation_id,event.event_id]);fingerprint=encode(asdict(event))
                prior=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-submit-events',event_key)).fetchone()
                prior=json.loads(prior['value']) if prior else None
                if prior and encode(prior['event'])!=fingerprint:raise RuntimeFault('identity_conflict','Original event facts changed')
                consume=not prior or not prior['consumed']
                conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',('host-submit-events',event_key,encode({'event':asdict(event),'consumed':False})))
            if result.session_id:
                binding_key=encode([actor,interaction])
                binding=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,binding_key)).fetchone()['value'])
                if binding['sessionId'] and binding['sessionId']!=result.session_id:
                    raise RuntimeFault('identity_conflict','Session changed after persistent binding')
                if binding.get('readonlyContext')=='business_session':
                    parent_key=encode([actor,binding['parent']])
                    parent_row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,parent_key)).fetchone()
                    parent=json.loads(parent_row['value']) if parent_row else None
                    if not parent or parent['hostRef']!=record['target'][2]:
                        raise RuntimeFault('identity_conflict','Observation changed the original business host')
                    if parent['sessionId'] is not None and parent['sessionId']!=result.session_id:
                        raise RuntimeFault('identity_conflict','Observation changed the original business session')
                    if parent['sessionId'] is None:
                        if event is None or event.kind!='session':
                            raise RuntimeFault('not_ready','Confirm the real business session through P28 before observing')
                        # Only the actual adapter's original claim may establish this
                        # business SID. Do not copy a requested material cwd or a
                        # professional definition into the unverified business.
                        parent['sessionId']=result.session_id
                        conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(parent),self.namespace,parent_key))
                binding['sessionId']=result.session_id
                conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(binding),self.namespace,binding_key))
                # Both reference facts are durable before the on_event P28 ACK returns.
                record.setdefault('bound',False)
            record['receipt']=asdict(result)
            conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(record),'host-deliveries',key))
        return (copy.deepcopy(result),consume) if event else copy.deepcopy(result)

    def origin_context(self, target, *, interaction=None):
        if not isinstance(target,NativeEventTarget):
            raise RuntimeFault('invalid_input','Use a role-resolved original business source')
        actor,ref=target.actor,target.business_ref
        with self._store().lock:
            if not self.store.db.execute('SELECT 1 FROM runtime_cursors WHERE actor=?',(actor,)).fetchone():
                raise RuntimeFault('forbidden','Source actor is outside this account runtime')
        expected=(target.namespace=='host-origins' and target.key==encode([actor,ref]) or
                  target.namespace=='delegation:'+actor and target.key=='task:'+ref or
                  target.namespace in ('flow:delegation:'+actor,'flow:messaging:'+actor,'flow:provider:'+actor) and target.key==ref)
        if not expected:raise RuntimeFault('forbidden','Business source locator does not belong to this actor/reference')
        record=self.store.checkpoint(target.namespace,target.key)
        if not record:raise RuntimeFault('not_ready','Restore the original business source','restore_original')
        if target.namespace=='delegation:'+actor and (record.get('context',{}).get('actor')!=actor or record.get('context',{}).get('scopeId')!=ref):
            raise RuntimeFault('forbidden','Task source does not match the original actor/scope')
        if target.namespace.startswith('flow:') and record.get('flowRef')!=ref:
            raise RuntimeFault('forbidden','Flow source does not match the original business reference')
        value=record if target.namespace=='host-origins' else record.get('nativeSource')
        if not value or target.namespace=='host-origins' and value.get('source')!='native':
            raise RuntimeFault('not_ready','This business has no verified native user source','connect_native_context')
        context=source_context(value,actor=actor)
        if context.host_ref not in self.hosts or not context.session_id or not Path(context.workspace).is_absolute():
            raise RuntimeFault('not_ready','Restore the original native session and registered host')
        if interaction is not None:
            relation=self.store.interaction(interaction_id=interaction)
            binding=self.context(actor,interaction)
            if (not relation or relation['actor']!=actor or not binding or binding.get('source')!='native'
                or source_value(source_context(binding,actor=actor))!=source_value(context)):
                raise RuntimeFault('identity_conflict','The established interaction changed its original native source')
        return context

    def cancel_job(self,job_id):
        """P21 resolves only original requests belonging to this exact SDK job."""
        with self._store().lock:
            rows=self.store.db.execute("SELECT value FROM runtime_checkpoints WHERE namespace='host-deliveries' AND json_extract(value,'$.format')=2 AND json_extract(value,'$.jobId')=? AND json_extract(value,'$.released')=0",(job_id,)).fetchall()
        results=[]
        for row in rows:
            record=json.loads(row['value'])
            if record.get('legacyUnverified'):continue
            actor,interaction,_=record['target']
            results.append(self.cancel_submission(actor,interaction,record['receipt']['operation_id']))
        return results
