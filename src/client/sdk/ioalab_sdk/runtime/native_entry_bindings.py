"""Explicit native setup permission in the original RuntimeStore.

Only SDK-verified source snapshots and authenticated adapter callbacks enter here.
This ledger never captures a path, starts a process, or selects a project.
"""
from dataclasses import asdict, dataclass
import json
import math
import secrets
import time

from .protocol import RuntimeFault, encode


NATIVE_ENTRY_BIND_OPERATION = 'runtime.entry.bind'
_OPS = 'native-entry-operations'
_INTENTS = 'native-entry-intents'
_SLOTS = 'native-entry-bindings'
_CLAIMS = 'native-entry-claims'


@dataclass(frozen=True)
class EntryBindingAuthority:
    """Authenticated private control identity; conveys no source or save permit."""
    host_ref: str
    adapter_id: str
    actor: str
    profile: str
    installation_ref: str
    account_ref: str
    boot_id: str


@dataclass(frozen=True)
class EntryBindingSource:
    host_ref: str
    adapter_id: str
    actor: str
    profile: str
    installation_ref: str
    account_ref: str
    boot_id: str
    source_digest: str
    expires_at: float


class NativeEntryBindings:
    def __init__(self, store, *, boot_id, installation_ref, account_ref, clock=time.time):
        self.store = store
        self.boot_id = boot_id
        self.installation_ref = installation_ref
        self.account_ref = account_ref
        self.clock = clock

    @staticmethod
    def _text(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 8192:
            raise RuntimeFault('invalid_input', 'Use an original native setup reference')
        return value

    @staticmethod
    def _read(conn, namespace, key):
        row = conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',
                           (namespace, key)).fetchone()
        return json.loads(row['value']) if row else None

    @staticmethod
    def _write(conn, namespace, key, value):
        conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',
                     (namespace, key, encode(value)))

    def _identity(self, identity):
        if (identity.account_ref != self.account_ref or identity.installation_ref != self.installation_ref
                or identity.boot_id != self.boot_id):
            raise RuntimeFault('identity_conflict', 'Use the original account and native installation')

    def _authority(self, authority):
        if not isinstance(authority, EntryBindingAuthority):
            raise RuntimeFault('authenticate', 'A verified native control identity is required')
        values = asdict(authority)
        for value in values.values(): self._text(value)
        self._identity(authority)
        return values

    def _source(self, source):
        if not isinstance(source, EntryBindingSource):
            raise RuntimeFault('not_ready', 'Use the installed native setup entry', 'connect_native_context')
        values = asdict(source)
        for key, value in values.items():
            if key != 'expires_at': self._text(value)
        self._identity(source)
        if (not isinstance(source.expires_at, (int, float)) or isinstance(source.expires_at, bool)
                or not math.isfinite(source.expires_at)):
            raise RuntimeFault('invalid_input', 'Use a verified native source lifetime')
        if source.expires_at <= self.clock():
            raise RuntimeFault('not_ready', 'Capture a fresh source for this explicit setup', 'connect_native_context')
        return values

    def _key(self, host):
        return encode([self.account_ref, host])

    def _projection(self, record):
        phase = record['phase']
        if record['source']['installation_ref'] != self.installation_ref:
            phase = 'migration_required'
        elif phase == 'intent' and (record['source']['boot_id'] != self.boot_id
                                   or record['source']['expires_at'] <= self.clock()):
            phase = 'expired'
        result = {'operationId': record['operationId'], 'hostRef': record['source']['host_ref'],
                  'phase': phase, 'state': 'completed' if phase == 'bound' else
                  'rejected' if phase in ('expired', 'migration_required') else 'waiting'}
        if phase == 'bound': result['bindingRef'] = record['bindingRef']
        if phase == 'reserved': result['action'] = 'recover_saved_receipt'
        if phase == 'migration_required': result['action'] = 'migrate_original_installation'
        return result

    def _save(self, conn, record):
        self._write(conn, _OPS, record['operationId'], record)
        self._write(conn, _INTENTS, record['intentRef'], record)
        self._write(conn, _SLOTS, self._key(record['source']['host_ref']), record)

    def begin(self, operation_id, *, source=None):
        self._text(operation_id)
        with self.store.transaction() as conn:
            old = self._read(conn, _OPS, operation_id)
            if old and old['phase'] == 'bound': return self._projection(old)
            snapshot = self._source(source)
            if old:
                if old['source'] != snapshot:
                    raise RuntimeFault('identity_conflict', 'Recover the original setup source and operation')
                return self._projection(old)
            slot = self._read(conn, _SLOTS, self._key(source.host_ref))
            if slot:
                if slot['source']['adapter_id'] != source.adapter_id:
                    raise RuntimeFault('identity_conflict', 'The host belongs to another native adapter')
                projection = self._projection(slot)
                if projection['phase'] == 'migration_required': return {**projection, 'operationId': operation_id}
                if slot['phase'] == 'bound':
                    alias = {**slot, 'operationId': operation_id}
                    self._write(conn, _OPS, operation_id, alias)
                    return self._projection(alias)
                if projection['phase'] != 'expired':
                    raise RuntimeFault('busy', 'Recover the original native setup before starting another')
                # Only an intent has never granted permission to save. Reservations
                # remain pinned even after boot/expiry/ACK loss.
                slot['phase'] = 'expired'
                self._write(conn, _OPS, slot['operationId'], slot)
                self._write(conn, _INTENTS, slot['intentRef'], slot)
            record = {'operationId': operation_id, 'intentRef': secrets.token_urlsafe(32),
                      'source': snapshot, 'phase': 'intent'}
            self._save(conn, record)
            return self._projection(record)

    def pending(self, scope):
        snapshot = self._source(scope)
        with self.store.transaction() as conn:
            slot = self._read(conn, _SLOTS, self._key(scope.host_ref))
            if not slot or slot['phase'] != 'intent': return None
            if slot['source'] != snapshot: return None
            return {'operationId': slot['operationId'], 'intentRef': slot['intentRef']}

    def claim(self, intent_ref, target_digest, *, scope):
        self._text(intent_ref); self._text(target_digest)
        snapshot = self._source(scope)
        with self.store.transaction() as conn:
            record = self._read(conn, _INTENTS, intent_ref)
            if not record or record['source'] != snapshot:
                raise RuntimeFault('identity_conflict', 'Use the original setup intent and native scope')
            slot = self._read(conn, _SLOTS, self._key(scope.host_ref))
            if not slot or slot['intentRef'] != intent_ref:
                raise RuntimeFault('identity_conflict', 'This setup intent no longer owns the host reservation')
            if record['phase'] == 'reserved':
                if record['targetDigest'] != target_digest:
                    raise RuntimeFault('identity_conflict', 'Recover the originally reserved native target')
            elif record['phase'] == 'intent':
                record.update(phase='reserved', targetDigest=target_digest, claimRef=secrets.token_urlsafe(32))
                self._save(conn, record)
                self._write(conn, _CLAIMS, record['claimRef'], {'intentRef': intent_ref})
            else:
                raise RuntimeFault('identity_conflict', 'This setup intent cannot grant save permission')
            return {'operationId': record['operationId'], 'claimRef': record['claimRef'],
                    'targetDigest': record['targetDigest']}

    def complete(self, claim_ref, target_digest, saved_receipt_ref, *, scope):
        for value in (claim_ref, target_digest, saved_receipt_ref): self._text(value)
        # Recovery acknowledges an existing saved claim; it does not grant a write.
        snapshot = self._authority(scope)
        with self.store.transaction() as conn:
            pointer = self._read(conn, _CLAIMS, claim_ref)
            record = self._read(conn, _INTENTS, pointer['intentRef']) if pointer else None
            if not record or record.get('claimRef') != claim_ref:
                raise RuntimeFault('identity_conflict', 'Restore the original SDK claim before acknowledging a save')
            if any(record['source'][key] != snapshot[key] for key in
                   ('host_ref', 'adapter_id', 'account_ref')):
                raise RuntimeFault('identity_conflict', 'The saved claim belongs to another native binding')
            if record['source']['installation_ref'] != self.installation_ref:
                return self._projection(record)
            slot = self._read(conn, _SLOTS, self._key(scope.host_ref))
            if not slot or slot['intentRef'] != record['intentRef']:
                raise RuntimeFault('identity_conflict', 'Restore the original host reservation')
            if record.get('targetDigest') != target_digest or record['phase'] not in ('reserved', 'bound'):
                raise RuntimeFault('identity_conflict', 'The saved native target differs from the original claim')
            if record['phase'] == 'bound':
                if record['savedReceiptRef'] != saved_receipt_ref:
                    raise RuntimeFault('identity_conflict', 'Recover the original adapter save receipt')
            else:
                record.update(phase='bound', savedReceiptRef=saved_receipt_ref, bindingRef=secrets.token_urlsafe(24))
                self._save(conn, record)
            return self._projection(record)

    def get(self, operation_id):
        self._text(operation_id)
        record = self.store.checkpoint(_OPS, operation_id)
        return self._projection(record) if record else None

    def status(self, host_ref):
        self._text(host_ref)
        record = self.store.checkpoint(_SLOTS, self._key(host_ref))
        return self._projection(record) if record else {'operationId': None, 'hostRef': host_ref,
                                                       'state': 'unknown', 'phase': 'intent',
                                                       'action': 'complete_native_setup'}
