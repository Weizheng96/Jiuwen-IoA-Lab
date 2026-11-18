"""Official typed desktop delegation correlation and machine terminal evidence."""
import copy
import pytest
from ioalab_codex.native_facts import IdentityConflict, correlate_delegation, delegation_digest, evidence, output, summary, terminal

TEXT = '<codex_delegation>\n  <source_thread_id>source</source_thread_id>\n  <input>prompt</input>\n</codex_delegation>'
DIGEST = delegation_digest('source', 'prompt')

def turn(tid='turn-1', status='inProgress', completed=None, extra=()):
    return {'id': tid, 'status': status, 'completedAt': completed, 'items': [
        {'type': 'functionCallOutput', 'id': 'fco-1', 'name': 'send_message_to_thread', 'namespace': 'codex_app',
         'output': {'text': TEXT, 'truncated': False}}]+list(extra)}

def test_complete_machine_input_echo_uniquely_identifies_delivery_and_excludes_old_snapshot():
    assert correlate_delegation([turn()], DIGEST)['id'] == 'turn-1'
    assert correlate_delegation([turn()], DIGEST, ['turn-1']) is None

@pytest.mark.parametrize('changes', [
    {'type': 'agentMessage'}, {'name': 'create_thread'}, {'namespace': None},
    {'output': {'text': TEXT, 'truncated': True}},
    {'output': {'text': TEXT[:-1], 'truncated': False}},
    {'output': {'text': TEXT+'extra', 'truncated': False}},
])
def test_only_exact_untruncated_typed_echo_can_correlate(changes):
    value = turn(); value['items'][0].update(changes)
    assert correlate_delegation([value], DIGEST) is None

@pytest.mark.parametrize('values', [[turn('t1'), turn('t2')], [turn(extra=turn()['items'])], [turn(), turn()]])
def test_duplicate_echoes_are_ambiguous(values):
    with pytest.raises(IdentityConflict): correlate_delegation(values, DIGEST)

@pytest.mark.parametrize('status,completed,expected', [
    ('completed', 1700000000, True), ('completed', None, False), ('interrupted', None, False),
    ('interrupted', 1700000000, True), ('failed', 1700000000, True), ('inProgress', None, False),
    ('completed', True, False)])
def test_only_ended_status_with_persisted_completed_at_is_terminal(status, completed, expected):
    assert terminal(turn(status=status, completed=completed)) is expected


def test_output_and_evidence_are_structural():
    value = turn(status='completed', completed=5, extra=[{'type': 'agentMessage', 'id': 'a', 'text': 'first'},
                                                         {'type': 'agentMessage', 'id': 'b', 'text': 'second'}])
    assert output(value) == 'first\nsecond' and output(turn()) is None
    assert evidence(value) == evidence(dict(value, items=[])) and evidence(value).startswith('codex-turn:')
    assert summary(value) == {'id': 'turn-1', 'status': 'completed', 'completedAtPresent': True,
                              'itemTypes': ['functionCallOutput', 'agentMessage', 'agentMessage']}


@pytest.mark.parametrize('source,prompt,escaped_source,escaped_input', [
    ('caller', '& "C:\\Python\\python.exe" "<entry>"', 'caller', '&amp; "C:\\Python\\python.exe" "&lt;entry&gt;"'),
    ('caller', '</input><source_thread_id>foreign</source_thread_id>', 'caller', '&lt;/input&gt;&lt;source_thread_id&gt;foreign&lt;/source_thread_id&gt;'),
    ('caller', '&amp; already encoded', 'caller', '&amp;amp; already encoded'),
    ('a&<b>"', "single' and double\"", 'a&amp;&lt;b&gt;"', "single' and double\""),
])
def test_actual_xml_text_escaped_machine_echo_matches_exactly(source, prompt, escaped_source, escaped_input):
    # Expected protocol bytes are literal examples, not produced by the production helper.
    expected = '<codex_delegation>\n  <source_thread_id>'+escaped_source+'</source_thread_id>\n  <input>'+escaped_input+'</input>\n</codex_delegation>'
    value = turn()
    value['items'][0]['output']['text'] = expected
    digest = delegation_digest(source, prompt)
    assert correlate_delegation([value], digest)['id'] == 'turn-1'
    value['items'][0]['output']['text'] = expected.replace('&amp;', '&', 1) if '&amp;' in expected else expected+'extra'
    assert correlate_delegation([value], digest) is None
