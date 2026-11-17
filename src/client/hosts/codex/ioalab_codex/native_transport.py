"""Private native transport component candidate; not a registered S01 host.

Separate from restricted SDK driver. Only creates its own new writer; no resume.
Native policy and capabilities are loaded by Codex from the original HOME/config.
"""
import os
from pathlib import Path
import time
from ioalab_sdk.runtime import BindingAck

from .transport import Stdio, WireError


INTERNAL_IDENTITY_ENV = ('CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'CODEX_APP_TOOLS_PIPE_PATH',
                         'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'CODEX_TOOL_CALL_ID')


class NativeTransport:
    def __init__(self, command, *, home, workspace, wire_factory=Stdio):
        if not isinstance(command, (tuple, list)) or not command or any(not isinstance(x, str) or not x for x in command):
            raise ValueError('Explicit native argv is required')
        if not Path(home).is_absolute() or not Path(workspace).is_absolute():
            raise ValueError('Original native HOME and SDK-authorized workspace must be absolute')
        self.command = tuple(command)
        self.home, self.workspace = Path(home).resolve(), Path(workspace).resolve()
        self._factory = wire_factory
        self._wire = None
        self._sid = None
        self._created = False
        self._first_dispatched = False
        self._closed = False
        self._queue_clients = set()
        self.defaults = None

    @property
    def session_id(self):
        return self._sid

    def _call(self, method, params, deadline, handle=None):
        if self._wire is None or self._closed:
            raise WireError('Native transport is not open')
        def forward(packet):
            if handle is None:
                if 'method' in packet and 'id' in packet:
                    raise WireError('Native request requires original user interaction transport')
            else:
                handle(packet)
        return self._wire.request(method, params, deadline, forward)

    def open(self):
        if self._wire is not None or self._closed:
            raise WireError('Open each owned native writer once')
        env = dict(os.environ)
        inherited = Path(env.get('CODEX_HOME') or Path.home()/'.codex').resolve()
        if inherited != self.home:
            raise WireError('Native HOME must match the original environment; no shadow HOME')
        for name in INTERNAL_IDENTITY_ENV:
            env.pop(name, None)
        self._wire = self._factory([*self.command, 'app-server', '--listen', 'stdio://'],
                                   cwd=str(self.workspace), env=env)
        try:
            result = self._call('initialize', {'clientInfo': {'name': 'ioalab_codex_native', 'version': '0.0.0'},
                                              'capabilities': {'experimentalApi': True}}, time.monotonic()+15)
            if not result.get('codexHome') or Path(result['codexHome']).resolve() != self.home:
                raise WireError('Original native HOME was not confirmed')
            self._wire.send({'method': 'initialized'})
            return result
        except Exception:
            self.close()
            raise

    def create(self, *, deadline):
        if self._created:
            raise WireError('Unknown or completed creation cannot be retried')
        self._created = True  # Caller must persist its SDK attempt before calling.
        result = self._call('thread/start', {'cwd': str(self.workspace), 'ephemeral': False}, deadline)
        actual = result.get('thread', {})
        sid = actual.get('id')
        if not isinstance(sid, str) or not sid:
            raise WireError('Missing actual native SID')
        self._sid = sid  # Preserve actual identity even if a later binding check fails.
        cwd = result.get('cwd')
        if actual.get('ephemeral') is not False or not isinstance(cwd, str) or not Path(cwd).is_absolute() or Path(cwd).resolve() != self.workspace:
            raise WireError('Native workspace or persistent-session identity conflict')
        self.defaults = {key: result.get(key) for key in ('model', 'modelProvider', 'approvalPolicy',
                         'approvalsReviewer', 'sandbox', 'activePermissionProfile', 'cwd')}
        return result

    def start_first(self, text, client_id, *, deadline, bind_request):
        if not self._sid or self._first_dispatched:
            raise WireError('First native request is single-dispatch for the owned new SID')
        if not isinstance(text, str) or not isinstance(client_id, str) or not client_id:
            raise ValueError('Preserve text and original correlation ID')
        self._first_dispatched = True
        early = []
        size = 0
        def buffer(packet):
            import json
            nonlocal size
            size += len(json.dumps(packet).encode('utf-8'))
            if len(early) >= 128 or size > 1024*1024:
                raise WireError('Native early packet bound exceeded')
            early.append(packet)
        result = self._call('turn/start', {'threadId': self._sid, 'clientUserMessageId': client_id,
                            'input': [{'type': 'text', 'text': text}]}, deadline, buffer)
        request = result.get('turn', {}).get('id')
        if not isinstance(request, str) or not request:
            raise WireError('Missing actual native turn ID')
        for packet in early:
            params = packet.get('params', {})
            if params.get('threadId') is not None and params['threadId'] != self._sid:
                raise WireError('Early native SID conflict')
            if packet.get('method') in ('turn/started', 'turn/completed') and params.get('turn', {}).get('id') != request:
                raise WireError('Early native turn conflict')
            if params.get('turnId') is not None and params['turnId'] != request:
                raise WireError('Early native item turn conflict')
        # S01 owner must validate exact SDK BindingAck; exceptions preserve unknown.
        ack = bind_request(self._sid, request)
        if not isinstance(ack, BindingAck) or (ack.session_id, ack.request_id) != (self._sid, request):
            raise WireError('SDK did not acknowledge the exact native SID/request')
        return result, tuple(early)

    def queue_owned(self, text, client_id, *, deadline, handle):
        if not self._sid or not self._first_dispatched:
            raise WireError('Queue only the original owned native SID')
        if not isinstance(text, str) or not isinstance(client_id, str) or not client_id:
            raise ValueError('Preserve original queue text/client identity')
        if client_id in self._queue_clients:
            raise WireError('Unknown or accepted queue attempt cannot be replayed')
        self._queue_clients.add(client_id)
        # Persist each logical attempt at SDK boundary. No in-module retries.
        result = self._call('thread/queue/add', {'threadId': self._sid, 'clientUserMessageId': client_id,
                            'input': [{'type': 'text', 'text': text}]}, deadline, handle)
        queued = result.get('queuedSubmission', {})
        if not isinstance(queued.get('id'), str) or not queued['id'] or queued.get('clientUserMessageId') != client_id:
            raise WireError('Native queue response identity conflict')
        return result

    def receive(self, deadline):
        if self._wire is None or self._closed:
            raise WireError('Native writer is closed')
        return self._wire.receive(deadline)

    def close(self):
        self._closed = True
        if self._wire is not None:
            self._wire.close()

    # No existing-SID resume, interrupt, policy mutation or business fallback.
