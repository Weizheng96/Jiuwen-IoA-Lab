"""Public IPC/browser forms keep the original rule publication and user authority."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from urllib.parse import urlsplit

import httpx
import pytest

from conftest import wait_for
from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.runtime import ClientOperations, ResidentRuntime, RuntimeConfig
from test_resident_runtime import configuration


def rule_values():
    return {
        'name': 'travel.update', 'description': '接收旅行进展',
        'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                        'required': ['text'], 'additionalProperties': False},
        'outputSchema': {'type': 'object', 'properties': {'reply': {'type': 'string'}},
                         'required': ['reply'], 'additionalProperties': False},
        'allowedSenders': [], 'enabled': True, 'tools': [],
        'instructions': '根据消息文本回复，不执行外部工具。',
    }


def browser_session(runtime, url):
    browser = httpx.Client(base_url=runtime.user_ui.origin, trust_env=False)
    response = browser.post('/user/session', json={'token': urlsplit(url).fragment},
                            headers={'Origin': runtime.user_ui.origin})
    assert response.status_code == 200
    return browser, {'Origin': runtime.user_ui.origin, 'X-Ioa-Csrf': response.json()['csrf']}


def test_public_forms_preview_wait_for_browser_approval_and_recover_publication(platform, tmp_path, monkeypatch):
    path, _ = configuration(platform, tmp_path, service=False)
    launched = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launched.append(url) or True)
    runtime = ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)).start()
    try:
        wait_for(lambda: runtime.connection.connected)
        api = ClientOperations(path)
        opened = api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': rule_values()},
                          operation_id='original-open')
        assert opened['status'] == 'completed'
        form = opened['data']
        target = {'reference': form['reference'], 'revision': form['revision']}
        preview = api.call('runtime.forms.preview', target)
        assert preview['data']['preview']['preview']['previousRevision'] == 0
        pending = api.call('runtime.forms.submit', target)
        assert pending['data']['result']['status'] == 'waiting'
        confirmation = pending['data']['result']['data']['confirmationRef']
        assert api.call('confirmations.get', {'reference': confirmation})['data']['status'] == 'pending'
        browser_open = api.call('runtime.forms.open_ui', {'reference': form['reference']})
        assert browser_open['data'] == {'opened': True, 'reference': form['reference']}
        token = urlsplit(launched[-1]).fragment
        assert token not in json.dumps(browser_open)
        with httpx.Client(base_url=runtime.user_ui.origin, trust_env=False) as browser:
            entry = browser.post('/user/session', json={'token': token}, headers={'Origin': runtime.user_ui.origin})
            assert entry.status_code == 200
            record = api.call('confirmations.get', {'reference': confirmation})['data']
            decided = browser.post('/user/decision', json={'reference': confirmation,
                'bindingDigest': record['bindingDigest'], 'decision': 'approve'},
                headers={'Origin': runtime.user_ui.origin, 'X-Ioa-Csrf': entry.json()['csrf']})
            assert decided.status_code == 200
        wait_for(lambda: api.call('runtime.forms.get', {'reference': form['reference']})['data']['phase'] == 'completed')
        completed = api.call('runtime.forms.get', {'reference': form['reference']})
        assert completed['data']['result']['status'] == 'completed'
        assert completed['data']['result']['data']['revision'] == 1
        assert api.call('runtime.forms.submit', target)['data'] == completed['data']
    finally:
        runtime.close()
    # Restart the actual writer; neither MCP lifetime nor page state owns the result.
    runtime = ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)).start()
    try:
        api = ClientOperations(path)
        restored = api.call('runtime.forms.get', {'reference': form['reference']})['data']
        assert restored['result'] == completed['data']['result']
        assert api.call('runtime.forms.submit', target)['data']['result'] == restored['result']
        assert len(api.call('assistant.rules.list', {})['data']['versions']) == 1
    finally:
        runtime.close()


def test_form_revision_conflicts_preserve_original_input_and_preview(platform, tmp_path):
    path, _ = configuration(platform, tmp_path, service=False)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)):
        api = ClientOperations(path)
        form = api.call('runtime.forms.open', {'kind': 'messaging.interface'}, operation_id='draft')['data']
        target = {'reference': form['reference'], 'revision': 0}
        rejected = api.call('runtime.forms.preview', target)
        assert rejected['status'] == 'rejected'
        assert api.call('runtime.forms.get', {'reference': form['reference']})['data']['values'] == {}
        first = rule_values()
        second = {**first, 'description': '另一个并发修改'}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda values: api.call('runtime.forms.update', {**target, 'values': values}), (first, second)))
        assert sorted(item['status'] for item in results) == ['completed', 'rejected']
        changed = next(item['data'] for item in results if item['status'] == 'completed')
        assert changed['revision'] == 1
        assert api.call('runtime.forms.open', {'kind': 'messaging.interface'}, operation_id='draft')['data'] == changed
        assert api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': first}, operation_id='draft')['error']['code'] == 'id_conflict'
        assert api.call('runtime.forms.preview', target)['error']['code'] == 'id_conflict'
        assert api.call('runtime.forms.get', {'reference': form['reference']})['data'] == changed


def test_browser_form_requires_session_origin_csrf_and_cannot_approve(platform, tmp_path, monkeypatch):
    path, _ = configuration(platform, tmp_path, service=False)
    launches = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launches.append(url) or True)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)) as runtime:
        api = ClientOperations(path)
        form = api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': rule_values()})['data']
        api.call('runtime.forms.open_ui', {'reference': form['reference']})
        body = {'action': 'get', 'reference': form['reference']}
        with httpx.Client(base_url=runtime.user_ui.origin, trust_env=False) as outsider:
            assert outsider.post('/user/forms/api', json=body, headers={'Origin': runtime.user_ui.origin}).status_code == 403
        browser, headers = browser_session(runtime, launches[-1])
        with closing(browser):
            page = browser.get('/user/forms/' + form['reference'])
            assert page.status_code == 200 and '消息字段名' in page.text
            assert browser.post('/user/forms/api', json=body, headers=headers).json()['values'] == rule_values()
            assert browser.post('/user/forms/api', json=body, headers={**headers, 'Origin': 'https://foreign.invalid'}).status_code == 403
            assert browser.post('/user/forms/api', json=body, headers={**headers, 'X-Ioa-Csrf': 'wrong'}).status_code == 403
            assert browser.post('/user/forms/api', json={'action': 'approve', 'reference': form['reference']}, headers=headers).status_code == 400
            assert browser.post('/user/forms/api', json={'action': {}}, headers=headers).status_code == 400
            assert browser.post('/user/session', json={'token': urlsplit(launches[-1]).fragment}, headers=headers).status_code == 403
        target = {'reference': form['reference'], 'revision': form['revision']}
        assert api.call('runtime.forms.submit', {**target, 'confirmed': True})['error']['code'] == 'invalid_input'
        api.call('runtime.forms.preview', target)
        waiting = api.call('runtime.forms.submit', target)['data']
        repeated = api.call('runtime.forms.submit', target)['data']
        assert waiting['result'] == repeated['result'] and waiting['result']['status'] == 'waiting'
        assert api.call('runtime.forms.update', {**target, 'values': rule_values()})['error']['code'] == 'id_conflict'
        assert api.call('assistant.rules.list', {})['data']['versions'] == []


def test_lost_publication_response_recovers_same_form_and_same_platform_revision(platform, tmp_path, monkeypatch):
    path, _ = configuration(platform, tmp_path, service=False)
    launches = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launches.append(url) or True)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)) as runtime:
        wait_for(lambda: runtime.connection.connected)
        api = ClientOperations(path)
        form = api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': rule_values()})['data']
        target = {'reference': form['reference'], 'revision': 0}
        api.call('runtime.forms.preview', target)
        pending = api.call('runtime.forms.submit', target)['data']['result']
        confirmation = api.call('confirmations.get', {'reference': pending['data']['confirmationRef']})['data']
        api.call('runtime.forms.open_ui', {'reference': form['reference']})
        browser, headers = browser_session(runtime, launches[-1])
        with closing(browser):
            assert browser.post('/user/decision', json={'reference': confirmation['reference'],
                'bindingDigest': confirmation['bindingDigest'], 'decision': 'approve'}, headers=headers).status_code == 200
        real_send = httpx.Client.send
        dropped = []
        def lose_response(client, request, **kwargs):
            response = real_send(client, request, **kwargs)
            if request.method == 'POST' and request.url.path == '/api/v1/messaging/interfaces' and not dropped:
                dropped.append(response.status_code)
                response.close()
                raise httpx.ReadTimeout('isolated lost response', request=request)
            return response
        monkeypatch.setattr(httpx.Client, 'send', lose_response)
        assert api.call('runtime.forms.submit', target)['status'] == 'unknown'
        assert dropped == [200]
        original = api.call('runtime.forms.get', {'reference': form['reference']})['data']
        assert original['phase'] == original['result']['status'] == 'unknown'
        recovered = api.call('runtime.forms.submit', target)['data']['result']
        assert recovered['status'] == 'completed' and recovered['data']['revision'] == 1
        assert len(api.call('assistant.rules.list', {})['data']['versions']) == 1


def test_single_stage_rejection_automatically_finishes_without_publication(platform, tmp_path):
    from test_assistant_role import user_decision
    path, _ = configuration(platform, tmp_path, service=False)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)) as runtime:
        wait_for(lambda: runtime.connection.connected)
        api = ClientOperations(path)
        form = api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': rule_values()})['data']
        target = {'reference': form['reference'], 'revision': 0}
        api.call('runtime.forms.preview', target)
        pending = api.call('runtime.forms.submit', target)['data']
        user_decision(runtime, pending['result']['data']['confirmationRef'], decision='reject')
        wait_for(lambda: api.call('runtime.forms.get', {'reference': form['reference']})['data']['phase'] == 'rejected')
        assert api.call('assistant.rules.list', {})['data']['versions'] == []
