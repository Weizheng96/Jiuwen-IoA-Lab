"""Route A machine correlation from persisted history; never from reply text.

queue -> the original clientUserMessageId -> exactly one userMessage -> exactly one
turn is delivery. Only that same turn persisted as completed with a concrete
completedAt is a machine terminal. interrupted/failed without proof, a vanished
queue item, the latest turn or an ordinal never substitute for this chain.
"""
import hashlib
import json


class IdentityConflict(ValueError):
    pass


def correlate(turns, client_id):
    """Return the unique persisted turn carrying ``client_id`` or None; conflicts raise."""
    if not isinstance(client_id, str) or not client_id:
        raise IdentityConflict('missing_original_client')
    matches = {}
    for turn in turns:
        if not isinstance(turn, dict):
            raise IdentityConflict('invalid_persisted_turn')
        items = turn.get('items')
        if not isinstance(items, list):
            continue
        own = [item for item in items if isinstance(item, dict) and item.get('type') == 'userMessage'
               and item.get('clientId') == client_id]
        if not own:
            continue
        tid = turn.get('id')
        if not isinstance(tid, str) or not tid:
            raise IdentityConflict('missing_actual_turn_ID')
        if len(own) > 1:
            raise IdentityConflict('client_repeated_in_turn')
        if tid in matches and matches[tid] is not turn:
            raise IdentityConflict('turn_listed_twice')
        matches[tid] = turn
    if len(matches) > 1:
        raise IdentityConflict('client_multiple_turns')
    return next(iter(matches.values()), None)


def terminal(turn):
    """Only completed with a persisted completedAt is a machine end."""
    completed = turn.get('completedAt')
    return turn.get('status') == 'completed' and isinstance(completed, int) and not isinstance(completed, bool)


def output(turn):
    texts = [item.get('text') for item in turn.get('items', ())
             if isinstance(item, dict) and item.get('type') == 'agentMessage' and isinstance(item.get('text'), str)]
    return '\n'.join(texts) if texts else None


def evidence(turn):
    """Stable structural reference of the persisted end (no message text)."""
    facts = {key: turn.get(key) for key in ('id', 'status', 'completedAt')}
    return 'codex-turn:'+hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def summary(turn):
    """Private diagnostic structure for records; no message bodies."""
    return {'id': turn.get('id'), 'status': turn.get('status'),
            'completedAtPresent': turn.get('completedAt') is not None,
            'itemTypes': [item.get('type') for item in turn.get('items', ()) if isinstance(item, dict)]}
