"""Explicit production continuation on original HTTP/WS/A2C/WASM/AP2 state."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import uuid

import pytest
from conftest import wait_for
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from test_assistant_role import user_decision
from test_resident_contracts import candidate, confirm
from test_resident_delegation import QuoteHost, participants
from test_resident_fulfillment import VERIFIER, accept_preview, invoke, operation
from test_task_verifiers import contract_state


class InterruptedProductionHost(QuoteHost):
    def __init__(self, *, host_status='failed', failed_rounds=1):
        super().__init__()
        self.host_status = host_status
        self.failed_rounds = failed_rounds
        self.production_turns = []
        self.failed_reports = []

    def run(self, turn):
        if json.loads(turn.input)['phase'] != 'production':
            return super().run(turn)
        self.turns.append(turn)
        self.production_turns.append(turn)
        turn.on_session(turn.session_id)
        failing = len(self.production_turns) <= self.failed_rounds
        content = 'candidate needing correction' if failing else 'actual file bytes'
        files = {'result.json': {'text': json.dumps({'delivered': content}), 'mediaType': 'application/json'}}
        result = turn.on_tool('provider.task.selftest', {'files': files}, 'selftest-' + str(len(self.production_turns)))
        if failing:
            assert result['status'] == 'fail', result
            self.failed_reports.append(copy.deepcopy(result))
            return HostResult(self.host_status, turn.session_id, {}, {'code': 'explicit_test_host_failure'},
                              request_id='known-production-round-' + str(len(self.production_turns)))
        assert result['status'] == 'pass', result
        assert turn.on_tool('provider.task.deliver', {'files': files}, 'deliver')['status'] == 'delivered'
        return HostResult('completed', turn.session_id, {'explicitProductionHostDouble': True},
                          request_id='known-production-round-' + str(len(self.production_turns)))


def snapshot(provider, reference):
    value = LocalClient(provider.config.home).call('provider.tasks.get', {'profile': 'service', 'taskRef': reference})
    assert value['status'] == 'completed', value
    return value['data']['items'][0]


def resume(provider, reference, operation_id, confirmation=None):
    arguments = {'profile': 'service', 'taskRef': reference}
    if confirmation is not None:
        arguments['confirmationRef'] = confirmation
    return LocalClient(provider.config.home).call('provider.task.resume', arguments, operation_id=operation_id)


def resumptions(provider):
    return provider.store.db.execute('SELECT COUNT(*) FROM runtime_job_resumptions').fetchone()[0]


def endpoint_for(provider, interaction):
    return next(value for value in provider.modules[0].execution.contexts.values() if value.interaction == interaction)


@contextmanager
def interrupted(platform, tmp_path, *, native=False, host_status='failed', failed_rounds=1):
    host = InterruptedProductionHost(host_status=host_status, failed_rounds=failed_rounds)
    business = {'native_tools': {'bash': 'allow'}} if native else None
    with participants(platform, tmp_path, agent_host=host, verifier_source=VERIFIER, business=business) as values:
        provider, assistant, _, template, task = values
        quote = candidate(assistant, provider, template, task)
        selected, _, _ = confirm(assistant, quote)
        contract = selected['data']['contractRef']
        wait_for(lambda: contract_state(assistant, contract).get('formed'))
        key, _ = invoke(assistant, contract, 'start')
        wait_for(lambda: operation(assistant, contract, key))
        def stopped_job():
            try:
                items = LocalClient(provider.config.home).call('provider.tasks.get', {'profile': 'service'})['data']['items']
            except RuntimeFault:
                # Preserve the failing public route; expose any internal exception
                # that the IPC boundary intentionally hides from its caller.
                provider.modules[0].execution.tasks.get(provider.identities[0])
                raise
            return next((item for item in items if item['phase'] == 'production' and item['status'] == 'unknown'), None)
        wait_for(stopped_job)
        wait_for(lambda: provider.scheduler.status()['active'] == 0)
        current = stopped_job()
        assert len(host.production_turns) == 1
        assert contract_state(assistant, contract)['availablePeriods'] == 0
        yield provider, assistant, host, contract, current


@pytest.mark.parametrize('restart', [False, True])
def test_verified_production_resumes_same_contract_session_and_completes_payment(platform, tmp_path, restart):
    with interrupted(platform, tmp_path) as (provider, assistant, host, contract, original):
        assert original['canResume'] and original['recoveryMode'] == 'runtime_verified', original
        assert not original['requiresConfirmation']
        failed = copy.deepcopy(host.failed_reports)
        before = contract_state(assistant, contract)['card']
        fresh = None
        if restart:
            config = provider.config
            provider.close()
            role = ProviderRole(host=host)
            fresh = ResidentRuntime(config, modules=(role,), host_status=host.capabilities).start()
            provider = fresh
            wait_for(lambda: provider.connection.connected)
            assert snapshot(provider, original['taskRef'])['recoveryMode'] == 'runtime_verified'
        try:
            op = str(uuid.uuid4())
            accepted = resume(provider, original['taskRef'], op)
            assert accepted['status'] == 'accepted', accepted
            wait_for(lambda: contract_state(assistant, contract).get('availablePeriods') == 1)
            current = snapshot(provider, original['taskRef'])
            assert current['status'] == 'completed' and current['sessionId'] == original['sessionId']
            assert all(turn.session_id == original['sessionId'] for turn in host.production_turns)
            assert len(host.production_turns) == 2 and host.failed_reports == failed
            state = contract_state(assistant, contract)
            assert state['interactionId'] == original['interactionId']
            for slot in ('1', '2', '3'):
                assert state['card']['signatures'][slot] == before['signatures'][slot]
            assert state['card']['acceptance'] == before['acceptance']
            key, _ = invoke(assistant, contract, 'fetch', period=1)
            wait_for(lambda: operation(assistant, contract, key))
            key, _ = invoke(assistant, contract, 'verify', period=1)
            wait_for(lambda: operation(assistant, contract, key))
            assert operation(assistant, contract, key)['data']['report']['status'] == 'pass'
            key, pending = accept_preview(assistant, contract)
            user_decision(assistant, pending['data']['confirmationRef'])
            LocalClient(assistant.config.home).call('assistant.delegation.accept', {'contractRef': contract, 'period': 1}, operation_id=key)
            wait_for(lambda: operation(assistant, contract, key))
            wait_for(lambda: contract_state(assistant, contract).get('closed'))
            assert contract_state(assistant, contract)['payment'] == 'paid'
            assert platform.market.reputation(provider.identities[0].protocol_id)['completed'] == 1
            assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?", provider.account.account['id'])) == 1
            assert len([turn for turn in host.turns if json.loads(turn.input)['phase'] == 'quote']) == 1
            assert resumptions(provider) == 1
        finally:
            if fresh:
                fresh.close()


def test_native_effects_require_actual_owner_confirmation_not_a_boolean(platform, tmp_path):
    with interrupted(platform, tmp_path, native=True) as (provider, assistant, host, contract, current):
        assert current['recoveryMode'] == 'owner_review' and current['requiresConfirmation']
        assert not current['canResume']
        op = str(uuid.uuid4())
        forged = LocalClient(provider.config.home).call('provider.task.resume', {'profile': 'service', 'taskRef': current['taskRef'], 'confirmed': True}, operation_id=str(uuid.uuid4()))
        assert forged['status'] == 'rejected' and resumptions(provider) == 0
        pending = resume(provider, current['taskRef'], op)
        assert pending['status'] == 'waiting', pending
        ref = pending['data']['confirmationRef']
        assert resume(provider, current['taskRef'], op)['data']['confirmationRef'] == ref
        assert resumptions(provider) == 0 and len(host.production_turns) == 1
        user_decision(provider, ref)
        accepted = resume(provider, current['taskRef'], op, ref)
        assert accepted['status'] == 'accepted', accepted
        wait_for(lambda: contract_state(assistant, contract).get('availablePeriods') == 1)
        assert resumptions(provider) == 1 and len(host.production_turns) == 2


@pytest.mark.parametrize('decision', ['reject', 'expire'])
def test_rejected_or_expired_owner_review_never_queues(platform, tmp_path, decision):
    with interrupted(platform, tmp_path, native=True) as (provider, _, host, _, current):
        op = str(uuid.uuid4())
        pending = resume(provider, current['taskRef'], op)
        assert pending['status'] == 'waiting', pending
        ref = pending['data']['confirmationRef']
        if decision == 'reject':
            user_decision(provider, ref, decision='reject')
        else:
            now = provider.confirmations.clock()
            provider.confirmations.clock = lambda: now + 86401
        result = resume(provider, current['taskRef'], op, ref)
        assert result['status'] in ('rejected', 'unknown'), result
        assert resumptions(provider) == 0 and len(host.production_turns) == 1


def test_unresolved_tool_effect_cannot_be_overridden_by_owner_review(platform, tmp_path):
    with interrupted(platform, tmp_path, native=True) as (provider, _, host, _, current):
        op = str(uuid.uuid4())
        pending = resume(provider, current['taskRef'], op)
        assert pending['status'] == 'waiting', pending
        ref = pending['data']['confirmationRef']
        user_decision(provider, ref)
        endpoint = endpoint_for(provider, current['interactionId'])
        key, value = next(iter(endpoint.store.all('production_calls').items()))
        for fault in ('missing_result', 'unknown_effect', 'observed_only', 'missing_context', 'missing_period', 'bad_fingerprint'):
            changed = copy.deepcopy(value)
            if fault in ('missing_result', 'unknown_effect', 'observed_only'):
                result = changed.pop('result')
                if fault == 'unknown_effect':
                    changed['state'] = 'unknown'
                elif fault == 'observed_only':
                    changed['observedResult'] = result
            elif fault == 'missing_context':
                changed.pop('context')
            elif fault == 'missing_period':
                changed['context'].pop('period')
            else:
                changed['fingerprint'] = 'sha256:' + '0' * 64
            endpoint.store.put('production_calls', key, changed)
            try:
                blocked = snapshot(provider, current['taskRef'])
                assert not blocked['canResume'] and blocked['recoveryMode'] is None, (fault, blocked)
                result = resume(provider, current['taskRef'], op, ref)
                assert result['status'] in ('rejected', 'unknown'), (fault, result)
                assert resumptions(provider) == 0 and len(host.production_turns) == 1, fault
            finally:
                endpoint.store.put('production_calls', key, value)


def test_changed_original_binding_or_existing_signed_delivery_blocks_resume(platform, tmp_path):
    with interrupted(platform, tmp_path) as (provider, _, host, contract, current):
        relation = provider.store.interaction(interaction_id=current['interactionId'])
        for fault in ('session', 'definition'):
            if fault == 'session':
                provider.store.db.execute('UPDATE runtime_interactions SET session=? WHERE id=?', ('different-session', current['interactionId']))
            else:
                details = {**relation['details'], 'definitionDigest': 'sha256:' + '0' * 64}
                provider.store.db.execute('UPDATE runtime_interactions SET details=? WHERE id=?', (json.dumps(details), current['interactionId']))
            try:
                value = resume(provider, current['taskRef'], str(uuid.uuid4()))
                assert value['status'] in ('rejected', 'unknown'), (fault, value)
                assert resumptions(provider) == 0 and len(host.production_turns) == 1, fault
            finally:
                provider.store.db.execute('UPDATE runtime_interactions SET session=?,details=? WHERE id=?',
                                          (relation['session'], json.dumps(relation['details']), current['interactionId']))
        endpoint = endpoint_for(provider, current['interactionId'])
        original_card = endpoint.store.get('cards', contract)
        invalid = copy.deepcopy(original_card)
        signature = invalid['card']['signatures']['1']['signature']
        invalid['card']['signatures']['1']['signature'] = ('A' if signature[0] != 'A' else 'B') + signature[1:]
        endpoint.store.put('cards', contract, invalid)
        try:
            # One invalid original must not turn the whole public task query
            # into an untyped IPC failure or offer a recovery confirmation.
            blocked = snapshot(provider, current['taskRef'])
            assert not blocked['canResume'] and blocked['recoveryMode'] is None, blocked
            value = resume(provider, current['taskRef'], str(uuid.uuid4()))
            assert value['status'] in ('rejected', 'unknown'), value
            assert resumptions(provider) == 0 and len(host.production_turns) == 1
        finally:
            endpoint.store.put('cards', contract, original_card)
        # A real signed delivery is irreversible, so check it after restoring
        # the purely local identity/definition fault injections above.
        endpoint = endpoint_for(provider, current['interactionId'])
        with endpoint.lock, endpoint.provider.transport.operation(str(uuid.uuid4())):
            delivered = endpoint.provider.deliver(contract, {'result.json': (b'{"delivered":"actual file bytes"}', 'application/json')})
        assert delivered['signatures']['4'] and delivered['signatures']['5']
        value = resume(provider, current['taskRef'], str(uuid.uuid4()))
        assert value['status'] in ('rejected', 'unknown'), value
        assert resumptions(provider) == 0 and len(host.production_turns) == 1


def test_lost_resume_response_and_parallel_retransmits_use_one_queue_receipt(platform, tmp_path, monkeypatch):
    with interrupted(platform, tmp_path) as (provider, assistant, host, contract, current):
        original = provider.scheduler.requeue
        calls = []
        def lose_once(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            raise RuntimeFault('unknown_result', 'durable resume acknowledgement lost')
        monkeypatch.setattr(provider.scheduler, 'requeue', lose_once)
        op = str(uuid.uuid4())
        assert resume(provider, current['taskRef'], op)['status'] == 'unknown'
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: resume(provider, current['taskRef'], op), range(4)))
        assert all(value['status'] == 'accepted' for value in results), results
        assert all(value['data']['resumeReceipt'] == calls[0] for value in results)
        wait_for(lambda: contract_state(assistant, contract).get('availablePeriods') == 1)
        assert len(calls) == 1 and resumptions(provider) == 1 and len(host.production_turns) == 2


def test_unknown_host_round_is_never_runtime_verified(platform, tmp_path):
    with interrupted(platform, tmp_path, host_status='unknown') as (provider, _, host, _, current):
        assert not current['canResume'] and current['recoveryMode'] != 'runtime_verified', current
        assert resumptions(provider) == 0 and len(host.production_turns) == 1


def test_legacy_missing_proof_requires_owner_review_without_inventing_terminal_evidence(platform, tmp_path):
    with interrupted(platform, tmp_path) as (provider, _, host, _, current):
        namespace = 'provider-production-recovery:' + provider.identities[0].agent_id
        provider.store.checkpoint_delete(namespace, 'proof:' + current['taskRef'])
        provider.store.checkpoint_delete(namespace, 'round:' + current['taskRef'])
        folder = Path(host.production_turns[0].workspace) / 'runs'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'model-written-claim.json').write_text(json.dumps({
            'operationId': current['taskRef'], 'sessionId': current['sessionId'],
            'state': 'completed', 'requestId': 'model-claimed-terminal-request',
            'createdAt': '2099-01-01T00:00:00Z', 'source': 'runtime_host_result',
        }), encoding='utf-8')
        legacy = snapshot(provider, current['taskRef'])
        assert legacy['recoveryMode'] == 'owner_review' and legacy['requiresConfirmation']
        assert not legacy['canResume']
        op = str(uuid.uuid4())
        pending = resume(provider, current['taskRef'], op)
        assert pending['status'] == 'waiting' and pending['data']['confirmationRef']
        assert provider.store.checkpoint(namespace, 'proof:' + current['taskRef']) is None
        detail = provider.confirmations.get(pending['data']['confirmationRef'], detail=True)
        assert detail['binding']['preview']['roundEvidence']['source'] == 'workspace_diagnostic_not_authoritative'
        assert resumptions(provider) == 0 and len(host.production_turns) == 1
        for index in range(128):
            (folder / ('extra-claim-' + str(index) + '.json')).write_text(json.dumps({
                'operationId': current['taskRef'], 'sessionId': current['sessionId'],
                'state': 'completed', 'requestId': 'claim-' + str(index),
                'createdAt': '2099-01-01T00:00:00Z',
            }), encoding='utf-8')
        too_many = snapshot(provider, current['taskRef'])
        assert too_many['recoveryMode'] is None and not too_many['canResume'], too_many
        assert too_many['resumeReason'] == 'not_ready', too_many
        blocked = resume(provider, current['taskRef'], op, pending['data']['confirmationRef'])
        assert blocked['status'] in ('rejected', 'unknown'), blocked
        assert resumptions(provider) == 0 and len(host.production_turns) == 1


def test_approved_review_cannot_be_reused_for_changed_preview_or_operation(platform, tmp_path):
    with interrupted(platform, tmp_path, native=True) as (provider, _, host, _, current):
        op = str(uuid.uuid4())
        pending = resume(provider, current['taskRef'], op)
        assert pending['status'] == 'waiting', pending
        ref = pending['data']['confirmationRef']
        user_decision(provider, ref)
        other = resume(provider, current['taskRef'], str(uuid.uuid4()), ref)
        assert other['status'] in ('rejected', 'unknown', 'waiting'), other
        if other['status'] == 'waiting':
            assert other['data']['confirmationRef'] != ref
        endpoint = endpoint_for(provider, current['interactionId'])
        key, value = next(iter(endpoint.store.all('production_calls').items()))
        changed = copy.deepcopy(value)
        changed['result']['checks'][0].append({'name': 'newly-observed-evidence', 'pass': False})
        endpoint.store.put('production_calls', key, changed)
        stale = resume(provider, current['taskRef'], op, ref)
        assert stale['status'] in ('rejected', 'unknown'), stale
        assert resumptions(provider) == 0 and len(host.production_turns) == 1
        artifact = Path(host.production_turns[0].workspace) / 'artifacts' / 'candidate.json'
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"candidate":"before review"}', encoding='utf-8')
        next_op = str(uuid.uuid4())
        pending = resume(provider, current['taskRef'], next_op)
        assert pending['status'] == 'waiting', pending
        artifact_ref = pending['data']['confirmationRef']
        user_decision(provider, artifact_ref)
        artifact.write_text('{"candidate":"changed after review"}', encoding='utf-8')
        stale = resume(provider, current['taskRef'], next_op, artifact_ref)
        assert stale['status'] in ('rejected', 'unknown'), stale
        assert resumptions(provider) == 0 and len(host.production_turns) == 1


def test_old_resume_receipt_cannot_start_another_unknown_round(platform, tmp_path):
    with interrupted(platform, tmp_path, failed_rounds=2) as (provider, assistant, host, contract, current):
        op = str(uuid.uuid4())
        accepted = resume(provider, current['taskRef'], op)
        assert accepted['status'] == 'accepted', accepted
        wait_for(lambda: len(host.production_turns) == 2 and snapshot(provider, current['taskRef'])['status'] == 'unknown')
        wait_for(lambda: provider.scheduler.status()['active'] == 0)
        repeated = resume(provider, current['taskRef'], op)
        assert repeated['status'] == 'accepted' and repeated['data']['resumeReceipt'] == accepted['data']['resumeReceipt']
        assert resumptions(provider) == 1 and len(host.production_turns) == 2
        next_round = resume(provider, current['taskRef'], str(uuid.uuid4()))
        assert next_round['status'] == 'accepted', next_round
        wait_for(lambda: contract_state(assistant, contract).get('availablePeriods') == 1)
        assert resumptions(provider) == 2 and len(host.production_turns) == 3
