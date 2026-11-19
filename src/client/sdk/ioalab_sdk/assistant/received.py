"""Read projections of this assistant's original incoming requests and replies."""
from ..runtime import QueryDefinition, RuntimeFault


QUERY = {'type': 'object', 'additionalProperties': False, 'properties': {
    'messageRef': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
    'before': {'type': 'integer', 'minimum': 1},
}}


def received(role, args):
    limit, before, reference = args.get('limit', 20), args.get('before'), args.get('messageRef')
    if (set(args) - set(QUERY['properties']) or type(limit) is not int or not 1 <= limit <= 50
            or before is not None and (type(before) is not int or before < 1)
            or reference is not None and (not isinstance(reference, str) or not 1 <= len(reference) <= 128)):
        raise RuntimeFault('invalid_input', 'Use messageRef or bounded limit/before filters')
    actor = role.identity.agent_id

    def project(frame, detail=False):
        message = frame['message']; body = message['body']; mid = message['messageId']
        version = role.rules.version(body['interfaceName'], body['revision'], validate_tools=False)
        declaration = version['declaration']
        result = {'messageRef': mid, 'senderAgentId': message['sender'],
                  'acceptedAt': message.get('acceptedAt'), 'expiresAt': message['expiresAt'],
                  'interface': body['interfaceName'], 'revision': body['revision'],
                  'description': declaration['description'], 'state': 'received'}
        job_ref = role.runtime.scheduler.reference(actor, 'assistant.message', mid)
        try: result['state'] = role.runtime.scheduler.get(job_ref)['status']
        except RuntimeFault as exc:
            if exc.code != 'not_ready': raise
        reply = role.store.checkpoint(role.namespace, 'reply:' + mid)
        receipt = role.store.response(actor, 'message.send', reply['operationId']) if reply else None
        result['replyAccepted'] = bool(receipt and receipt.get('ok'))
        result['summary'] = [{'label': declaration['inputSchema'].get('properties', {}).get(key, {}).get('title', key),
                              'value': str(value)[:160]}
                             for key, value in body['input'].items() if isinstance(value, (str, int, float, bool))][:3]
        if detail:
            result.update(input=body['input'], inputSchema=declaration['inputSchema'],
                          outputSchema=declaration['outputSchema'],
                          reply=reply['envelope']['body'] if reply else None)
        return result

    if reference is not None:
        frame = role.store.message(actor, reference)
        if (not frame or frame['message']['kind'] != 'message.request'
                or frame['message'].get('payloadAvailable') is False):
            raise RuntimeFault('forbidden', 'Use an available request received by this assistant')
        return {'detail': project(frame, True)}
    frames = role.store.received_requests(actor, before=before, limit=limit + 1)
    return {'items': [project(frame) for frame in frames[:limit]],
            'nextBefore': frames[limit - 1]['recipientSeq'] if len(frames) > limit else None}


def inbox_form(role):
    return QueryDefinition('messaging.inbox', '收到的请求', QUERY, lambda values: received(role, values))
