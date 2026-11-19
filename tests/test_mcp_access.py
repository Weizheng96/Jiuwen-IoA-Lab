"""The public stdio MCP entry uses the existing local runtime, never a second writer."""
import asyncio
import pytest
from test_interaction_forms import rule_values
from datetime import timedelta
import json
import os
import sys
from pathlib import Path
import ioalab_sdk

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from conftest import wait_for
from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.runtime import ResidentRuntime, RuntimeConfig
from test_resident_runtime import configuration


def test_standard_stdio_tools_and_app_resource_preserve_runtime(platform, tmp_path, monkeypatch):
    path, _ = configuration(platform, tmp_path, service=False)
    launches = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launches.append(url) or True)
    runtime = ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)).start()
    try:
        wait_for(lambda: runtime.connection.connected)
        boot = runtime.status()['bootId']

        async def exercise():
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'ioalab_sdk.mcp', '--config', str(path)], env={**os.environ, 'PYTHONPATH': str(Path(ioalab_sdk.__file__).resolve().parent.parent)})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                    await session.initialize()
                    tools = {item.name: item for item in (await session.list_tools()).tools}
                    assert 'ioa_open_form' in tools and 'ioa_submit_form' in tools
                    assert 'ui' not in (tools['ioa_open_form'].meta or {})
                    assert not any('approve' in name or 'execute' in name for name in tools)
                    config = await session.call_tool('ioa_configuration', {})
                    assert not config.isError and 'password' not in json.dumps(config.model_dump()).lower()
                    form = await session.call_tool('ioa_open_form', {'kind': 'messaging.interface'})
                    assert not form.isError and form.structuredContent['data']['phase'] == 'draft'
                    assert launches and launches[-1].split('#')[1] not in json.dumps(form.model_dump())
                    resource = await session.read_resource('ui://ioalab/forms/v1.html')
                    assert resource.contents[0].mimeType == 'text/html;profile=mcp-app'
                    assert 'ui/initialize' in resource.contents[0].text
                    assert 'http://127.0.0.1' not in resource.contents[0].text
                    bad = await session.call_tool('ioa_submit_form', {'reference': 'missing', 'revision': 0, 'approve': True})
                    assert bad.isError
                    from test_interaction_forms import rule_values
                    ref = form.structuredContent['data']['reference']
                    updated = await session.call_tool('ioa_update_form', {'reference': ref, 'revision': 0, 'values': rule_values()})
                    target = {'reference': ref, 'revision': updated.structuredContent['data']['revision']}
                    previewed = await session.call_tool('ioa_preview_form', target)
                    assert not previewed.isError, previewed.structuredContent
                    pending = await session.call_tool('ioa_submit_form', target)
                    assert pending.structuredContent['data']['phase'] == 'waiting', pending.structuredContent
                    opened = await session.call_tool('ioa_open_browser', {'reference': ref})
                    assert not opened.isError
                    assert launches[-1].split('#')[0] == runtime.user_ui.origin + '/user'
                    assert launches[-1].split('#')[1] not in json.dumps(opened.model_dump())
                    status = await session.call_tool('ioa_status', {})
                    assert status.structuredContent['data']['bootId'] == boot
        asyncio.run(exercise())
        assert runtime.status()['bootId'] == boot and runtime.status()['ipcReady']
    finally:
        runtime.close()


def test_app_metadata_requires_capability_and_preserves_no_ui_mode(tmp_path):
    # Initialization and static resources do not require an installed/running account.
    async def exercise():
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'ioalab_sdk.mcp', '--config', str(tmp_path/'missing.json')], env={**os.environ, 'PYTHONPATH': str(Path(ioalab_sdk.__file__).resolve().parent.parent)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                await session.send_request(types.ClientRequest(types.InitializeRequest(params=types.InitializeRequestParams(
                    protocolVersion='2025-11-25', capabilities=types.ClientCapabilities(
                        extensions={'io.modelcontextprotocol/ui': {'mimeTypes': ['text/html;profile=mcp-app']}}),
                    clientInfo=types.Implementation(name='isolated-app-host',version='1')))), types.InitializeResult)
                await session.send_notification(types.ClientNotification(types.InitializedNotification()))
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                setup = await session.call_tool('ioa_setup_status', {})
                assert not setup.isError and setup.structuredContent['data']['phase'] == 'input'
                secret_input = await session.call_tool('ioa_setup', {'password': 'must-never-be-accepted'})
                assert secret_input.isError
                assert tools['ioa_open_form'].meta['ui']['resourceUri'] == 'ui://ioalab/forms/v1.html'
                absent = await session.call_tool('ioa_status', {})
                assert absent.isError and absent.structuredContent['status'] == 'rejected'
                bad = await session.call_tool('ioa_configuration', {'password': 'never-echo-this'})
                assert bad.isError and 'never-echo-this' not in json.dumps(bad.model_dump())
    asyncio.run(exercise())


def test_apps_keep_forms_inline_and_require_view_navigation(platform, tmp_path, monkeypatch):
    """Real stdio/IPC: accidental model calls cannot launch the user's browser."""
    from test_interaction_forms import rule_values
    path, _ = configuration(platform, tmp_path, service=False)
    launches = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launches.append(url) or True)
    runtime = ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)).start()
    async def initialize(session):
        await session.send_request(types.ClientRequest(types.InitializeRequest(params=types.InitializeRequestParams(
            protocolVersion='2025-11-25', capabilities=types.ClientCapabilities(
                extensions={'io.modelcontextprotocol/ui': {'mimeTypes': ['text/html;profile=mcp-app']}}),
            clientInfo=types.Implementation(name='isolated-app-host', version='1')))), types.InitializeResult)
        await session.send_notification(types.ClientNotification(types.InitializedNotification()))
    try:
        wait_for(lambda: runtime.connection.connected)
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'ioalab_sdk.mcp', '--config', str(path)],
            env={**os.environ, 'PYTHONPATH': str(Path(ioalab_sdk.__file__).resolve().parent.parent)})
        async def exercise():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                    await initialize(session)
                    tools = {t.name: t for t in (await session.list_tools()).tools}
                    assert tools['ioa_open_browser'].meta['ui']['visibility'] == ['app']
                    opened = await session.call_tool('ioa_open_form', {'kind': 'messaging.interface', 'values': rule_values()})
                    form = opened.structuredContent['data']; ref = form['reference']
                    nav = opened.meta['ioalab/navigation']
                    assert nav['reference'] == ref
                    token = nav['token']
                    assert token not in json.dumps(opened.structuredContent)
                    assert token not in ''.join(c.text for c in opened.content if hasattr(c, 'text'))
                    for extra in ({}, {'uiNavigationToken': 'fabricated'}, {'fromApp': True}):
                        denied = await session.call_tool('ioa_open_browser', {'reference': ref, **extra})
                        assert denied.isError
                    other = await session.call_tool('ioa_open_form', {'kind': 'messaging.interface'})
                    denied = await session.call_tool('ioa_open_browser', {'reference': other.structuredContent['data']['reference'], 'uiNavigationToken': token})
                    assert denied.isError
                    updated = await session.call_tool('ioa_update_form', {'reference': ref, 'revision': 0, 'values': rule_values()})
                    target = {'reference': ref, 'revision': updated.structuredContent['data']['revision']}
                    preview = await session.call_tool('ioa_preview_form', target)
                    assert not preview.isError
                    restored = await session.call_tool('ioa_get_form', {'reference': ref})
                    assert not restored.isError and restored.meta['ioalab/navigation']['token'] == token
                    assert not launches
                    # Explicit App navigation is allowed; this is not publication approval.
                    browser = await session.call_tool('ioa_open_browser', {'reference': ref, 'uiNavigationToken': token})
                    assert not browser.isError and len(launches) == 1
                    assert launches[-1].split('#')[0].endswith('/user/forms/' + ref)
                    pending = await session.call_tool('ioa_submit_form', target)
                    assert pending.structuredContent['data']['phase'] == 'waiting'
                    assert len(launches) == 1
                    denied = await session.call_tool('ioa_open_browser', {'reference': ref})
                    assert denied.isError and len(launches) == 1
                    confirmed = await session.call_tool('ioa_open_browser', {'reference': ref, 'uiNavigationToken': token})
                    assert not confirmed.isError and len(launches) == 2
                    assert launches[-1].split('#')[0] == runtime.user_ui.origin + '/user'
                    assert launches[-1].split('#')[1] not in json.dumps(confirmed.model_dump())
            # Reconnection invalidates navigation data, but not the original form.
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                    await initialize(session)
                    denied = await session.call_tool('ioa_open_browser', {'reference': ref, 'uiNavigationToken': token})
                    assert denied.isError and len(launches) == 2
                    restored = await session.call_tool('ioa_get_form', {'reference': ref})
                    fresh = restored.meta['ioalab/navigation']['token']
                    assert fresh != token
                    resumed = await session.call_tool('ioa_open_browser', {'reference': ref, 'uiNavigationToken': fresh})
                    assert not resumed.isError and len(launches) == 3
                    assert restored.structuredContent['data']['phase'] == 'waiting'
        asyncio.run(exercise())
    finally:
        runtime.close()


def test_account_setup_negotiates_inline_and_keeps_entry_out_of_model(platform, tmp_path, monkeypatch):
    import signal
    import shlex
    from ioalab_sdk.runtime.onboarding import Onboarding
    path = tmp_path / 'new-account.json'
    marker = tmp_path / 'browser-opened'
    browser = tmp_path / 'browser.py'
    browser.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("opened")\n')
    env = {**os.environ, 'PYTHONPATH': str(Path(ioalab_sdk.__file__).resolve().parent.parent),
           'BROWSER': shlex.join([sys.executable, str(browser)]) + ' %s'}
    async def exercise(apps):
        params = StdioServerParameters(command=sys.executable, args=['-m','ioalab_sdk.mcp','--config',str(path)], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                if apps:
                    await session.send_request(types.ClientRequest(types.InitializeRequest(params=types.InitializeRequestParams(
                        protocolVersion='2025-11-25', capabilities=types.ClientCapabilities(extensions={'io.modelcontextprotocol/ui': {'mimeTypes': ['text/html;profile=mcp-app']}}),
                        clientInfo=types.Implementation(name='account-test',version='1')))), types.InitializeResult)
                    await session.send_notification(types.ClientNotification(types.InitializedNotification()))
                else:
                    await session.initialize()
                tools={t.name:t for t in (await session.list_tools()).tools}
                result=await session.call_tool('ioa_setup',{'platform_url':platform.s.base_url,'role':'service'} if apps else {})
                assert not path.exists()
                assert not result.isError, result.structuredContent
                if apps:
                    assert not marker.exists()
                    assert tools['ioa_setup'].meta['ui']['resourceUri']=='ui://ioalab/account/v1.html'
                    entry=result.meta['ioalab/account']
                    assert entry['token'] not in json.dumps(result.structuredContent)
                    assert entry['token'] not in result.content[0].text
                    resource=await session.read_resource('ui://ioalab/account/v1.html')
                    assert resource.contents[0].meta['ui']['csp']['connectDomains']==[entry['origin']]
                    assert 'ioalab/account' in resource.contents[0].text
                    # A host may resolve UI resources on a separate stdio connection.
                    async with stdio_client(params) as (other_read, other_write):
                        async with ClientSession(other_read, other_write) as other:
                            await other.initialize()
                            separate = await other.read_resource('ui://ioalab/account/v1.html')
                            assert separate.contents[0].meta['ui']['csp']['connectDomains'] == [entry['origin']]
                            assert entry['token'] not in separate.contents[0].text
                    assert not marker.exists()
                    denied=await session.call_tool('ioa_setup_browser',{'uiNavigationToken':'forged'})
                    assert denied.isError and not marker.exists()
                    opened=await session.call_tool('ioa_setup_browser',{'uiNavigationToken':entry['navigationToken']})
                    assert not opened.isError
                    wait_for(marker.exists)
                    marker.unlink()
                else:
                    assert not result.meta
                    wait_for(marker.exists)
    try:
        asyncio.run(exercise(True))
        asyncio.run(exercise(False))
    finally:
        descriptor=Onboarding(path).home/'runtime.json'
        if descriptor.exists():
            os.kill(json.loads(descriptor.read_text())['pid'], signal.SIGTERM)


@pytest.mark.parametrize('kind', ['delegation.trade', 'messaging.interface'])
def test_trade_inline_confirmation_is_scoped_and_never_a_model_tool(platform, tmp_path, monkeypatch, kind):
    import httpx
    path, _ = configuration(platform, tmp_path, service=False)
    launches = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launches.append(url) or True)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)) as runtime:
        wait_for(lambda: runtime.connection.connected)
        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'ioalab_sdk.mcp', '--config', str(path)],
                env={**os.environ, 'PYTHONPATH': str(Path(ioalab_sdk.__file__).resolve().parent.parent)})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.send_request(types.ClientRequest(types.InitializeRequest(params=types.InitializeRequestParams(
                        protocolVersion='2025-11-25', capabilities=types.ClientCapabilities(extensions={'io.modelcontextprotocol/ui': {'mimeTypes': ['text/html;profile=mcp-app']}}),
                        clientInfo=types.Implementation(name='test-app', version='1')))), types.InitializeResult)
                    await session.send_notification(types.ClientNotification(types.InitializedNotification()))
                    initial_resource = await session.read_resource('ui://ioalab/forms/v1.html')
                    assert initial_resource.contents[0].meta['ui']['csp']['connectDomains'] == [runtime.user_ui.origin]
                    opened = await session.call_tool('ioa_open_form', {'kind':kind,'values':{'purpose':'制作文本','budget':5,'proposalFeeLimit':0,'query':'文本'} if kind == 'delegation.trade' else rule_values()})
                    f = opened.structuredContent['data']; target = {'reference':f['reference'],'revision':f['revision']}
                    await session.call_tool('ioa_preview_form', target)
                    waiting = await session.call_tool('ioa_submit_form', target)
                    assert waiting.structuredContent['data']['phase'] == 'waiting'
                    entry = waiting.meta['ioalab/confirmation']
                    assert entry['token'] not in json.dumps(waiting.structuredContent)
                    assert entry['token'] not in ''.join(c.text for c in waiting.content)
                    assert not launches
                    resource = await session.read_resource('ui://ioalab/forms/v1.html')
                    assert resource.contents[0].meta['ui']['csp']['connectDomains'] == [entry['origin']]
                    with httpx.Client(base_url=entry['origin'], trust_env=False) as web:
                        headers={'Origin':'null','Authorization':'Bearer '+entry['token']}
                        assert web.get('/user/confirmation-app/status').status_code == 403
                        assert web.get('/user/confirmation-app/status',headers={'Authorization':'Bearer '+runtime.ipc.token}).status_code == 403
                        assert web.post('/v1/ui/confirmation',headers=headers,json={}).status_code == 403
                        assert web.get('/user/confirmation-app/unknown',headers=headers).status_code == 403
                        item=web.get('/user/confirmation-app/status',headers=headers).json()
                        assert item['status']=='pending' and item['view']['sections']
                        assert runtime.confirmations.get(item['reference'])['status']=='pending'
                        body={'reference':item['reference'],'bindingDigest':item['bindingDigest'],'decision':'reject'}
                        assert web.post('/user/confirmation-app/decision',headers=headers,json={**body,'reference':'another'}).status_code==403
                        assert web.post('/user/decision',headers=headers,json=body).status_code==403
                        assert web.post('/user/app/setup',headers=headers,json={}).status_code==403
                        result=web.post('/user/confirmation-app/decision',headers=headers,json=body)
                        assert result.status_code==200 and result.json()['status']=='rejected'
                        assert web.post('/user/confirmation-app/decision',headers=headers,json={**body,'decision':'approve'}).status_code!=200
                        assert runtime.confirmations.get(item['reference'])['status']=='rejected'
                        terminal = await session.call_tool('ioa_get_form', {'reference': f['reference']})
                        summary = terminal.meta['ioalab/confirmation']
                        assert summary['status'] == 'rejected' and summary['reference'] == item['reference']
                        assert 'token' not in summary
                        runtime.user_ui.clock = lambda: 10**12
                        assert web.get('/user/confirmation-app/status',headers=headers).status_code == 403
        asyncio.run(exercise())


def test_confirmation_entry_expiry_is_not_business_renewal(platform, tmp_path):
    import httpx
    import time
    from ioalab_sdk.runtime import ClientOperations
    from ioalab_sdk.runtime.protocol import instant
    path, _ = configuration(platform, tmp_path, service=False)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)) as runtime:
        wait_for(lambda: runtime.connection.connected)
        now = [time.time()]
        runtime.confirmations.clock = runtime.user_ui.clock = lambda: now[0]
        api = ClientOperations(path)
        form = api.call('runtime.forms.open', {'kind': 'delegation.trade', 'values': {
            'purpose': '确认期限核对', 'budget': 5, 'proposalFeeLimit': 0, 'query': '文本'}})['data']
        target = {'reference': form['reference'], 'revision': form['revision']}
        api.call('runtime.forms.preview', target)
        form = api.call('runtime.forms.submit', target)['data']
        ref = form['result']['data']['confirmationRef']
        original = runtime.confirmations.get(ref)
        now[0] = instant(original['expiresAt']) - 5
        entry = api.confirmation_entry(form['reference'])
        with httpx.Client(base_url=entry['origin'], trust_env=False) as web:
            headers = {'Origin': 'null', 'Authorization': 'Bearer ' + entry['token']}
            now[0] = instant(original['expiresAt']) + 1
            response = web.get('/user/confirmation-app/status', headers=headers)
            assert response.status_code == 200
            assert response.json()['status'] == 'expired'
            assert web.post('/user/confirmation-app/decision', headers=headers, json={
                'reference': ref, 'bindingDigest': original['bindingDigest'], 'decision': 'approve'}).status_code != 200
            terminal = api.confirmation_entry(form['reference'])
            assert terminal['status'] == 'expired' and 'token' not in terminal
            assert terminal['reference'] == ref and terminal['expiresAt'] == original['expiresAt']
            assert runtime.confirmations.get(ref)['status'] == 'expired'
            now[0] += 900
            stale = web.get('/user/confirmation-app/status', headers=headers)
            assert stale.status_code == 403
            assert stale.json()['error']['recovery'] == 'refresh_confirmation_entry'
