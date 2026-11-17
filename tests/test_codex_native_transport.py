"""Route A owned app-server boundary, fake wire only; no Codex process/model."""
import time
import pytest
from ioalab_codex.native_transport import NativeAppServer
from ioalab_codex.transport import WireError, WireRejected


class PublicWire:
    def __init__(self, command, *, cwd, env):
        self.command, self.cwd, self.env = command, cwd, env
        self.calls, self.sent = [], []
        self.closed = False
        self.home = None
        self.queue_failure = None
        self.pages = None

    def send(self, value):
        self.sent.append(value)

    def request(self, method, params, deadline, handle):
        self.calls.append((method, params))
        if method == 'initialize':
            return {'codexHome': self.home}
        if method == 'thread/start':
            return {'thread': {'id': 'native-sid', 'ephemeral': False}, 'cwd': self.cwd, 'model': 'original-model'}
        if method == 'thread/name/set':
            return {}
        if method == 'thread/queue/add':
            if self.queue_failure:
                raise self.queue_failure
            return {'queuedSubmission': {'id': 'actual-qid', 'clientUserMessageId': params['clientUserMessageId'], 'input': []}}
        if method == 'thread/turns/list':
            return self.pages.pop(0)
        if method == 'thread/queue/list':
            return {'data': [{'id': 'q', 'clientUserMessageId': 'waiting', 'input': []}], 'nextCursor': None}
        raise AssertionError(method)

    def close(self):
        self.closed = True


def opened(tmp_path, monkeypatch, *, state='set'):
    home, work = tmp_path/'normal-home', tmp_path/'project'
    work.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'resident-home'))
    monkeypatch.setenv('CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'parent-identity')
    monkeypatch.setenv('CODEX_APP_TOOLS_PIPE_PATH', 'parent-private-pipe')
    monkeypatch.setenv('CODEX_PERMISSION_PROFILE', 'original-permissions')
    wires = []
    def factory(*args, **kwargs):
        wire = PublicWire(*args, **kwargs)
        wire.home = str(home) if state == 'set' else str(__import__('pathlib').Path.home()/'.codex')
        wires.append(wire)
        return wire
    server = NativeAppServer(['desktop-codex'], home_state=state, home_directory=str(home) if state == 'set' else None,
                             workspace=str(work), wire_factory=factory)
    server.open()
    return server, wires[0]


def test_owned_server_uses_captured_home_and_honest_identity(tmp_path, monkeypatch):
    server, wire = opened(tmp_path, monkeypatch)
    assert wire.command == ['desktop-codex', 'app-server', '--listen', 'stdio://']
    assert wire.env['CODEX_HOME'] == str((tmp_path/'normal-home').resolve())
    assert 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE' not in wire.env and 'CODEX_APP_TOOLS_PIPE_PATH' not in wire.env
    assert wire.env['CODEX_PERMISSION_PROFILE'] == 'original-permissions'
    assert wire.calls[0][1]['clientInfo']['name'] == 'ioalab_codex_native'
    server.close()
    assert wire.closed


def test_unset_home_removes_inherited_override(tmp_path, monkeypatch):
    _, wire = opened(tmp_path, monkeypatch, state='unset')
    assert 'CODEX_HOME' not in wire.env


def test_unconfirmed_home_closes_owned_child(tmp_path, monkeypatch):
    def factory(*args, **kwargs):
        wire = PublicWire(*args, **kwargs); wire.home = str(tmp_path/'shadow'); seen.append(wire); return wire
    seen = []
    (tmp_path/'p').mkdir()
    server = NativeAppServer(['c'], home_state='set', home_directory=str(tmp_path/'home'), workspace=str(tmp_path/'p'),
                             wire_factory=factory)
    with pytest.raises(WireError, match='HOME'):
        server.open()
    assert seen[0].closed


def test_zero_model_create_names_persistent_chat_and_never_starts_turn(tmp_path, monkeypatch):
    server, wire = opened(tmp_path, monkeypatch)
    assert server.create('IoA Lab · business', deadline=time.monotonic()+2) == ('native-sid', True)
    assert [m for m, _ in wire.calls] == ['initialize', 'thread/start', 'thread/name/set']
    assert wire.calls[1][1] == {'cwd': wire.cwd, 'ephemeral': False}
    assert not hasattr(server, 'start_first') and not hasattr(server, 'resume')


def test_queue_carries_original_client_and_rejection_is_distinct_from_loss(tmp_path, monkeypatch):
    server, wire = opened(tmp_path, monkeypatch)
    assert server.queue('user-sid', 'text', 'client-1', deadline=time.monotonic()+2) == 'actual-qid'
    assert wire.calls[-1] == ('thread/queue/add', {'threadId': 'user-sid', 'clientUserMessageId': 'client-1',
                                                   'input': [{'type': 'text', 'text': 'text'}]})
    wire.queue_failure = WireRejected('refused')
    with pytest.raises(WireRejected):
        server.queue('user-sid', 'text', 'client-2', deadline=time.monotonic()+2)
    wire.queue_failure = WireError('lost')
    with pytest.raises(WireError) as lost:
        server.queue('user-sid', 'text', 'client-3', deadline=time.monotonic()+2)
    assert not isinstance(lost.value, WireRejected)


def test_reader_pages_persisted_turns_and_queue(tmp_path, monkeypatch):
    server, wire = opened(tmp_path, monkeypatch)
    wire.pages = [{'data': [{'id': 't2'}], 'nextCursor': 'c1'}, {'data': [{'id': 't1'}], 'nextCursor': None}]
    turns, complete = server.turns('sid', deadline=time.monotonic()+2)
    assert [t['id'] for t in turns] == ['t2', 't1'] and complete
    assert wire.calls[-1][1] == {'threadId': 'sid', 'itemsView': 'full', 'limit': 20, 'sortDirection': 'desc', 'cursor': 'c1'}
    assert server.queued('sid', deadline=time.monotonic()+2) == (['waiting'], True)


def test_native_request_is_never_answered(tmp_path, monkeypatch):
    server, wire = opened(tmp_path, monkeypatch)
    def requested(method, params, deadline, handle):
        handle({'id': 'native-request', 'method': 'item/commandExecution/requestApproval', 'params': {}})
    wire.request = requested
    with pytest.raises(WireError, match='original desktop interaction'):
        server.create('name', deadline=time.monotonic()+2)
    assert wire.sent == [{'method': 'initialized'}]
