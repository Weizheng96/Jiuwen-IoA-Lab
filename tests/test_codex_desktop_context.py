"""Private desktop connection capture and authenticated setup boundary."""
import json
from types import SimpleNamespace

import pytest

from ioalab_codex.desktop_context import DesktopContextStore, capture_desktop
from ioalab_codex.native_setup import NativeSetup
from ioalab_sdk.runtime import RuntimeFault


@pytest.fixture
def desktop_environment(tmp_path):
    home = tmp_path/'codex'
    plugin = home/'plugins/cache/openai-bundled/codex-app-tools/26.11.1'
    (plugin/'.codex-plugin').mkdir(parents=True)
    (plugin/'.codex-plugin/plugin.json').write_text(json.dumps({'name': 'codex-app-tools'}))
    (plugin/'.mcp.json').write_text('{}')
    (plugin/'server.mjs').write_text('')
    node = tmp_path/'node.exe'; node.write_bytes(b'')
    return {'CODEX_HOME': str(home), 'CODEX_THREAD_ID': 'actual-hook-session',
            'CODEX_APP_TOOLS_PIPE_PATH': 'private-pipe', 'CODEX_MCP_NODE_PATH': str(node)}


def test_capture_uses_inherited_executor_and_official_bundle_without_call_identity(desktop_environment):
    captured = capture_desktop('actual-hook-session', environ=desktop_environment)
    assert captured['threadId'] == 'actual-hook-session'
    assert captured['pipePath'] == 'private-pipe'
    assert captured['pluginDirectory'].endswith('26.11.1')
    assert captured['hostId'] is None
    assert set(captured) == {'format', 'threadId', 'pipePath', 'nodePath', 'pluginDirectory', 'hostId'}


def test_capture_refuses_other_hook_identity_and_missing_context(desktop_environment):
    with pytest.raises(RuntimeFault) as mismatch:
        capture_desktop('model-selected-chat', environ=desktop_environment)
    assert mismatch.value.code == 'identity_conflict'
    desktop_environment.pop('CODEX_APP_TOOLS_PIPE_PATH')
    with pytest.raises(RuntimeFault) as missing:
        capture_desktop('actual-hook-session', environ=desktop_environment)
    assert missing.value.code == 'not_ready'
    assert missing.value.recovery == 'run_native_setup'
    assert 'private-pipe' not in str(missing.value)


def test_private_refresh_requires_original_launch_authority_and_preserves_it(tmp_path, desktop_environment):
    authority = {'hostRef': 'codex', 'adapterId': 'codex', 'accountBindingRef': 'original-account'}
    original = {'authority': authority, 'claim': 'original-claim'}
    launch = SimpleNamespace(home=tmp_path/'adapter/launch', read=lambda: original)
    setup = NativeSetup(host_ref='codex', home=launch.home, store=launch)
    captured = capture_desktop('actual-hook-session', environ=desktop_environment)
    body = {'kind': 'entry_desktop', 'capture': captured}
    assert setup.exchange(authority, body) == {'captured': True}
    assert DesktopContextStore(tmp_path/'adapter').read() == captured
    assert original == {'authority': authority, 'claim': 'original-claim'}
    with pytest.raises(RuntimeFault) as wrong:
        setup.exchange({**authority, 'accountBindingRef': 'other-account'}, body)
    assert wrong.value.code == 'identity_conflict'
    assert DesktopContextStore(tmp_path/'adapter').read() == captured
