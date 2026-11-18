"""Route A machine correlation from persisted history; never from reply text.

A complete typed official delegation input echo identifies exactly one turn. Only that same turn persisted as ended with a concrete
completedAt is a machine terminal. interrupted/failed without end proof, a vanished
queue item, the latest turn or an ordinal never substitute for this chain.
"""
import hashlib
from html import escape
import json


class IdentityConflict(ValueError):
    pass


def delegation_digest(source_sid, text):
    """Hash the complete official machine delegation wrapper, never parse embedded XML."""
    wrapper = ('<codex_delegation>\n  <source_thread_id>'+escape(source_sid, quote=False)+
               '</source_thread_id>\n  <input>'+escape(text, quote=False)+'</input>\n</codex_delegation>')
    return hashlib.sha256(wrapper.encode('utf-8')).hexdigest()


def correlate_delegation(turns, digest, excluded=()):
    """Match one complete typed tool-input echo; agent prose cannot claim a turn."""
    matches = {}
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get('id'), str):
            raise IdentityConflict('invalid_persisted_turn')
        if turn['id'] in excluded:
            continue
        own = []
        for item in turn.get('items', []):
            if not isinstance(item, dict) or (item.get('type'), item.get('namespace'), item.get('name')) != (
                    'functionCallOutput', 'codex_app', 'send_message_to_thread'):
                continue
            output = item.get('output')
            if (isinstance(output, dict) and output.get('truncated') is False and isinstance(output.get('text'), str)
                    and hashlib.sha256(output['text'].encode('utf-8')).hexdigest() == digest):
                own.append(item)
        if len(own) > 1 or own and turn['id'] in matches:
            raise IdentityConflict('delegation_repeated')
        if own:
            matches[turn['id']] = turn
    if len(matches) > 1:
        raise IdentityConflict('delegation_multiple_turns')
    return next(iter(matches.values()), None)


def terminal(turn):
    """Only an ended machine status with a persisted completedAt is an end."""
    completed = turn.get('completedAt')
    return turn.get('status') in ('completed','failed','interrupted') and isinstance(completed, int) and not isinstance(completed, bool)


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
