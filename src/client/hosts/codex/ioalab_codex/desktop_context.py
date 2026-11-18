"""Private desktop MCP launch context, captured only by the native entry.

The pipe is supplied by Codex to its child, never discovered from another
process. No model credentials, historical turns or tool-call identity is saved.
"""
import json
import os
from pathlib import Path
import threading

from ioalab_sdk.runtime import RuntimeFault, private_json, restrict

FORMAT = 'ioalab.codex-desktop-context/1'
FIELDS = {'format', 'pipePath', 'nodePath', 'pluginDirectory', 'threadId', 'hostId'}


def _fault():
    return RuntimeFault('not_ready', 'Run the fixed IoA setup entry in the open Codex desktop to refresh its tool connection',
                        'run_native_setup')


def validate(value):
    if not isinstance(value, dict) or set(value) != FIELDS or value.get('format') != FORMAT:
        raise _fault()
    if any(not isinstance(value[k], str) or not 1 <= len(value[k]) <= 8192 or '\x00' in value[k]
           for k in ('pipePath', 'nodePath', 'pluginDirectory', 'threadId')):
        raise _fault()
    if value['hostId'] is not None and (not isinstance(value['hostId'], str) or not value['hostId']):
        raise _fault()
    if not Path(value['nodePath']).is_absolute() or not Path(value['pluginDirectory']).is_absolute():
        raise _fault()
    return value


def capture_desktop(session_id, *, environ=None):
    """Require the real one-time hook session to match this child's executor."""
    env = os.environ if environ is None else environ
    if not isinstance(session_id, str) or not session_id or env.get('CODEX_THREAD_ID') != session_id:
        raise RuntimeFault('identity_conflict', 'The native hook and desktop executor identify different chats')
    node = env.get('CODEX_MCP_NODE_PATH')
    pipe = env.get('CODEX_APP_TOOLS_PIPE_PATH')
    if not node or not pipe or not Path(node).is_absolute() or not Path(node).is_file():
        raise _fault()
    home = Path(env.get('CODEX_HOME') or Path.home()/'.codex')
    cache = home/'plugins'/'cache'/'openai-bundled'/'codex-app-tools'
    candidates = []
    if cache.is_dir():
        for directory in cache.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                manifest = json.loads((directory/'.codex-plugin'/'plugin.json').read_text(encoding='utf-8'))
                version = tuple(int(part) for part in directory.name.split('.'))
                if manifest.get('name') == 'codex-app-tools' and (directory/'.mcp.json').is_file() and (directory/'server.mjs').is_file():
                    candidates.append((version, directory))
            except (OSError, ValueError, TypeError):
                continue
    if not candidates:
        raise _fault()
    return validate({'format': FORMAT, 'pipePath': pipe, 'nodePath': node,
                     'pluginDirectory': str(max(candidates, key=lambda item: item[0])[1].resolve()),
                     'threadId': session_id, 'hostId': env.get('CODEX_APP_TOOLS_CALLER_HOST_ID') or None})


class DesktopContextStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root/'desktop-context.json'
        self.lock = threading.RLock()

    def _paths(self):
        if any(path.is_symlink() for path in (self.path, self.root, *self.root.parents)):
            raise RuntimeFault('forbidden', 'Restore the original private desktop connection directory')

    def save(self, value):
        validate(value)
        with self.lock:
            self._paths()
            self.root.mkdir(parents=True, exist_ok=True)
            restrict(self.root, directory=True)
            private_json(self.path, value)
        return {'captured': True}

    def read(self):
        with self.lock:
            self._paths()
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise _fault() from None
            return dict(validate(value))
