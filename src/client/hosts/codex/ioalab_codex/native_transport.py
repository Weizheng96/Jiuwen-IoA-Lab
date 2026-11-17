"""Route A owned short-lived App Server: zero-model chat, public queue, read-only reader.

Each instance is one adapter-owned stdio child for one step. It never resumes a
user chat, never calls turn/start or queue/start and never answers a native
approval: route A turns run only in the user's own desktop server. Native
policy, model and capabilities are loaded by Codex from the original HOME/config.
"""
import os
from pathlib import Path
import time

from .transport import Stdio, WireError, WireRejected

INTERNAL_IDENTITY_ENV = ('CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'CODEX_APP_TOOLS_PIPE_PATH',
                         'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_TOOL_CALL_ID')
HANDSHAKE_SECONDS = 15
TURN_PAGE = 20


def original_home(state, directory):
    """The CODEX_HOME captured by setup: unset means the default user directory."""
    if state == 'unset':
        return (Path.home()/'.codex').resolve()
    if state == 'set' and isinstance(directory, str) and Path(directory).is_absolute():
        return Path(directory).resolve()
    raise ValueError('Original native configuration directory is not captured')


class NativeAppServer:
    """One owned app-server child; closing it reaps only this PID."""

    def __init__(self, command, *, home_state, home_directory, workspace, wire_factory=Stdio):
        if not isinstance(command, (tuple, list)) or not command or any(not isinstance(x, str) or not x for x in command):
            raise ValueError('Explicit native argv is required')
        if not Path(workspace).is_absolute():
            raise ValueError('The original native project directory must be absolute')
        self.command = tuple(command)
        self.home_state, self.home_directory = home_state, home_directory
        self.home = original_home(home_state, home_directory)
        self.workspace = Path(workspace).resolve()
        self._factory = wire_factory
        self._wire = None
        self._opened = False

    def _environment(self):
        env = dict(os.environ)
        for name in INTERNAL_IDENTITY_ENV:
            env.pop(name, None)  # Never impersonate the parent native session or its tool pipe.
        if self.home_state == 'unset':
            env.pop('CODEX_HOME', None)
        else:
            env['CODEX_HOME'] = str(self.home)
        return env

    def _call(self, method, params, deadline):
        if self._wire is None:
            raise WireError('Native app server is not open')
        def forward(packet):
            if 'method' in packet and 'id' in packet:
                # Route A owns no turn here; a native request belongs to the desktop UI.
                raise WireError('Native request requires the original desktop interaction')
        return self._wire.request(method, params, deadline, forward)

    def open(self):
        if self._opened:
            raise WireError('Open each owned native app server once')
        self._opened = True
        self._wire = self._factory([*self.command, 'app-server', '--listen', 'stdio://'],
                                   cwd=str(self.workspace), env=self._environment())
        try:
            result = self._call('initialize', {'clientInfo': {'name': 'ioalab_codex_native', 'version': '0.0.0'},
                                              'capabilities': {'experimentalApi': True}}, time.monotonic()+HANDSHAKE_SECONDS)
            if not result.get('codexHome') or Path(result['codexHome']).resolve() != self.home:
                raise WireError('Original native HOME was not confirmed')
            self._wire.send({'method': 'initialized'})
            return result
        except Exception:
            self.close()
            raise

    def create(self, name, *, deadline):
        """Zero-model persistent chat: thread/start(ephemeral=false) and name/set, never a turn."""
        result = self._call('thread/start', {'cwd': str(self.workspace), 'ephemeral': False}, deadline)
        actual = result.get('thread', {})
        sid = actual.get('id')
        if not isinstance(sid, str) or not 1 <= len(sid) <= 8192:
            raise WireError('Missing actual native SID')
        cwd = result.get('cwd')
        if actual.get('ephemeral') is not False or not isinstance(cwd, str) or not Path(cwd).is_absolute() or Path(cwd).resolve() != self.workspace:
            raise WireError('Native workspace or persistent-session identity conflict')
        named = True
        try:
            self._call('thread/name/set', {'threadId': sid, 'name': name}, deadline)
        except WireError:
            named = False  # The real SID exists; a missing title never hides or replaces it.
        return sid, named

    def queue(self, sid, text, client_id, *, deadline):
        """Public persistent queue only; acceptance is not delivery or execution."""
        if not isinstance(text, str) or not isinstance(client_id, str) or not client_id:
            raise ValueError('Preserve the original queue text and client identity')
        result = self._call('thread/queue/add', {'threadId': sid, 'clientUserMessageId': client_id,
                            'input': [{'type': 'text', 'text': text}]}, deadline)
        queued = result.get('queuedSubmission', {})
        if not isinstance(queued.get('id'), str) or not queued['id'] or queued.get('clientUserMessageId') != client_id:
            raise WireError('Native queue response identity conflict')
        return queued['id']

    def turns(self, sid, *, deadline, pages=5):
        """Read-only persisted turns (newest first); no resume or subscription."""
        found, cursor = [], None
        for _ in range(pages):
            params = {'threadId': sid, 'itemsView': 'full', 'limit': TURN_PAGE, 'sortDirection': 'desc'}
            if cursor:
                params['cursor'] = cursor
            page = self._call('thread/turns/list', params, deadline)
            data = page.get('data')
            if not isinstance(data, list):
                raise WireError('Invalid persisted turn page')
            found.extend(data)
            cursor = page.get('nextCursor')
            if not cursor:
                return found, True
        return found, False

    def queued(self, sid, *, deadline, pages=5):
        """Client IDs still waiting in the public queue; (ids, complete)."""
        found, cursor = [], None
        for _ in range(pages):
            params = {'threadId': sid, **({'cursor': cursor} if cursor else {})}
            page = self._call('thread/queue/list', params, deadline)
            data = page.get('data')
            if not isinstance(data, list):
                raise WireError('Invalid native queue page')
            found.extend(item.get('clientUserMessageId') for item in data if isinstance(item, dict))
            cursor = page.get('nextCursor')
            if not cursor:
                return found, True
        return found, False

    def close(self):
        if self._wire is not None:
            self._wire.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


__all__ = ['NativeAppServer', 'WireError', 'WireRejected', 'original_home', 'INTERNAL_IDENTITY_ENV']
