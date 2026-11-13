"""The public stdio MCP entry uses the existing local runtime, never a second writer."""
import asyncio
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
