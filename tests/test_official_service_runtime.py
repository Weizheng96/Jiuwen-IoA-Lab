"""Actual resource-to-WS/A2C/WASM/AP2 lifecycle; user channel is a scripted browser."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import uuid

import pytest
from ioalab_services import prepare
from ioalab_services.catalog import definitions, seed_manifest
from ioalab_sdk.assistant.role import AssistantRole
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json, read_json
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.resident import ResidentRuntime
from conftest import wait_for
from test_resident_runtime import configuration
from test_assistant_role import user_decision
from test_resident_contracts import confirm, status
from test_resident_fulfillment import invoke, operation, accept_preview


class SampleHost:
    """Explicit deterministic double; no claim of model production."""
    def capabilities(self): return {'available': True, 'testDouble': True}
    def cancel(self, operation): return True
    def run(self, turn):
        turn.on_session(turn.session_id or 'official-test-' + str(uuid.uuid4()))
        phase = json.loads(turn.input)['phase']
        if phase == 'decision':
            result = turn.on_tool('provider.task.decision', {'approved': True}, 'decision')
            assert result['status'] == 'recorded', result
        else:
            assert phase == 'production'
            sample = next(item['sample'] for item in definitions() if item['id'] == 'wechat-posts')
            files = {'posts.json': {'text': json.dumps(sample, ensure_ascii=False), 'mediaType': 'application/json'}}
            tested = turn.on_tool('provider.task.selftest', {'files': files}, 'selftest')
            assert tested['status'] == 'pass', tested
            delivered = turn.on_tool('provider.task.deliver', {'files': files}, 'delivery')
            assert delivered['status'] == 'delivered', delivered
        return HostResult('completed', turn.session_id, {'explicitTestDouble': True})


@contextmanager
def owned_participants(platform, path, host):
    pconfig, _ = configuration(platform, path / 'provider')
    config = read_json(pconfig)
    config['identities'] = [config['identities'][1]]
    config['provider'] = {'services': {'service': {'instructions': '待装配业务资源', 'skills': [],
                                                 'max_active_delegations': 1, 'accepting': False}}}
    private_json(pconfig, config)
    role = ProviderRole(host)
    provider = ResidentRuntime(RuntimeConfig.load(pconfig), modules=(role,), host_status=host.capabilities).start()
    aconfig, _ = configuration(platform, path / 'assistant', service=False)
    assistant = ResidentRuntime(RuntimeConfig.load(aconfig), modules=(AssistantRole(),)).start()
    try:
        wait_for(lambda: provider.connection.connected and assistant.connection.connected)
        definition = next(item for item in definitions() if item['id'] == 'wechat-posts')
        verifier = definition['verifier']
        rpc = LocalClient(provider.config.home)
        uploaded = rpc.call('provider.verifier.publish', {'profile': 'service', 'verifierId': verifier['id'],
            'source': verifier['source'], 'reads': verifier['reads'], 'kind': 'script'})
        assert uploaded['status'] == 'completed', uploaded
        prepared = prepare('wechat-posts', profile='service', verifier_ref=uploaded['data'],
                           payment_provider=platform.payment_provider)
        checked = rpc.call('provider.preview', {'service': prepared['service']})
        assert checked['status'] == 'completed', checked
        args = {'service': checked['data']['service'], 'contentDigest': checked['data']['contentDigest'],
                'expectedRevision': checked['data']['expectedRevision']}
        operation_id = str(uuid.uuid4())
        pending = rpc.call('provider.publish', args, operation_id=operation_id)
        assert pending['status'] == 'waiting', pending
        user_decision(provider, pending['data']['confirmationRef'])
        published = rpc.call('provider.publish', args, operation_id=operation_id)
        assert published['status'] == 'completed', published
        config['provider']['services']['service'] = prepared['execution']
        private_json(pconfig, config)
        provider.close()
        role = ProviderRole(host)
        provider = ResidentRuntime(RuntimeConfig.load(pconfig), modules=(role,), host_status=host.capabilities).start()
        wait_for(lambda: provider.connection.connected and role.ready('service'))
        yield provider, assistant, published['data']['publicationReceipt']['templates'][0]
    finally:
        assistant.close()
        provider.close()


@pytest.mark.parametrize('real', [False, True])
def test_official_service_uses_ordinary_sdk_publication_execution_and_payment(platform, tmp_path, real):
    if real and not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'):
        pytest.skip('requires explicitly configured existing original Jiuwen/model')
    if real:
        from ioalab_jiuwen.process import ProcessHost
        host = ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),
            os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'], model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
        assert host.probe()['available'], host.capabilities()
    else:
        host = SampleHost()
    turns, calls = [], []
    original = host.run
    def execute(turn):
        turns.append(turn)
        original_tool = turn.on_tool
        def call(name, args, key):
            result = original_tool(name, args, key)
            calls.append({'name': name, 'status': result.get('status')})
            return result
        return original(replace(turn, on_tool=call))
    host.run = execute
    with owned_participants(platform, tmp_path, host) as (provider, assistant, template):
        client = LocalClient(assistant.config.home)
        task = client.call('assistant.delegation.prepare', {'purpose': '为小满美甲写春日朋友圈文案',
            'budget': 50, 'proposalFeeLimit': 0})['data']['taskRef']
        operation_id = str(uuid.uuid4())
        pending = client.call('assistant.delegation.authorize', {'taskRef': task}, operation_id=operation_id)
        user_decision(assistant, pending['data']['confirmationRef'])
        assert client.call('assistant.delegation.authorize', {'taskRef': task}, operation_id=operation_id)['status'] == 'completed'
        quote = client.call('assistant.delegation.request', {'taskRef': task, 'service': provider.identities[0].agent_id,
            'templateDigest': template['templateDigest'], 'spec': {'posts': {'shop': '小满美甲', 'theme': '春日上新'}}})
        assert quote['status'] == 'completed', quote
        selected, _, _ = confirm(assistant, quote['data'])
        reference = selected['data']['contractRef']
        def drive(check):
            pending = [value for value in provider.confirmations.list() if value['status'] == 'pending']
            assert not pending, {'unexpectedNativeQuestions': pending}
            return check()
        wait_for(lambda: drive(lambda: status(assistant, reference)), timeout=180 if real else 20)
        assert status(assistant, reference)['data']['formed'], status(assistant, reference)
        key, _ = invoke(assistant, reference, 'start')
        wait_for(lambda: operation(assistant, reference, key))
        wait_for(lambda: drive(lambda: status(assistant, reference)['data']['availablePeriods'] == 1), timeout=180 if real else 20)
        key, _ = invoke(assistant, reference, 'fetch', period=1)
        wait_for(lambda: operation(assistant, reference, key))
        key, pending = accept_preview(assistant, reference)
        preview = assistant.confirmations.get(pending['data']['confirmationRef'], detail=True)['binding']['preview']
        assert preview['report']['status'] == 'pass' and preview['amount'] == 6
        user_decision(assistant, pending['data']['confirmationRef'])
        args = {'contractRef': reference, 'period': 1}
        client.call('assistant.delegation.accept', args, operation_id=key)
        wait_for(lambda: operation(assistant, reference, key))
        assert operation(assistant, reference, key)['status'] == 'completed', operation(assistant, reference, key)
        wait_for(lambda: status(assistant, reference)['data']['closed'])
        actual = status(assistant, reference)['data']
        assert actual['payment'] == 'paid'
        client.call('assistant.delegation.accept', args, operation_id=key)
        payments = platform.db.rows('SELECT purpose,amount FROM payment_records WHERE ref=? ORDER BY purpose', reference)
        assert payments == [{'purpose': 'installment', 'amount': '6.00'}], payments
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed'] == 1
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?', actual['admissionId'])['state'] == 'released'
        assert len(turns) >= 2 and {json.loads(turn.input)['phase'] for turn in turns} == {'decision', 'production'}
        session = provider.store.interaction(interaction_id=actual['interactionId'])['session']
        assert all(turn.session_id in (None, session) for turn in turns)
        assert all('sample' not in turn.definition.instructions for turn in turns)
        if real and os.environ.get('IOALAB_REAL_OFFICIAL_REPORT'):
            private_json(Path(os.environ['IOALAB_REAL_OFFICIAL_REPORT']), {
                'scope': 'Owned packaged service on ordinary SDK, real original Jiuwen, HTTP/WS/A2C/WASM/AP2; scripted trusted browser confirmations; not native front-end workflow, bulk defaults or film acceptance',
                'service': 'wechat-posts', 'formation': template['formation'], 'model': host.capabilities()['model'],
                'resourceSources': seed_manifest()['source'], 'sampleGivenToModel': False, 'interactionId': actual['interactionId'],
                'sessionId': session, 'cardDigest': reference, 'calls': calls, 'paymentRecords': payments,
                'closed': True, 'reputationCompleted': 1, 'capacityReleased': True})
