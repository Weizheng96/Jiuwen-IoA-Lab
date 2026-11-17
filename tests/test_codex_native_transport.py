"""Native public transport boundary, fake wire only; no Codex process/model."""
import time
import pytest
from ioalab_codex.native_transport import NativeTransport
from ioalab_codex.transport import WireError
from ioalab_sdk.runtime import BindingAck


class PublicWire:
    def __init__(self, command, *, cwd, env):
        self.command, self.cwd, self.env = command, cwd, env
        self.calls, self.sent = [], []
        self.early = []
        self.closed = False
        self.fail_queue = False

    def send(self, value):
        self.sent.append(value)

    def request(self, method, params, deadline, handle):
        self.calls.append((method, params))
        if method == 'initialize':
            return {'codexHome': self.env['CODEX_HOME']}
        if method == 'thread/start':
            return {'thread': {'id': 'native-sid', 'ephemeral': False}, 'cwd': self.cwd,
                    'model': 'original-model', 'modelProvider': 'original-provider',
                    'approvalPolicy': 'on-request', 'sandbox': {'type': 'workspaceWrite'}}
        if method == 'turn/start':
            for packet in self.early:
                handle(packet)
            return {'turn': {'id': 'actual-turn'}}
        if method == 'thread/queue/add':
            if self.fail_queue:
                raise WireError('Connection lost after dispatch')
            return {'queuedSubmission': {'id': 'actual-qid', 'clientUserMessageId': params['clientUserMessageId']}}
        raise AssertionError(method)

    def close(self):
        self.closed = True


def opened(tmp_path, monkeypatch):
    home, work = tmp_path/'normal-home', tmp_path/'authorized-work'
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'parent-identity')
    monkeypatch.setenv('CODEX_APP_TOOLS_PIPE_PATH', 'parent-private-pipe')
    monkeypatch.setenv('CODEX_PERMISSION_PROFILE', 'original-permissions')
    host = NativeTransport(['native-codex'], home=str(home), workspace=str(work), wire_factory=PublicWire)
    host.open()
    return host


def first(host, callback=lambda sid, rid: BindingAck(sid, rid)):
    return host.start_first('original input', 'first-client', deadline=time.monotonic()+2, bind_request=callback)


def test_native_creation_preserves_defaults_and_honest_identity(tmp_path, monkeypatch):
    host = opened(tmp_path, monkeypatch)
    wire = host._wire
    assert wire.command == ['native-codex', 'app-server', '--listen', 'stdio://']
    assert 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE' not in wire.env
    assert 'CODEX_APP_TOOLS_PIPE_PATH' not in wire.env
    assert wire.env['CODEX_PERMISSION_PROFILE'] == 'original-permissions'
    assert wire.calls[0][1]['clientInfo']['name'] == 'ioalab_codex_native'
    host.create(deadline=time.monotonic()+2)
    assert wire.calls[-1] == ('thread/start', {'cwd': wire.cwd, 'ephemeral': False})
    assert host.session_id == 'native-sid'
    assert host.defaults['model'] == 'original-model'
    assert host.defaults['approvalPolicy'] == 'on-request'
    with pytest.raises(WireError, match='cannot be retried'):
        host.create(deadline=time.monotonic()+2)
    host.close()
    assert wire.closed


def test_first_requires_exact_sid_request_ack_before_returning_early_packets(tmp_path, monkeypatch):
    host = opened(tmp_path, monkeypatch)
    host.create(deadline=time.monotonic()+2)
    host._wire.early = [{'method': 'turn/started', 'params': {'threadId': 'native-sid', 'turn': {'id': 'actual-turn'}}}]
    seen = []
    result, early = first(host, lambda sid, rid: seen.append((sid, rid)) or BindingAck(sid, rid))
    assert seen == [('native-sid', 'actual-turn')]
    assert result['turn']['id'] == 'actual-turn' and len(early) == 1
    assert host._wire.calls[-1][1] == {'threadId': 'native-sid', 'clientUserMessageId': 'first-client',
                                     'input': [{'type': 'text', 'text': 'original input'}]}
    with pytest.raises(WireError, match='single-dispatch'):
        first(host)


@pytest.mark.parametrize('ack', [None, BindingAck('wrong-sid', 'actual-turn'), BindingAck('native-sid', None)])
def test_wrong_p28_ack_never_releases_native_packets(tmp_path, monkeypatch, ack):
    host = opened(tmp_path, monkeypatch)
    host.create(deadline=time.monotonic()+2)
    with pytest.raises(WireError, match='exact native SID/request'):
        first(host, lambda sid, rid: ack)


def test_early_other_turn_rejected_before_binding_callback(tmp_path, monkeypatch):
    host = opened(tmp_path, monkeypatch)
    host.create(deadline=time.monotonic()+2)
    host._wire.early = [{'method': 'item/started', 'params': {'threadId': 'native-sid', 'turnId': 'foreign'}}]
    calls = []
    with pytest.raises(WireError, match='Early native item turn conflict'):
        first(host, lambda *args: calls.append(args))
    assert calls == []


def test_queue_unknown_is_not_replayed_or_resumed(tmp_path, monkeypatch):
    host = opened(tmp_path, monkeypatch)
    host.create(deadline=time.monotonic()+2)
    first(host)
    host._wire.fail_queue = True
    with pytest.raises(WireError, match='lost after dispatch'):
        host.queue_owned('message', 'queue-client', deadline=time.monotonic()+2, handle=lambda packet: None)
    with pytest.raises(WireError, match='cannot be replayed'):
        host.queue_owned('message', 'queue-client', deadline=time.monotonic()+2, handle=lambda packet: None)
    assert [m for m, _ in host._wire.calls].count('thread/queue/add') == 1
    assert not any(m in ('thread/resume', 'thread/queue/start') for m, _ in host._wire.calls)


def test_native_request_does_not_auto_approve_or_deny(tmp_path, monkeypatch):
    host = opened(tmp_path, monkeypatch)
    host._wire.early = []
    # Exercise the real component's request forwarding policy through wire boundary.
    def requested(method, params, deadline, handle):
        handle({'id': 'native-request', 'method': 'item/commandExecution/requestApproval', 'params': {}})
    host._wire.request = requested
    with pytest.raises(WireError, match='original user interaction'):
        host.create(deadline=time.monotonic()+2)
    assert host._wire.sent == [{'method': 'initialized'}]
