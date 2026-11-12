"""Small durable compositions of existing assistant operations."""
import copy
import uuid

from a2c.codec import digest
from ..runtime import RuntimeFault, encode


def begin(role, operation, args):
    namespace = 'assistant-flow:' + role.identity.agent_id
    with role.lock:
        record = role.store.checkpoint(namespace, operation)
        fingerprint = digest(args)
        if record and record['fingerprint'] != fingerprint:
            raise RuntimeFault('id_conflict', 'Original delegation input changed')
        if record is None:
            children = {name: str(uuid.uuid5(uuid.NAMESPACE_URL, encode(
                ['assistant.begin', role.identity.agent_id, operation, name]))) for name in ('prepare', 'authorize')}
            record = {'fingerprint': fingerprint, 'children': children}
            role.store.checkpoint_set(namespace, operation, record)
        if 'taskRef' not in record:
            prepared = role.delegation.prepare(record['children']['prepare'], args)
            record['taskRef'] = prepared['data']['taskRef']
            role.store.checkpoint_set(namespace, operation, record)
        # The original authorize method owns the immutable grant and its decision.
        result = role.delegation.authorize(record['children']['authorize'], {'taskRef': record['taskRef']})
        return {**result, 'data': {**result['data'], 'taskRef': record['taskRef']}}


def guidance(name, operation, args, result):
    """Hints contain real references only and never grant permission."""
    if name in ('delegation.flow', 'messaging.flow'):
        return result
    result = copy.deepcopy(result)
    actions = []
    data = result.get('data', {})
    if not isinstance(data, dict):
        result['nextActions'] = actions
        return result
    status = result.get('status')
    def add(target, values, **extra):
        actions.append({'operation': target, 'arguments': values, **extra})
    if status == 'waiting' and data.get('confirmationRef'):
        add('runtime.open_ui', {})
        add('assistant.' + name, args, resumeOriginal=True, operationId=operation)
    elif status in ('accepted', 'running'):
        job = data.get('job', {})
        reference = data.get('reference')
        if not reference and job.get('reference'):
            reference = {'kind': 'job', 'id': job['reference']}
        if isinstance(reference, dict): add('operations.get', {'reference': reference})
        elif data.get('contractRef'): add('assistant.delegation.status', {'contractRef': data['contractRef']})
        elif data.get('quoteRef'): add('assistant.delegation.status', {'quoteRef': data['quoteRef']})
    elif status == 'completed':
        if name in ('delegation.begin', 'delegation.authorize') and data.get('taskRef'):
            add('assistant.delegation.search', {'taskRef': data['taskRef']}, requires=['query'])
        elif name == 'delegation.search':
            for hit in data.get('hits', []):
                if isinstance(hit, dict) and hit.get('id'):
                    add('assistant.delegation.inspect', {'service': hit['id']})
        elif data.get('contractRef') and name != 'delegation.status':
            add('assistant.delegation.status', {'contractRef': data['contractRef']})
    result['nextActions'] = actions
    return result
