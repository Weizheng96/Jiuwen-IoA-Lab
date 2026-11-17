"""Native correlation at partial-event/history boundaries, without inference."""
import pytest
from ioalab_codex.native_facts import IdentityConflict, NativeTurnFacts


def turn(tid='actual-first', client='first-client', **extra):
    item = {'type': 'userMessage', 'id': 'native-user'}
    if client != 'omit':
        item['clientId'] = client
    return {'id': tid, 'status': 'inProgress', 'items': [item], **extra}


def facts():
    value = NativeTurnFacts('first-client', 'queue-client')
    value.bind_first('actual-first')
    return value


@pytest.mark.parametrize('items', [[], [{'type': 'reasoning'}],
                                  [{'type': 'userMessage'}], [{'type': 'userMessage', 'clientId': None}]])
def test_partial_absence_never_confirms_terminal(items):
    value = facts()
    sample = turn(items=items)
    value.observe(sample, source='thread/turns/list')
    assert value.terminal('first-client', sample) is False


def test_nonnull_foreign_client_is_rejected():
    with pytest.raises(IdentityConflict, match='unexpected_nonnull_client'):
        facts().observe(turn(client='foreign'), source='thread/turns/list')


def test_first_rpc_id_rejects_other_turn_even_without_items():
    with pytest.raises(IdentityConflict, match='unexpected_turn_before_queue'):
        facts().observe(turn(tid='foreign-turn', items=[]), source='turn/started')


def test_early_event_must_match_actual_rpc_id():
    value = NativeTurnFacts('first-client', 'queue-client')
    value.observe(turn(tid='wrong', items=[]), source='turn/started')
    with pytest.raises(IdentityConflict, match='early_turn_RPC_conflict'):
        value.bind_first('actual-first')


def test_event_user_client_can_correlate_later_partial_history():
    value = facts()
    value.observe(turn(), source='item/started')
    done = turn(client='omit', status='completed', completedAt=100)
    value.observe(done, source='turn/completed')
    assert value.terminal('first-client', done)


def test_queue_needs_own_client_not_just_real_completion():
    value = facts()
    value.queue_enabled = True
    done = turn(tid='actual-second', client='omit', status='completed', completedAt=100)
    value.observe(done, source='turn/completed')
    assert not value.terminal('queue-client', done)
    value.observe(turn(tid='actual-second', client='queue-client'), source='item/started')
    assert value.terminal('queue-client', done)


def test_direct_RPC_id_proves_first_without_optional_client():
    value = facts()
    done = turn(client='omit', status='completed', completedAt=100)
    assert not value.terminal('first-client', done)
    value.observe(done, source='turn/completed')
    assert value.terminal('first-client', done)


def test_optional_first_client_if_present_cannot_be_foreign():
    value = facts()
    with pytest.raises(IdentityConflict, match='unexpected_nonnull_client'):
        value.observe(turn(client='foreign', status='completed', completedAt=100), source='turn/completed')


def test_interrupted_null_and_conflicting_terminal_remain_unconfirmed():
    value = facts()
    value.observe(turn(), source='item/started')
    partial = turn(status='interrupted', completedAt=None)
    value.observe(partial, source='turn/completed')
    assert not value.terminal('first-client', partial)
    with pytest.raises(IdentityConflict, match='terminal_conflict'):
        value.terminal('first-client', turn(status='completed', completedAt=100))
