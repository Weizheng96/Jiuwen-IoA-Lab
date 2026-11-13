"""Complete role workflows through the public form API and original platform protocols."""
from conftest import wait_for
from ioalab_sdk.runtime import ClientOperations
from test_assistant_role import runtime_at, user_decision
from test_interaction_forms import rule_values


def step(api, form, values=None):
    target = {'reference': form['reference'], 'revision': form['revision']}
    if values is not None:
        saved = api.call('runtime.forms.update', {**target, 'values': values})
        assert saved['status'] == 'completed', saved
        target['revision'] = saved['data']['revision']
    preview = api.call('runtime.forms.preview', target)
    assert preview['status'] == 'completed', preview
    result = api.call('runtime.forms.submit', target)
    assert result['status'] == 'completed', result
    return result['data']


def test_private_workflow_discovers_schema_sends_and_recovers_same_message(platform, tmp_path):
    import json
    from ioalab_sdk.runtime import HostResult
    class EchoHost:
        def __init__(self): self.turns = []
        def capabilities(self): return {'available': True, 'testDouble': True}
        def cancel(self, operation): return True
        def run(self, turn):
            self.turns.append(turn)
            session = turn.session_id or 'isolated-private-conversation'
            turn.on_session(session)
            incoming = json.loads(turn.input.split('\n', 1)[1])['input']
            turn.on_tool('ioa_reply', {'output': {'reply': incoming['text']}}, 'reply')
            return HostResult('completed', session, {'testDouble': True})
    host = EchoHost()
    with runtime_at(platform, tmp_path / 'recipient', host) as (recipient, _, _, user):
        recipient_api = ClientOperations(recipient.config.source)
        values = {**rule_values(), 'allowedSenders': None}
        rule = recipient_api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': values})['data']
        waiting = step(recipient_api, rule)
        user_decision(recipient, waiting['result']['data']['confirmationRef'])
        published = step(recipient_api, waiting)
        assert published['phase'] == 'completed'
        with runtime_at(platform, tmp_path / 'sender') as (sender, _, _, _):
            api = ClientOperations(sender.config.source)
            form = api.call('runtime.forms.open', {'kind': 'messaging.interaction'})['data']
            choose = step(api, form, {'account': user['username']})
            reference = choose['schema']['properties']['interfaceRef']['enum'][0]
            compose = step(api, choose, {'interfaceRef': reference})
            assert compose['schema']['properties']['input']['required'] == ['text']
            partial = api.call('runtime.forms.update', {'reference': compose['reference'], 'revision': compose['revision'], 'values': {'input': {}}})
            assert partial['status'] == 'completed'
            compose = partial['data']
            assert api.call('runtime.forms.preview', {'reference': compose['reference'], 'revision': compose['revision']})['status'] == 'rejected'
            follow = step(api, compose, {'input': {'text': '分享旅行计划'}})
            message = follow['evidence']['messageRef']
            restored = api.call('runtime.forms.get', {'reference': form['reference']})['data']
            assert restored['evidence']['messageRef'] == message
            assert restored['schema']['properties']['action']['enum'][0] == 'refresh'
            refreshed = step(api, restored)
            assert refreshed['evidence']['messageRef'] == message
            # The next round is offered only after the original platform opening receipt arrives.
            wait_for(lambda: api.call('assistant.messaging.status', {'messageRef': message})['data']['result'])
            refreshed = step(api, refreshed)
            assert refreshed['evidence']['result']['output'] == {'reply': '分享旅行计划'}
            reply = step(api, refreshed, {'action': 'reply'})
            second = step(api, reply, {'input': {'text': '补充预算与出发时间'}})
            assert second['evidence']['messageRef'] != message
            assert second['evidence']['interactionRef'] == refreshed['evidence']['interactionRef']
            wait_for(lambda: api.call('assistant.messaging.status', {'messageRef': second['evidence']['messageRef']})['data']['result'])
            second = step(api, second)
            assert second['evidence']['result']['output'] == {'reply': '补充预算与出发时间'}
            assert len(host.turns) == 2 and host.turns[1].session_id == 'isolated-private-conversation'
            closed = step(api, second, {'action': 'close'})
            assert closed['phase'] == 'completed' and closed['result']['data']['closeRequested']


def test_trade_form_uses_a2c_field_domains_and_omits_template_constants(platform, tmp_path):
    from test_resident_delegation import participants
    from test_provider_authoring import terms
    def populated_terms(_):
        value = terms()
        value['deliverables'] = [{'name': 'result', 'description': '受约束交付', 'specSchema': {
            'format': {'const': 'text'}, 'topic': {'type': 'string', 'maxLength': 20},
            'copies': {'type': 'integer', 'minimum': 1, 'maximum': 3}, 'language': {'enum': ['zh', 'en']}}}]
        value['pricing']['paymentWeights'] = {'result': 1}
        value['deadlines']['deliverWithin'] = {'result': 'PT30M'}
        return value
    with participants(platform, tmp_path, template_terms=populated_terms) as (provider, assistant, _, template, _):
        api = ClientOperations(assistant.config.source)
        form = api.call('runtime.forms.open', {'kind': 'delegation.trade'})['data']
        form = step(api, form, {'purpose': '按规格制作文本', 'budget': 50, 'proposalFeeLimit': 0, 'query': '专业服务'})
        user_decision(assistant, form['result']['data']['confirmationRef'])
        form = step(api, form)
        form = step(api, form, {'action': 'select', 'service': provider.identities[0].agent_id})
        form = step(api, form, {'templateDigest': template['templateDigest']})
        fields = form['schema']['properties']['spec']['properties']['result']
        assert fields['type'] == 'object'
        assert set(fields['properties']) == {'topic', 'copies', 'language'}
        assert set(fields['required']) == {'topic', 'copies', 'language'}
        assert fields['additionalProperties'] is False
        values = {'topic': '验收', 'copies': 2, 'language': 'zh'}
        target = {'reference': form['reference'], 'revision': form['revision']}
        for bad in ({**values, 'format': 'text'}, {**values, 'copies': 4}, {**values, 'language': 'fr'}):
            rejected = api.call('runtime.forms.update', {**target, 'values': {'spec': {'result': bad}}})
            assert rejected['status'] == 'rejected', rejected
        saved = api.call('runtime.forms.update', {**target, 'values': {'spec': {'result': values}}})
        assert saved['status'] == 'completed', saved
        target['revision'] = saved['data']['revision']
        assert api.call('runtime.forms.preview', target)['status'] == 'completed'


def test_trade_workflow_keeps_task_and_contract_approval_separate(platform, tmp_path):
    from test_resident_delegation import participants
    with participants(platform, tmp_path, formation='countersign', fee=0) as (provider, assistant, _, template, _):
        api = ClientOperations(assistant.config.source)
        form = api.call('runtime.forms.open', {'kind': 'delegation.trade'})['data']
        brief = step(api, form, {'purpose': '制作通用业务交付', 'budget': 50, 'proposalFeeLimit': 0, 'query': '专业服务'})
        assert brief['phase'] == 'waiting'
        user_decision(assistant, brief['result']['data']['confirmationRef'])
        service = step(api, brief)
        assert provider.identities[0].agent_id in service['schema']['properties']['service']['enum']
        templates = step(api, service, {'action': 'select', 'service': provider.identities[0].agent_id})
        spec = step(api, templates, {'templateDigest': template['templateDigest']})
        quote = step(api, spec, {'spec': {'result': {}}})
        assert quote['title'] == '比较报价与选择合同'
        ref = quote['schema']['properties']['quoteRef']['enum'][0]
        contract = step(api, quote, {'action': 'select', 'quoteRef': ref})
        assert contract['phase'] == 'waiting'
        assert contract['result']['data']['confirmationRef'] != brief['result']['data']['confirmationRef']
        assert len(assistant.confirmations.list()) >= 2


def test_workflow_reaches_real_delivery_step_acceptance_and_payment(platform, tmp_path, monkeypatch):
    from test_resident_delegation import participants
    from test_resident_fulfillment import ProductionHost, VERIFIER, configure_template
    configure_template(monkeypatch)
    with participants(platform, tmp_path, formation='countersign', fee=0,
                      agent_host=ProductionHost(), verifier_source=VERIFIER) as (provider, assistant, host, template, _):
        api = ClientOperations(assistant.config.source)
        def pending(form, predicate):
            found = []
            def check():
                current = step(api, form)
                assert current['phase'] not in ('failed', 'rejected', 'unknown'), current
                if predicate(current): found.append(current); return True
                return False
            wait_for(check)
            return found[0]
        form = api.call('runtime.forms.open', {'kind': 'delegation.trade'})['data']
        form = step(api, form, {'purpose': '制作业务交付', 'budget': 50, 'proposalFeeLimit': 0, 'query': '专业服务'})
        user_decision(assistant, form['result']['data']['confirmationRef'])
        form = step(api, form)
        form = step(api, form, {'action': 'select', 'service': provider.identities[0].agent_id})
        form = step(api, form, {'templateDigest': template['templateDigest']})
        form = step(api, form, {'spec': {'result': {}}})
        form = step(api, form, {'action': 'select', 'quoteRef': form['schema']['properties']['quoteRef']['enum'][0]})
        user_decision(assistant, form['result']['data']['confirmationRef'])
        form = pending(form, lambda f: f['phase'] == 'draft')
        # Refresh uses a new UI revision, never another start/contract operation.
        for _ in range(20):
            if form['evidence']['合同状态'].get('stepRequestDigest'): break
            form = step(api, form, {'action': 'refresh'})
        assert form['evidence']['合同状态']['stepRequestDigest']
        form = step(api, form, {'action': 'answer_step'})
        assert form['phase'] == 'waiting'
        user_decision(assistant, form['result']['data']['confirmationRef'], decision='answer', answers=[{'selected_options': ['allow']}])
        form = pending(form, lambda f: f['phase'] == 'draft')
        for _ in range(40):
            if form['evidence']['合同状态']['availablePeriods']: break
            form = step(api, form, {'action': 'refresh'})
        assert form['evidence']['合同状态']['availablePeriods'] == 1
        form = step(api, form, {'action': 'accept', 'period': 1})
        if form['phase'] != 'waiting': form = pending(form, lambda f: f['phase'] == 'waiting')
        user_decision(assistant, form['result']['data']['confirmationRef'])
        form = pending(form, lambda f: f['phase'] == 'draft')
        assert form['evidence']['合同状态']['acceptedPeriods'] == 1
        assert form['evidence']['合同状态']['payment'] == 'paid'


def test_provider_management_pause_and_resume_use_original_admission_policy(platform, tmp_path):
    from test_resident_delegation import participants
    with participants(platform, tmp_path) as (provider, _, _, _, _):
        api = ClientOperations(provider.config.source)
        form = api.call('runtime.forms.open', {'kind': 'services.management'})['data']
        form = step(api, form, {'profile': 'service'})
        form = step(api, form, {'action': 'pause'})
        assert form['evidence']['服务状态']['admissionPolicy']['accepting'] is False
        form = step(api, form, {'action': 'resume'})
        assert form['evidence']['服务状态']['admissionPolicy']['accepting'] is True


def test_service_publication_form_drafts_terms_previews_and_waits_for_approval(platform, tmp_path):
    from test_resident_delegation import participants
    with participants(platform, tmp_path) as (provider, _, _, template, _):
        api = ClientOperations(provider.config.source)
        form = api.call('runtime.forms.open', {'kind': 'services.management'})['data']
        form = step(api, form, {'profile': 'service'})
        form = step(api, form, {'action': 'publish'})
        terms = {key: value for key, value in template.items() if key in ('formation','deliverables','inputs','deadlines','pricing','acceptance','authorizableSteps')}
        terms['pricing'] = {key: value for key, value in terms['pricing'].items() if key != 'paymentProviders'}
        pending = step(api, form, {'name': '更新后的通用服务', 'description': '根据需求生产业务交付', 'tags': ['交付'],
                                  'categoryId': form['schema']['properties']['categoryId']['enum'][0], 'terms': terms})
        assert pending['phase'] == 'waiting'
        user_decision(provider, pending['result']['data']['confirmationRef'])
        wait_for(lambda: api.call('runtime.forms.get', {'reference': form['reference']})['data']['phase'] == 'draft')
        current = api.call('runtime.forms.get', {'reference': form['reference']})['data']
        assert current['evidence']['服务状态']['revision'] == 2


def test_existing_interface_can_be_disabled_through_registry_workflow(platform, tmp_path):
    with runtime_at(platform, tmp_path / 'owner') as (runtime, _, _, _):
        api = ClientOperations(runtime.config.source)
        form = api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': rule_values()})['data']
        form = step(api, form)
        user_decision(runtime, form['result']['data']['confirmationRef'])
        step(api, form)
        manage = api.call('runtime.forms.open', {'kind': 'registry.interfaces'})['data']
        manage = step(api, manage, {'action': 'manage'})
        manage = step(api, manage, {'name': 'travel.update', 'action': 'disable'})
        assert manage['values']['enabled'] is False
        pending = step(api, manage)
        user_decision(runtime, pending['result']['data']['confirmationRef'])
        # Closing the UI does not prevent already-confirmed publication from finishing.
        wait_for(lambda: api.call('runtime.forms.get', {'reference': manage['reference']})['data']['phase'] == 'completed')
        result = api.call('runtime.forms.get', {'reference': manage['reference']})['data']
        assert result['result']['data']['revision'] == 2


def test_original_contract_case_form_preserves_confirmation_and_provider_denial(platform, tmp_path, monkeypatch):
    from test_arbitration_cases import due, formed
    from test_resident_contracts import candidate
    from test_resident_delegation import participants
    monkeypatch.setattr(platform.arbitration_cases, 'process_one', lambda: False)
    with participants(platform, tmp_path, formation='countersign', fee=0) as (provider, assistant, _, template, task):
        reference, admission = formed(assistant, candidate(assistant, provider, template, task))
        due(platform, assistant, admission, monkeypatch)
        api = ClientOperations(assistant.config.source)
        form = api.call('runtime.forms.open', {'kind': 'delegation.case'})['data']
        form = step(api, form, {'object': 'contract', 'reference': reference})
        form = step(api, form, {'ground': 'start-overdue'})
        form = step(api, form)
        assert form['phase'] == 'waiting'
        user_decision(assistant, form['result']['data']['confirmationRef'])
        wait_for(lambda: api.call('runtime.forms.get', {'reference': form['reference']})['data']['phase'] == 'draft')
        current = api.call('runtime.forms.get', {'reference': form['reference']})['data']
        case = current['evidence']['caseRef']
        assert current['evidence']['state'] == 'queued'
        assert step(api, current)['evidence']['caseRef'] == case
        provider_api = ClientOperations(provider.config.source)
        form = provider_api.call('runtime.forms.open', {'kind': 'services.management'})['data']
        form = step(provider_api, form, {'profile': 'service'})
        form = step(provider_api, form, {'action': 'tasks'})
        form = step(provider_api, form, {'action': 'dispute', 'taskRef': form['schema']['properties']['taskRef']['enum'][0]})
        rejected = step(provider_api, form, {'ground': 'accept-overdue', 'period': 1})
        assert rejected['phase'] == 'rejected'
        assert rejected['result']['data']['eligible'] is False


def test_overdue_paid_proposal_uses_original_quote_without_a_contract(platform, tmp_path, monkeypatch):
    import threading
    from a2c.codec import parse_time
    from test_arbitration_cases import preview, actor
    from test_resident_delegation import participants, request, terminal
    monkeypatch.setattr(platform.arbitration_cases, 'process_one', lambda: False)
    with participants(platform, tmp_path, fee=1) as (provider, assistant, host, template, task):
        host.wait = threading.Event()
        started, _ = request(assistant, provider, template, task)
        quote = started['data']['quoteRef']
        try:
            wait_for(lambda: len(host.turns) == 1)
            admission = platform.db.one('SELECT id FROM delegation_admissions WHERE client=?', actor(assistant))['id']
            until = parse_time(preview(assistant, admission, 'proposal-overdue')['deadline']).timestamp() + 1
            monkeypatch.setattr(platform.arbitration_evidence, 'clock', lambda: until)
            api = ClientOperations(assistant.config.source)
            form = api.call('runtime.forms.open', {'kind': 'delegation.case'})['data']
            form = step(api, form, {'object': 'quote', 'reference': quote})
            form = step(api, form, {'ground': 'proposal-overdue'})
            form = step(api, form)
            assert form['phase'] == 'waiting'
            user_decision(assistant, form['result']['data']['confirmationRef'])
            wait_for(lambda: api.call('runtime.forms.get', {'reference': form['reference']})['data']['phase'] == 'draft')
            case = api.call('runtime.forms.get', {'reference': form['reference']})['data']['evidence']
            assert case['subject']['kind'] == 'proposal-request' and case['period'] is None
            assert case['ground'] == 'proposal-overdue'
        finally: host.wait.set()
        wait_for(lambda: terminal(assistant, quote))
