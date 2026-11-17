"""Route A persisted-history correlation: queue client -> unique turn -> completed end."""
import pytest
from ioalab_codex.native_facts import IdentityConflict, correlate, evidence, output, summary, terminal


def turn(tid='turn-1', client='client-1', status='inProgress', completed=None, extra=()):
    items = [{'type': 'userMessage', 'id': 'u-'+tid, 'content': []}]
    if client != 'omit':
        items[0]['clientId'] = client
    return {'id': tid, 'status': status, 'completedAt': completed, 'items': items+list(extra)}


def test_unique_client_turn_is_the_original_delivery():
    assert correlate([turn('t2', 'other'), turn('t1')], 'client-1')['id'] == 't1'


@pytest.mark.parametrize('turns', [[], [turn(client='omit')], [turn(client=None)], [turn(client='foreign')],
                                   [{'id': 'latest', 'status': 'completed', 'completedAt': 9,
                                     'items': [{'type': 'agentMessage', 'text': 'client-1 done'}]}]])
def test_absent_null_foreign_client_or_reply_text_is_not_correlation(turns):
    assert correlate(turns, 'client-1') is None


@pytest.mark.parametrize('turns,reason', [
    ([turn('t1'), turn('t2')], 'client_multiple_turns'),
    ([turn('t1', extra=[{'type': 'userMessage', 'clientId': 'client-1'}])], 'client_repeated_in_turn'),
    ([{'status': 'completed', 'items': [{'type': 'userMessage', 'clientId': 'client-1'}]}], 'missing_actual_turn_ID'),
])
def test_ambiguous_history_is_identity_conflict(turns, reason):
    with pytest.raises(IdentityConflict, match=reason):
        correlate(turns, 'client-1')


@pytest.mark.parametrize('status,completed,expected', [
    ('completed', 1700000000, True), ('completed', None, False), ('interrupted', None, False),
    ('interrupted', 1700000000, False), ('failed', 1700000000, False), ('inProgress', None, False),
    ('completed', True, False)])
def test_only_completed_with_persisted_completed_at_is_terminal(status, completed, expected):
    assert terminal(turn(status=status, completed=completed)) is expected


def test_output_and_evidence_are_structural():
    value = turn(status='completed', completed=5, extra=[{'type': 'agentMessage', 'id': 'a', 'text': 'first'},
                                                         {'type': 'agentMessage', 'id': 'b', 'text': 'second'}])
    assert output(value) == 'first\nsecond' and output(turn()) is None
    assert evidence(value) == evidence(dict(value, items=[])) and evidence(value).startswith('codex-turn:')
    assert summary(value) == {'id': 'turn-1', 'status': 'completed', 'completedAtPresent': True,
                              'itemTypes': ['userMessage', 'agentMessage', 'agentMessage']}
