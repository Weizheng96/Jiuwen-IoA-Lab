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

from .protocol import RuntimeFault, encode


@dataclass(frozen=True)
class HostContext:
    host_ref: str
    session_id: str
    workspace: str


@dataclass(frozen=True)
class DeliveryReceipt:
    operation_id: str
    host_ref: str
    session_id: str
    status: str
    reference: str|None = None

    def __post_init__(self):
        if self.status not in ('accepted', 'delivered', 'handled', 'unknown'):
            raise ValueError('Use the actual host delivery status')
        if not all(isinstance(v, str) and v for v in (self.operation_id, self.host_ref, self.session_id)):
            raise ValueError('Delivery requires the original operation and host session')


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

    def _delivery(self, method, actor, interaction, operation_id, value):
        binding = self._bind(actor, interaction)
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
                    return DeliveryReceipt(**original['receipt'])
                receipt=DeliveryReceipt(operation_id,binding['hostRef'],binding['sessionId'],'unknown')
                original={'parameters':parameters,'target':target,'receipt':asdict(receipt)}
                conn.execute('INSERT INTO runtime_checkpoints VALUES(?,?,?)',('host-deliveries',key,encode(original)))
            elif not original or original['receipt']['reference']!=value:
                raise RuntimeFault('identity_conflict','Query the original saved delivery reference')
        try:
            receipt = callback(operation_id, binding['sessionId'], value)
        except Exception:
            receipt=DeliveryReceipt(operation_id,binding['hostRef'],binding['sessionId'],'unknown',
                                    original['receipt']['reference'] if method=='deliver' else value)
        if (not isinstance(receipt, DeliveryReceipt) or
            (receipt.operation_id, receipt.host_ref, receipt.session_id) != (operation_id, binding['hostRef'], binding['sessionId'])):
            raise RuntimeFault('unknown_result', 'Preserve and inspect the original host delivery reference', 'resume_original')
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
            strength={'unknown':0,'accepted':1,'delivered':2,'handled':3}
            if strength[saved['receipt']['status']]>strength[receipt.status]:
                receipt=DeliveryReceipt(**saved['receipt'])
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
