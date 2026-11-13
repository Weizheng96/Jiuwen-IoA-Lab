"""Durable role-owned flow journal; business sequencing stays in each role."""
import copy
import threading
import uuid

from a2c.codec import digest
from .catalog import CATALOG
from .protocol import RuntimeFault, encode


def flow_key(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encode(list(parts))))


class FlowJournal:
    def __init__(self, runtime, actor, kind, advance):
        self.runtime, self.actor, self.kind, self.advance = runtime, actor, kind, advance
        self.store = runtime.store
        self.namespace = 'flow:' + kind + ':' + actor
        self.lock = threading.RLock()
        self.cursor = 0

    def save(self, record):
        self.store.checkpoint_set(self.namespace, record['flowRef'], record)

    def call(self, operation, args, actions):
        action = args.get('action', 'observe')
        value = args.get('input', {})
        if action not in (*actions, 'observe', 'resume') or not isinstance(value, dict):
            raise RuntimeFault('invalid_input', 'Use a documented business action and object input')
        if action in ('observe', 'resume') and value:
            raise RuntimeFault('invalid_input', 'Observation and recovery use only the original flowRef')
        ref = args.get('flowRef')
        if ref is not None and (not isinstance(ref, str) or not ref):
            raise RuntimeFault('invalid_input', 'Use an original flowRef')
        if ref is not None:
            try:
                if str(uuid.UUID(ref)) != ref:
                    raise ValueError()
            except ValueError:
                raise RuntimeFault('invalid_input', 'Use the exact original flowRef') from None
        with self.lock:
            request_key = 'request:' + operation
            original = self.store.checkpoint(self.namespace, request_key)
            if original and original['fingerprint'] != digest(args):
                raise RuntimeFault('id_conflict', 'Original flow invocation changed')
            if original:
                record = self.store.checkpoint(self.namespace, original['flowRef'])
            elif ref:
                record = self.store.checkpoint(self.namespace, ref)
                if not record:
                    raise RuntimeFault('forbidden', 'Flow belongs to another local role or identity')
            else:
                if action in ('observe', 'resume'):
                    raise RuntimeFault('invalid_input', 'An original flowRef is required')
                ref = flow_key(self.kind, self.actor, operation)
                record = {'flowRef': ref, 'context': {}, 'action': None, 'results': {}, 'active': False}
            if not original and action not in ('observe', 'resume'):
                if record['active']:
                    raise RuntimeFault('not_ready', 'Resume the pending action before supplying a new business decision')
                record.update(action=action, input=copy.deepcopy(value), operation=operation,
                              results={}, active=True, stage=0)
            # Invocation binding and its initial state commit together, before effects.
            self.store.checkpoint_update(self.namespace, {record['flowRef']: record, request_key: {'fingerprint': digest(args), 'flowRef': record['flowRef']}})
            return self.drive(record)

    def drive(self, record, advance=None):
        if not record['active'] and record.get('receipt'):
            return {**copy.deepcopy(record['receipt']), 'data': {**copy.deepcopy(record['receipt']['data']), **copy.deepcopy(record['context'])}}
        try:
            result = (advance or self.advance)(self, record)
        except FlowWait as exc:
            result = exc.receipt
        except RuntimeFault as exc:
            if exc.code not in ('unknown_result', 'result_unknown', 'not_ready', 'closed'):
                record['active'] = False
                record['receipt'] = self.receipt(record,'rejected',exc.code,error={'code':exc.code,'message':str(exc),'recovery':exc.recovery})
                self.save(record)
                raise
            if exc.code == 'closed':
                record['active'] = False
            result = self.receipt(record, 'unknown' if exc.code in ('unknown_result', 'result_unknown') else 'waiting',
                                  exc.code, error={'code': exc.code, 'message': str(exc), 'recovery': exc.recovery})
        record['receipt'] = result
        self.save(record)
        return copy.deepcopy(result)

    def step(self, record, index, name, args, invoke):
        """Persist the exact child call, then consume its actual job if asynchronous."""
        required, optional = CATALOG[name].inputs[0]
        if not set(required) <= set(args) or set(args) - set(required) - set(optional):
            raise RuntimeFault('invalid_input', 'Use documented input fields for ' + name)
        key = str(index) + ':' + name
        child = flow_key(record['flowRef'], record['operation'], key)
        entry = record['results'].get(key)
        if entry is None:
            entry = {'operationId': child, 'name': name, 'args': copy.deepcopy(args)}
            record['results'][key] = entry
            self.save(record)
        elif entry['args'] != args:
            raise RuntimeFault('id_conflict', 'Original flow step changed')
        result = entry.get('result')
        if not result or result['status'] not in ('completed', 'rejected', 'failed', 'unknown'):
            result = invoke(name, child, args)
            data = result.get('data') or {}
            job = data.get('job') if isinstance(data, dict) else None
            reference = data.get('reference') if isinstance(data, dict) else None
            job_ref = job.get('reference') if isinstance(job, dict) else None
            if isinstance(reference, dict) and reference.get('kind') == 'job':
                job_ref = reference['id']
            if job_ref:
                actual = self.runtime.scheduler.get(job_ref)
                result = {'status': actual['status'], 'data': {**data, **(actual.get('data') or {})}}
            elif name == 'assistant.messaging.send' and result['status'] == 'accepted':
                result = {**result, 'status': 'completed'}
            elif isinstance(data, dict) and data.get('operationRef'):
                actual = self.runtime._delivery_operation({'reference': data['operationRef']})
                result = {'status':actual['status'], 'data':{**data, 'delivery':actual}}
            entry['result'] = result
            self.save(record)
        # Unknown jobs are reconciled by their existing owner, never requeued here.
        if result['status'] == 'unknown':
            data = result.get('data') or {}
            job = data.get('job') or {}
            if job.get('reference'):
                actual = self.runtime.scheduler.get(job['reference'])
                if actual['status'] != 'unknown':
                    result = entry['result'] = {'status': actual['status'], 'data': {**data, **(actual.get('data') or {})}}
        if result['status'] == 'completed':
            record['stage'] = max(record['stage'], index + 1)
            self.save(record)
            return result.get('data')
        if result['status'] in ('rejected', 'failed'):
            record['active'] = False
        reason = ('confirmation' if isinstance(result.get('data'), dict) and result['data'].get('confirmationRef') else
                  'unknown_result' if result['status'] == 'unknown' else 'external_processing')
        if reason == 'confirmation' and not entry.get('uiAttempted'):
            entry['uiAttempted'] = True
            try:
                entry['uiOpened'] = bool(self.runtime.user_ui.open())
            except RuntimeFault:
                pass
            self.save(record)
        raise FlowWait(self.receipt(record, result['status'] if result['status'] in ('rejected', 'failed', 'unknown') else 'waiting', reason,
                            evidence=result.get('data')))

    def receipt(self, record, status='waiting', reason='business_input', *, required=(), **data):
        return {'status': status, 'data': {'flowRef': record['flowRef'], 'action': record['action'],
                'reason': reason, 'required': list(required), 'continue': {'action': 'resume', 'flowRef': record['flowRef']},
                **copy.deepcopy(record['context']), **data}, 'nextActions': []}

    def scan(self):
        with self.lock:
            records = [(key,record) for key,record in self.store.checkpoints(self.namespace).items()
                       if not key.startswith(('request:','delivery:')) and record.get('active')]
            if not records:
                return
            selected = (records + records)[self.cursor:self.cursor + min(32,len(records))]
            self.cursor = (self.cursor + 32) % len(records)
            for key, record in selected:
                if key.startswith('request:') or not record.get('active'):
                    continue
                if key.startswith('delivery:'):
                    continue
                try:
                    self.drive(record)
                except RuntimeFault:
                    continue


class FlowWait(Exception):
    def __init__(self, receipt):
        self.receipt = receipt
