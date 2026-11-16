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

def native_source():
    value=_native_source.get()
    return value.resolve() if isinstance(value,PendingSource) else value

@contextmanager
def source_scope(context):
    token = _native_source.set(context)
    try: yield
    finally: _native_source.reset(token)

def source_value(context):
    return {"hostRef":context.host_ref,"sessionId":context.session_id,"workspace":context.workspace} if context else None

def source_context(value):
    return HostContext(value["hostRef"],value["sessionId"],value["workspace"]) if value else None

import json
import uuid
import hashlib

from .protocol import RuntimeFault, encode


@dataclass(frozen=True)
class HostContext:
    host_ref: str
    session_id: str
    workspace: str


@dataclass(frozen=True)
class NativeExecutionEvidence:
    operation_id: str
    host_ref: str
    session_id: str
    request_ref: str
    attempt_nonce: str
    receipt_store_id: str
    terminal: str
    evidence_ref: str

    def __post_init__(self):
        if not all(isinstance(value,str) and 1<=len(value)<=8192 for value in asdict(self).values()):
            raise ValueError('Native execution evidence requires bounded machine references')
        if self.terminal not in ('completed','failed','cancelled'):
            raise ValueError('Use a real native machine terminal, never an acknowledgement')


@dataclass(frozen=True)
class DeliveryReceipt:
    operation_id: str
    host_ref: str
    session_id: str
    status: str
    reference: str|None = None
    execution: NativeExecutionEvidence|None = None

    def __post_init__(self):
        if isinstance(self.execution,dict):object.__setattr__(self,'execution',NativeExecutionEvidence(**self.execution))
        if self.execution is not None and not isinstance(self.execution,NativeExecutionEvidence):raise ValueError('Use verified native machine evidence')
        if self.status not in ('accepted', 'delivered', 'handled', 'unknown'):
            raise ValueError('Use the actual host delivery status')
        if not all(isinstance(v, str) and v for v in (self.operation_id, self.host_ref, self.session_id)):
            raise ValueError('Delivery requires the original operation and host session')


@dataclass(frozen=True)
class NativeEventTarget:
    actor: str
    business_ref: str
    namespace: str
    key: str



@dataclass(frozen=True)
class DeliveryAttempt:
    nonce: str
    receipt_store_id: str
    target_digest: str


def validate_native_execution(evidence,*,operation_id,host_ref,session_id,attempt):
    if (not isinstance(evidence,NativeExecutionEvidence) or not isinstance(attempt,DeliveryAttempt) or
        (evidence.operation_id,evidence.host_ref,evidence.session_id,evidence.attempt_nonce,evidence.receipt_store_id)!=
        (operation_id,host_ref,session_id,attempt.nonce,attempt.receipt_store_id)):
        raise RuntimeFault('identity_conflict','Native execution evidence does not belong to the original attempt')
    return evidence


class DeliveryNotStarted(Exception):
    """Trusted adapter proof: this exact attempt never entered the native queue."""
    def __init__(self,operation_id,session_id,attempt):
        super().__init__('Native adapter proved this attempt was not dispatched')
        self.operation_id,self.session_id,self.attempt=operation_id,session_id,attempt



class HostRegistry:
    """One injected AgentHost facade, backed by the account-owned RuntimeStore."""
    namespace = 'host-bindings'

    def __init__(self, registrations, default, *, tools=None):
        if not registrations or default not in registrations:
            raise RuntimeFault('not_ready', 'Select a registered default host', 'configure_host')
        self.hosts = dict(registrations)
        self.default = default
        self.tools = dict(tools or {})
        self.store = None

    def bind(self, store):
        self.store = store

    def _store(self):
        if self.store is None:
            raise RuntimeFault('not_ready', 'The account runtime has not acquired host ownership')
        return self.store

    def context(self, actor, interaction):
        return self._store().checkpoint(self.namespace, encode([actor, interaction]))

    def _bind(self, actor, interaction, *, source=None):
        store = self._store()
        key = encode([actor, interaction])
        with store.transaction() as conn:
            if not conn.execute('SELECT 1 FROM runtime_cursors WHERE actor=?', (actor,)).fetchone():
                raise RuntimeFault('forbidden', 'Host context belongs to another identity')
            # Use the store transaction for first binding, even when multiple jobs race.
            row = conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?', (self.namespace, key)).fetchone()
            value = json.loads(row['value']) if row else None
            if value:
                if source and (value['hostRef'], value['sessionId'], value['workspace']) != (source.host_ref, source.session_id, source.workspace):
                    raise RuntimeFault('identity_conflict', 'Keep the original host and native session')
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
        if not context.session_id or not Path(context.workspace).is_absolute():
            raise RuntimeFault('invalid_input','A real native session and workspace are required')
        self._store().record_host_origin(actor,origin_message_id,
            {'hostRef':context.host_ref,'sessionId':context.session_id,'workspace':context.workspace,'source':'native'})

    def attach_origin(self, actor, origin_message_id, interaction):
        value=self._store().checkpoint('host-origins',encode([actor,origin_message_id]))
        if not value:
            raise RuntimeFault('not_ready','Recover the verified original source before sending', 'resume_original')
        return self.bind_context(actor,interaction,HostContext(value['hostRef'],value['sessionId'],value['workspace']))

    def for_interaction(self, actor, interaction):
        self._bind(actor, interaction)
        return BoundHost(self, actor, interaction)

    def tools_for(self, actor, interaction, profile):
        ref = self._bind(actor, interaction)['hostRef']
        resource = self.tools.get(ref)
        return resource.for_profile(profile) if resource else {}

    def capabilities(self):
        registrations={key:value.capabilities() for key,value in self.hosts.items()}
        default=registrations[self.default]
        return {**default, 'available':any(item.get('available') for item in registrations.values()),
                'defaultAvailable':bool(default.get('available')), 'defaultHost': self.default,
                'capabilityStates':{'execution':'supported' if default.get('available') else 'unavailable',
                                    'nativeSource':default.get('nativeSourceState','unknown'),
                                    'originalDelivery':default.get('originalDeliveryState','unknown')},
                'registrations':registrations}

    def probe(self):
        for host in self.hosts.values():
            host.probe()
        return self.capabilities()

    def prepare(self, host_ref):
        host = self.hosts.get(host_ref)
        if host is None:
            raise RuntimeFault('not_ready', 'Register this host first', 'configure_host')
        return host.probe()

    def capture_context(self, host_ref):
        host = self.hosts.get(host_ref)
        capture = getattr(host, 'capture_context', None)
        if capture is None:
            raise RuntimeFault('not_ready', 'Native source capture is not available for this adapter', 'configure_host')
        value = capture()
        if not isinstance(value, HostContext) or value.host_ref != host_ref:
            raise RuntimeFault('identity_conflict', 'Native source did not identify the registered host')
        return value

    def resolve_context(self, host_ref, context_ref, *, config):
        """Opaque references are verified by the issuing native adapter, never decoded by models."""
        host=self.hosts.get(host_ref)
        resolve=getattr(host,'resolve_context',None)
        if resolve is None:
            raise RuntimeFault('not_ready','Verified native source references are not available','configure_host')
        value=resolve(context_ref,host_ref=host_ref,config=config)
        if not isinstance(value,HostContext) or value.host_ref!=host_ref:
            raise RuntimeFault('identity_conflict','Native source reference belongs to another host')
        return value

    def deliver(self, actor, interaction, operation_id, message):
        return self._delivery('deliver', actor, interaction, operation_id, message)

    def delivery_status(self, actor, interaction, operation_id, reference):
        return self._delivery('delivery_status', actor, interaction, operation_id, reference)

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
        context=source_context(value)
        if context.host_ref not in self.hosts or not context.session_id or not Path(context.workspace).is_absolute():
            raise RuntimeFault('not_ready','Restore the original native session and registered host')
        if interaction is not None:
            relation=self.store.interaction(interaction_id=interaction)
            binding=self.context(actor,interaction)
            if (not relation or relation['actor']!=actor or not binding or binding.get('source')!='native'
                or source_value(source_context(binding))!=source_value(context)):
                raise RuntimeFault('identity_conflict','The established interaction changed its original native source')
        return context

    def deliver_origin(self,target,operation_id,message,*,interaction=None):
        context=self.origin_context(target,interaction=interaction)
        return self._origin_delivery('deliver',target,context,operation_id,message)

    def origin_delivery_status(self,target,operation_id,reference,*,interaction=None):
        context=self.origin_context(target,interaction=interaction)
        return self._origin_delivery('delivery_status',target,context,operation_id,reference)

    def origin_readiness(self,target):
        context=self.origin_context(target)
        callback=getattr(self.hosts[context.host_ref],'prepare_delivery',None)
        if callback is None:raise RuntimeFault('not_ready','Prepare the original native delivery channel','prepare_native_delivery')
        try:value=callback(context.session_id)
        except RuntimeFault:raise
        except Exception:
            raise RuntimeFault('not_ready','Original native preparation is unavailable; no dispatch attempted','prepare_native_delivery') from None
        if not isinstance(value,dict) or value.get('ready') is not True:
            raise RuntimeFault('not_ready','Original native delivery is not ready; no dispatch attempted','prepare_native_delivery')
        if not isinstance(value.get('receiptStoreId'),str) or not value['receiptStoreId']:
            raise RuntimeFault('not_ready','Native receipt store identity is not verified','prepare_native_delivery')
        return value

    def _origin_delivery(self,method,target,context,operation_id,value):
        # This immutable source locator never becomes an interaction UUID later.
        identity=encode(['native-origin',asdict(target),context.workspace])
        binding=source_value(context)
        if method=='deliver':binding={**binding,'deliveryReady':self.origin_readiness(target)}
        return self._delivery_to(method,target.actor,identity,binding,operation_id,value)

    def _delivery(self, method, actor, interaction, operation_id, value):
        return self._delivery_to(method,actor,interaction,self._bind(actor,interaction),operation_id,value)

    def _delivery_to(self,method,actor,interaction,binding,operation_id,value):
        host = self.hosts[binding['hostRef']]
        callback = getattr(host, method, None)
        if callback is None or not binding['sessionId']:
            raise RuntimeFault('not_ready', 'Original-session delivery is not available', 'configure_host')
        key=encode([actor,operation_id])
        target=[actor,interaction,binding['hostRef'],binding['sessionId']]
        with self.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()
            original=json.loads(row['value']) if row else None
            if original and original['target']!=target:
                raise RuntimeFault('identity_conflict','The original delivery belongs to another business session')
            if method=='deliver':
                parameters=encode(value)
                if original:
                    if original['parameters']!=parameters:
                        raise RuntimeFault('id_conflict','Keep the original delivery arguments')
                    if original.get('dispatchState')!='not_started':return DeliveryReceipt(**original['receipt'])
                    ready=binding.get('deliveryReady')
                    if not ready or ready['receiptStoreId']!=original.get('receiptStoreId'):
                        raise RuntimeFault('identity_conflict','Verify the original receipt store before resuming the proven undispatched operation')
                receipt=DeliveryReceipt(operation_id,binding['hostRef'],binding['sessionId'],'unknown')
                original={'parameters':parameters,'target':target,'receipt':asdict(receipt),'dispatchState':'attempting','attemptNonce':str(uuid.uuid4()),
                          'targetDigest':hashlib.sha256(encode(target).encode()).hexdigest(),
                          'receiptStoreId':(binding.get('deliveryReady') or {}).get('receiptStoreId')}
                conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',('host-deliveries',key,encode(original)))
            elif not original or original['receipt']['reference']!=value:
                raise RuntimeFault('identity_conflict','Query the original saved delivery reference')
        if method=='delivery_status' and original.get('receiptStoreId'):
            try:current=getattr(host,'prepare_delivery')(binding['sessionId'])
            except Exception:
                raise RuntimeFault('unknown_result','Restore the original native receipt store before querying','resume_original') from None
            if not isinstance(current,dict) or current.get('receiptStoreId')!=original['receiptStoreId']:
                raise RuntimeFault('unknown_result','Original native receipt store identity is not verified','resume_original')
        try:
            attempt=DeliveryAttempt(original['attemptNonce'],original['receiptStoreId'],original['targetDigest']) if method=='deliver' and binding.get('deliveryReady') else None
            receipt = (callback(operation_id,binding['sessionId'],value,attempt=attempt) if attempt else
                       callback(operation_id,binding['sessionId'],value,expected_receipt_store_id=original['receiptStoreId'])
                       if method=='delivery_status' and original.get('receiptStoreId') else callback(operation_id,binding['sessionId'],value))
        except DeliveryNotStarted as proof:
            if (method!='deliver' or not original.get('receiptStoreId') or
                (proof.operation_id,proof.session_id,proof.attempt)!=(operation_id,binding['sessionId'],attempt)):
                # This is not trustworthy proof about the original attempted effect.
                raise RuntimeFault('unknown_result','Preserve the original uncertain native operation','resume_original')
            with self.store.transaction() as conn:
                saved=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()['value'])
                if saved['target']!=target or saved.get('attemptNonce')!=original['attemptNonce'] or saved['receipt']['status']!='unknown':
                    raise RuntimeFault('unknown_result','Native not-started proof conflicts with an existing receipt','resume_original')
                conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',
                             (encode({**saved,'dispatchState':'not_started'}),'host-deliveries',key))
            raise
        except Exception:
            receipt=DeliveryReceipt(operation_id,binding['hostRef'],binding['sessionId'],'unknown',
                                    original['receipt']['reference'] if method=='deliver' else value)
        if (not isinstance(receipt, DeliveryReceipt) or
            (receipt.operation_id, receipt.host_ref, receipt.session_id) != (operation_id, binding['hostRef'], binding['sessionId'])):
            raise RuntimeFault('unknown_result', 'Preserve and inspect the original host delivery reference', 'resume_original')
        if receipt.execution is not None:
            validate_native_execution(receipt.execution,operation_id=operation_id,host_ref=binding['hostRef'],
                                      session_id=binding['sessionId'],attempt=DeliveryAttempt(original['attemptNonce'],original['receiptStoreId'],original['targetDigest']))
        # Merge only machine-confirmed facts. Concurrent queries can finish out of order;
        # a late timeout/acceptance cannot erase an already observed delivery/handling.
        with self.store.transaction() as conn:
            saved=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-deliveries',key)).fetchone()['value'])
            if saved['target']!=target:
                raise RuntimeFault('identity_conflict','The original delivery belongs to another business session')
            known=saved['receipt']['reference']
            if known is not None:
                if receipt.reference is not None and receipt.reference!=known:
                    raise RuntimeFault('unknown_result','Host delivery reference changed; preserve the original reference','resume_original')
                if receipt.reference is None:receipt=replace(receipt,reference=known)
            previous=DeliveryReceipt(**saved['receipt'])
            if previous.execution and receipt.execution and previous.execution.request_ref!=receipt.execution.request_ref:
                raise RuntimeFault('identity_conflict','Native execution request changed for the original notice')
            execution=previous.execution or receipt.execution
            strength={'unknown':0,'accepted':1,'delivered':2,'handled':3}
            if strength[saved['receipt']['status']]>strength[receipt.status]:
                receipt=DeliveryReceipt(**saved['receipt'])
            if execution is not None:receipt=replace(receipt,execution=execution)
            conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',
                         (encode({**saved,'receipt':asdict(receipt)}),'host-deliveries',key))
        return receipt

    def run(self, turn):
        binding = self._bind(turn.actor, turn.interaction_id)
        if binding['source'] == 'native':
            raise RuntimeFault('not_ready', 'Use original-session delivery; do not replace the user conversation definition')
        if binding['sessionId'] != turn.session_id:
            raise RuntimeFault('identity_conflict', 'Resume the original bound session')
        workspace = str(Path(turn.workspace).resolve())
        if binding['workspace'] and binding['workspace'] != workspace:
            raise RuntimeFault('identity_conflict', 'Resume in the original workspace')
        binding = {**binding, 'workspace': workspace}
        self.store.checkpoint_set(self.namespace, encode([turn.actor, turn.interaction_id]), binding)
        execution={'actor':turn.actor,'interaction':turn.interaction_id,'hostRef':binding['hostRef']}
        with self.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-executions',turn.operation_id)).fetchone()
            if row and row['value']!=encode(execution):
                raise RuntimeFault('identity_conflict','The original execution belongs to another business context')
            conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',('host-executions',turn.operation_id,encode(execution)))

        def session(value):
            if binding['sessionId'] and binding['sessionId'] != value:
                raise RuntimeFault('identity_conflict', 'Host changed its original session')
            binding['sessionId'] = value
            self.store.checkpoint_set(self.namespace, encode([turn.actor, turn.interaction_id]), binding)
            if turn.on_session:
                turn.on_session(value)
        return self.hosts[binding['hostRef']].run(replace(turn, on_session=session))

    def cancel(self, operation_id):
        original=self._store().checkpoint('host-executions',operation_id)
        if not original:return False
        host=self.hosts.get(original['hostRef'])
        if host is None:raise RuntimeFault('not_ready','Restore the original execution host')
        return host.cancel(operation_id)


def for_interaction(host, actor, interaction):
    """Resolve a registered host or retain an explicitly injected single-host application."""
    return host.for_interaction(actor, interaction) if isinstance(host, HostRegistry) else host


class BoundHost:
    def __init__(self, registry, actor, interaction):
        self.registry, self.actor, self.interaction = registry, actor, interaction

    def capabilities(self):
        binding = self.registry._bind(self.actor, self.interaction)
        return self.registry.hosts[binding['hostRef']].capabilities()

    def run(self, turn):
        if (turn.actor, turn.interaction_id) != (self.actor, self.interaction):
            raise RuntimeFault('identity_conflict', 'Host call changed its bound business context')
        return self.registry.run(turn)

    def cancel(self, operation_id):
        original=self.registry._store().checkpoint('host-executions',operation_id)
        if original and (original['actor'],original['interaction'])!=(self.actor,self.interaction):
            raise RuntimeFault('identity_conflict','Cancel only the original bound execution')
        return self.registry.cancel(operation_id)
