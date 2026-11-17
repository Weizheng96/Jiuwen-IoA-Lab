"""Route A product adapter at its seams: owned app-server boundary, process enumeration
boundary, SDK P28/P40 callbacks and the real HostRegistry. No Codex process or model."""
from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace

import pytest

from ioalab_codex import native_adapter
from ioalab_codex.native_desktop import DesktopHealth, ABSENT, PRESENT
from ioalab_codex.native_host import NativeCodexHost
from ioalab_codex.native_process import NativeLaunchStore
from ioalab_codex.transport import WireError, WireRejected
from ioalab_sdk.runtime import (BindingAck, HostDefinition, HostExecution, HostInput, HostRegistry,
                               HostToolDeclaration, HostToolResult, RuntimeFault, RuntimeStore,
                               SubmissionAttempt, SubmissionEvent)

SCHEMA = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text'], 'additionalProperties': False}


class Desktop:
    """Persisted chat state as seen by the public app-server protocol."""

    def __init__(self):
        self.calls, self.servers = [], []
        self.chats = {}
        self.script = []           # callables run on each turns/list read
        self.fail = {}
        self.lock = threading.Lock()
        self.queue_read = threading.Event()

    def consume(self, sid, client, turn='turn-1', status='inProgress', completed=None, text=None):
        items = [{'type': 'userMessage', 'id': 'u-'+turn, 'clientId': client, 'content': []}]
        if text is not None:
            items.append({'type': 'agentMessage', 'id': 'a-'+turn, 'text': text})
        with self.lock:
            chat = self.chats[sid]
            chat['queue'] = [c for c in chat['queue'] if c != client]
            chat['turns'] = [t for t in chat['turns'] if t['id'] != turn]
            chat['turns'].insert(0, {'id': turn, 'status': status, 'completedAt': completed, 'items': items})


class Server:
    def __init__(self, desktop, command, *, home_state, home_directory, workspace):
        self.desktop, self.command = desktop, command
        self.home_state, self.workspace = home_state, workspace
        self.open_ = False
        desktop.servers.append(self)

    def _log(self, method, *args):
        self.desktop.calls.append((method, *args))
        failure = self.desktop.fail.pop(method, None)
        if failure:
            raise failure

    def open(self):
        self._log('open')
        self.open_ = True

    def create(self, name, *, deadline):
        self._log('thread/start', name)
        sid = 'new-sid-%d' % len(self.desktop.chats)
        self.desktop.chats[sid] = {'name': name, 'queue': [], 'turns': []}
        return sid, True

    def queue(self, sid, text, client, *, deadline):
        self._log('thread/queue/add', sid, client, text)
        self.desktop.chats.setdefault(sid, {'queue': [], 'turns': []})['queue'].append(client)
        return 'qid-'+client[:6]

    def turns(self, sid, *, deadline):
        self._log('thread/turns/list', sid)
        if self.desktop.script:
            self.desktop.script.pop(0)()
        return list(self.desktop.chats[sid]['turns']), True

    def queued(self, sid, *, deadline):
        self._log('thread/queue/list', sid)
        result = list(self.desktop.chats[sid]['queue'])
        self.desktop.queue_read.set()
        return result, True

    def close(self):
        self.desktop.calls.append(('close',))
        self.open_ = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


@pytest.fixture
def adapter(tmp_path):
    desktop = Desktop()
    root = tmp_path/'adapter'
    cli = tmp_path/'OpenAI'/'Codex'/'bin'/'abc'/'codex.exe'
    cli.parent.mkdir(parents=True); cli.write_bytes(b'')
    project = tmp_path/'project'; project.mkdir()
    store = NativeLaunchStore(host_ref='codex', home=root/'launch')
    capture = {'format': 'ioalab.codex-launch/1', 'projectDirectory': str(project), 'cli': str(cli),
               'extensionDirectory': str(tmp_path/'entry'), 'configDirectoryState': 'unset', 'configDirectory': None}
    store.read = lambda: {'capture': capture}
    host = NativeCodexHost(host_ref='codex', root=root, launch_store=store, poll=(.01, .02),
                           server_factory=lambda *a, **k: Server(desktop, *a, **k),
                           enumerate_processes=lambda: [], health_samples=1)
    host._tool_entry = lambda profile: ('C:\\py\\python.exe', 'C:\\skills\\native\\entry-assistant.py', 'assistant')
    yield SimpleNamespace(host=host, desktop=desktop, store=store, capture=capture, cli=cli)
    host.close()


def soon(seconds):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()


def attempt(nonce='attempt-1'):
    return SubmissionAttempt(nonce, 'scope', 'actor-a', 'profile-a')


def execution(**changes):
    value = HostExecution('actor-a', 'profile-a', 'business-1', 'interaction-1', 'job-1',
                          HostDefinition('definition', '1', 'digest-1', 'frozen instructions'),
                          tools=(HostToolDeclaration('ioa_reply', 'reply', SCHEMA),),
                          authorization={'nativeTools': {}, 'nativeToolsEnabled': True})
    return replace(value, **changes)


class Events:
    def __init__(self):
        self.seen = []

    def __call__(self, event):
        assert isinstance(event, SubmissionEvent)
        self.seen.append(event)
        if event.kind == 'session':
            return BindingAck(event.result.session_id, event.result.request_id)

    def kinds(self):
        return [(e.kind, e.result.state, e.result.request_id, e.result.delivery) for e in self.seen]


def methods(desktop):
    return [call[0] for call in desktop.calls]


# --- S02 ---------------------------------------------------------------------------------

def test_s02_present_desktop_server_is_available_regardless_of_chat(adapter):
    root = adapter.cli.parent.parent
    rows = [{'pid': 10, 'parent': 9, 'image': str(root/'other-version'/'codex.exe')}]
    health = DesktopHealth(lambda: str(adapter.cli), enumerate_processes=lambda: rows, samples=1, owner=1)
    assert health.check() == {'state': 'available', 'evidence': PRESENT}


def test_packaged_desktop_location_survives_version_updates_but_not_other_packages():
    from ioalab_codex.native_desktop import install_location
    tail = '/app/resources/codex.exe'.replace('/', chr(92))
    base = 'C:/Program Files/WindowsApps/'.replace('/', chr(92))
    old = base+'OpenAI.Codex_26.1103.7773.0_x64__2p2nqsd0c76g0'+tail
    new = base+'OpenAI.Codex_26.1111.2228.0_x64__2p2nqsd0c76g0'+tail
    other = base+'Other.Codex_26.1111.2228.0_x64__2p2nqsd0c76g0'+tail
    if __import__('os').name != 'nt':
        pytest.skip('Windows path semantics')
    assert install_location(old) == install_location(new) != install_location(other)
    rows = [{'pid': 7, 'parent': 3, 'image': new}]
    health = DesktopHealth(lambda: old, enumerate_processes=lambda: rows, samples=1, owner=1)
    assert health.check()['state'] == 'available'


def test_submit_uses_running_server_of_same_installation_after_update(adapter, tmp_path):
    updated = adapter.cli.parent.parent/'def'/'codex.exe'
    updated.parent.mkdir(); updated.write_bytes(b'')
    adapter.cli.unlink()
    adapter.host.enumerate = lambda: [{'pid': 5, 'parent': 2, 'image': str(updated)}]
    adapter.desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    result = adapter.host.submit('notice-u', HostInput('n', attempt=attempt()), 'user-sid', on_event=Events())
    assert result.state == 'accepted'
    assert adapter.desktop.servers[0].command == [str(updated)]
    adapter.host.enumerate = lambda: []
    missing = adapter.host.submit('notice-v', HostInput('n', attempt=attempt('a9')), 'user-sid', on_event=Events())
    assert missing.error.phase == 'desktop_location' and missing.error.dispatch == 'not_submitted'


def test_s02_owned_children_and_other_installs_do_not_count_and_absence_is_unavailable(adapter, tmp_path):
    rows = [{'pid': 10, 'parent': 1, 'image': str(adapter.cli)},
            {'pid': 11, 'parent': 5, 'image': str(tmp_path/'elsewhere'/'bin'/'x'/'codex.exe')}]
    sleeps = []
    health = DesktopHealth(lambda: str(adapter.cli), enumerate_processes=lambda: rows, samples=3, owner=1,
                           sleep=sleeps.append)
    assert health.check() == {'state': 'unavailable', 'evidence': ABSENT}
    assert len(sleeps) == 2  # every sample in the interval confirmed absence


def test_s02_enumeration_failure_or_missing_location_is_unknown(adapter):
    def broken():
        raise PermissionError('denied')
    assert DesktopHealth(lambda: str(adapter.cli), enumerate_processes=broken, samples=1).check()['state'] == 'unknown'
    assert DesktopHealth(lambda: None, enumerate_processes=lambda: [], samples=1).check()['state'] == 'unknown'
    # Product host reads the saved setup capture; nothing running is unavailable, not busy.
    assert adapter.host.check() == {'state': 'unavailable', 'evidence': ABSENT}


def test_registration_declares_business_session_and_no_optional_or_restriction():
    registration = NativeCodexHost.registration
    assert registration.readonly_context == 'business_session'
    assert registration.optional_methods == ()
    assert getattr(registration, 'native_tool_restriction', False) is False
    assert not hasattr(NativeCodexHost, 'get_request') and not hasattr(NativeCodexHost, 'cancel')


# --- S01 new chat, queue, correlation, terminal ------------------------------------------

def test_new_business_creates_zero_model_chat_binds_then_queues_and_tracks_unique_turn(adapter):
    desktop, events = adapter.desktop, Events()
    def client():
        return next(call for call in desktop.calls if call[0] == 'thread/queue/add')[2]
    desktop.script = [lambda: None,
                      lambda: desktop.consume('new-sid-0', client()),
                      lambda: desktop.consume('new-sid-0', client(), status='interrupted'),
                      lambda: desktop.consume('new-sid-0', client(), status='completed', completed=1700000000, text='done')]
    result = adapter.host.submit('op-1', HostInput('hello', attempt=attempt()), None,
                                 execution=execution(deadline='2999-01-01T00:00:00Z'), on_event=events, on_tool=lambda call: None)
    assert result.state == 'completed' and result.delivery == 'handled' and result.output == 'done'
    assert (result.session_id, result.request_id) == ('new-sid-0', 'turn-1')
    assert result.terminal.state == 'completed' and result.terminal.request_id == 'turn-1'
    # Owned creation writer closed before P28; P28 before queueing; never a turn/start or resume.
    order = methods(desktop)
    assert order[:5] == ['open', 'thread/start', 'close', 'open', 'thread/queue/add']
    assert not {'turn/start', 'thread/resume', 'thread/queue/start'} & set(order)
    assert events.kinds()[0] == ('session', 'unknown', None, 'unknown')
    assert events.kinds()[1] == ('accepted', 'accepted', None, 'accepted')
    assert ('session', 'running', 'turn-1', 'delivered') in events.kinds()
    # interrupted without completedAt was not a terminal; exactly one terminal event.
    assert [k for k in events.kinds() if k[0] == 'terminal'] == [('terminal', 'completed', 'turn-1', 'handled')]
    text = next(call for call in desktop.calls if call[0] == 'thread/queue/add')[3]
    assert 'digest-1' in text and 'frozen instructions' in text and 'assistant.native.tool' in text
    record = json.loads(next((adapter.host.root/'submissions').glob('*.json')).read_text())
    assert record['phase'] == 'completed' and record['requestId'] == 'turn-1' and 'hello' not in json.dumps(record)


def test_active_business_queues_into_original_chat_without_creating(adapter):
    adapter.desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    def finish():
        client = next(c for c in adapter.desktop.calls if c[0] == 'thread/queue/add')[2]
        adapter.desktop.consume('user-sid', client, status='completed', completed=1, text='ok')
    adapter.desktop.script = [finish]
    result = adapter.host.submit('op-2', HostInput('next', attempt=attempt()), 'user-sid',
                                 execution=execution(deadline='2999-01-01T00:00:00Z'), on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'completed' and result.session_id == 'user-sid'
    assert 'thread/start' not in methods(adapter.desktop)
    assert [c[1] for c in adapter.desktop.calls if c[0] == 'thread/queue/add'] == ['user-sid']


def test_waiting_turn_returns_accepted_at_deadline_and_never_requeues(adapter):
    adapter.desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    later = soon(.6)
    result = adapter.host.submit('op-3', HostInput('next', attempt=attempt()), 'user-sid',
                                 execution=execution(deadline=later), on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'accepted' and result.terminal is None and result.request_id is None
    assert methods(adapter.desktop).count('thread/queue/add') == 1


def test_queue_rejected_is_not_submitted_and_queue_loss_is_unknown_without_replay(adapter):
    adapter.desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    adapter.desktop.fail['thread/queue/add'] = WireRejected('refused')
    refused = adapter.host.submit('op-4', HostInput('x', attempt=attempt('a1')), 'user-sid',
                                  execution=execution(), on_event=Events(), on_tool=lambda c: None)
    assert refused.state == 'failed' and refused.error.dispatch == 'not_submitted' and refused.error.phase == 'native_queue'
    adapter.desktop.fail['thread/queue/add'] = WireError('stream ended after send')
    lost = adapter.host.submit('op-4', HostInput('x', attempt=attempt('a2')), 'user-sid',
                               execution=execution(), on_event=Events(), on_tool=lambda c: None)
    assert lost.state == 'unknown' and lost.error.dispatch == 'unknown'
    again = adapter.host.submit('op-4', HostInput('x', attempt=attempt('a2')), 'user-sid',
                                execution=execution(), on_event=Events(), on_tool=lambda c: None)
    assert again.error.dispatch == 'unknown'
    assert methods(adapter.desktop).count('thread/queue/add') == 2  # one per attempt, the unknown never replayed


def test_creation_unknown_keeps_unknown_and_creates_no_second_chat(adapter):
    adapter.desktop.fail['thread/start'] = WireError('lost')
    result = adapter.host.submit('op-5', HostInput('x', attempt=attempt()), None, execution=execution(),
                                 on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'unknown' and result.error.phase == 'native_session' and result.error.dispatch == 'unknown'
    assert 'thread/queue/add' not in methods(adapter.desktop)


def test_wrong_p28_ack_never_queues(adapter):
    def wrong(event):
        return BindingAck('other-sid')
    result = adapter.host.submit('op-6', HostInput('x', attempt=attempt()), None, execution=execution(),
                                 on_event=wrong, on_tool=lambda c: None)
    assert result.error.phase == 'session_binding' and result.session_id == 'new-sid-0'
    assert 'thread/queue/add' not in methods(adapter.desktop)


@pytest.mark.parametrize('turns,reason', [
    ([{'id': 't1', 'status': 'completed', 'completedAt': 1, 'items': [{'type': 'userMessage', 'clientId': '@'}]},
      {'id': 't2', 'status': 'completed', 'completedAt': 2, 'items': [{'type': 'userMessage', 'clientId': '@'}]}],
     'client_multiple_turns'),
])
def test_ambiguous_correlation_stays_unknown(adapter, turns, reason):
    desktop = adapter.desktop
    desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    def inject():
        client = next(c for c in desktop.calls if c[0] == 'thread/queue/add')[2]
        desktop.chats['user-sid']['turns'] = json.loads(json.dumps(turns).replace('@', client))
    desktop.script = [inject]
    result = adapter.host.submit('op-7', HostInput('x', attempt=attempt()), 'user-sid',
                                 execution=execution(deadline='2999-01-01T00:00:00Z'), on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'unknown' and result.terminal is None and reason in result.error.message


def test_reply_text_or_foreign_client_is_not_correlation(adapter):
    desktop = adapter.desktop
    desktop.chats['user-sid'] = {'queue': [], 'turns': [
        {'id': 'latest', 'status': 'completed', 'completedAt': 5, 'items': [
            {'type': 'userMessage', 'clientId': 'someone-else'}, {'type': 'agentMessage', 'text': 'IoA done'}]}]}
    later = soon(.6)
    result = adapter.host.submit('op-8', HostInput('x', attempt=attempt()), 'user-sid',
                                 execution=execution(deadline=later), on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'accepted' and result.request_id is None


# --- phase authorization, schema, resources ----------------------------------------------

def test_unrestricted_quote_review_and_observation_are_queued_not_refused(adapter):
    adapter.desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    restricted = execution(authorization={'nativeTools': {}, 'nativeToolsEnabled': False}, deadline=soon(.6))
    result = adapter.host.submit('op-9', HostInput('quote', attempt=attempt()), 'user-sid', execution=restricted,
                                 on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'accepted' and result.error is None
    observation = execution(kind='observation', authorization={'nativeTools': {}, 'nativeToolsEnabled': False},
                            tools=(HostToolDeclaration('verifier.observe', 'observe', SCHEMA),), deadline=soon(.6))
    result = adapter.host.submit('op-10', HostInput('observe', attempt=attempt()), 'user-sid', execution=observation,
                                 on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'accepted'
    texts = [c[3] for c in adapter.desktop.calls if c[0] == 'thread/queue/add']
    assert all('does not authorize native tools' not in text and 'do not use shell' not in text for text in texts)
    assert all('Protected confirmations happen only on the IoA browser page' in text for text in texts)


@pytest.mark.parametrize('changes,phase', [
    ({'authorization': {'nativeTools': {}, 'nativeToolsEnabled': True}, 'proposal_schema': {'type': 'object'}}, 'proposal_schema'),
    ({'definition': HostDefinition('d', '1', 'g', 'i', resources=(
        __import__('ioalab_sdk.runtime', fromlist=['HostResource']).HostResource('r', 'n', 'text/plain', None, 'job', 'ref'),))}, 'resource_mapping'),
    ({'deadline': '2000-01-01T00:00:00Z'}, 'deadline'),
])
def test_unsupported_turns_are_not_submitted_before_any_process(adapter, changes, phase):
    result = adapter.host.submit('op-11', HostInput('x', attempt=attempt()), 'user-sid', execution=execution(**changes),
                                 on_event=Events(), on_tool=lambda c: None)
    assert result.error.dispatch == 'not_submitted' and result.error.phase == phase
    assert adapter.desktop.calls == []


def test_same_effect_tool_carries_the_proposal_and_missing_setup_is_not_submitted(adapter):
    adapter.desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    later = soon(.6)
    result = adapter.host.submit('op-12', HostInput('x', attempt=attempt()), 'user-sid',
                                 execution=execution(proposal_schema=SCHEMA, deadline=later), on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'accepted' and result.proposal is None
    adapter.store.read = lambda: None
    missing = adapter.host.submit('op-13', HostInput('x', attempt=attempt()), 'user-sid', execution=execution(),
                                  on_event=Events(), on_tool=lambda c: None)
    assert missing.error.phase == 'desktop_location' and missing.error.dispatch == 'not_submitted'


# --- SDK tools through the fixed entry ----------------------------------------------------

def queued_tool_turn(adapter):
    desktop, events, calls = adapter.desktop, Events(), []
    desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    released = threading.Event()
    def on_tool(call):
        calls.append(call)
        return HostToolResult(value={'status': 'submitted'})
    holder = {}
    def run():
        holder['result'] = adapter.host.submit('op-t', HostInput('x', attempt=attempt()), 'user-sid',
            execution=execution(deadline='2999-01-01T00:00:00Z'), on_event=events, on_tool=on_tool)
        released.set()
    adapter.host.poll = (5., 5.)  # the tool call, not the watcher, establishes the binding here
    thread = threading.Thread(target=run); thread.start()
    # queue/add logs before scope publication. The first queue read proves the
    # watcher saw the accepted empty turn history. Its next read is five seconds
    # away, so the tool still establishes the exact turn binding in this test.
    assert desktop.queue_read.wait(10), holder.get('result')
    client = next(c for c in desktop.calls if c[0] == 'thread/queue/add')[2]
    desktop.consume('user-sid', client, turn='turn-9')
    return events, calls, thread, holder


def tool_body(**changes):
    value = {'kind': 'tool', 'sessionId': 'user-sid', 'turnId': 'turn-9', 'callId': 'call-1',
             'name': 'ioa_reply', 'arguments': {'text': 'hi'}}
    value.update(changes)
    return value


SCOPE = {'hostRef': 'codex', 'adapterId': 'codex', 'actor': 'actor-a', 'profile': 'profile-a'}


def test_tool_call_binds_exact_turn_through_p28_before_the_sdk_callback(adapter):
    events, calls, thread, _ = queued_tool_turn(adapter)
    reply = adapter.host.exchange(SCOPE, tool_body())
    assert reply == {'toolResult': {'value': {'status': 'submitted'}, 'resources': (), 'error': None}}
    assert [(c.session_id, c.request_id, c.call_id, c.name) for c in calls] == [('user-sid', 'turn-9', 'call-1', 'ioa_reply')]
    assert events.kinds().index(('session', 'running', 'turn-9', 'delivered')) < len(events.seen)
    # Replay of the same native call returns the original result without a second effect.
    assert adapter.host.exchange(SCOPE, tool_body()) == reply and len(calls) == 1
    with pytest.raises(RuntimeFault) as changed:
        adapter.host.exchange(SCOPE, tool_body(arguments={'text': 'other'}))
    assert changed.value.code == 'id_conflict'
    adapter.host.drain(2); thread.join(5)


def test_tool_call_refusals_never_reach_the_sdk_callback(adapter):
    events, calls, thread, _ = queued_tool_turn(adapter)
    for scope, body, code in [({**SCOPE, 'actor': 'actor-b'}, tool_body(), 'not_ready'),
                              (SCOPE, tool_body(turnId='other-turn'), 'identity_conflict'),
                              ({**SCOPE, 'adapterId': 'claude_code'}, tool_body(), 'identity_conflict')]:
        with pytest.raises(RuntimeFault) as refused:
            adapter.host.exchange(scope, body)
        assert refused.value.code == code
    invalid = adapter.host.exchange(SCOPE, tool_body(callId='call-2', arguments={'wrong': 1}))
    assert invalid['toolResult']['error']['code'] == 'invalid_input' and calls == []
    with pytest.raises(RuntimeFault) as forbidden:
        adapter.host.exchange(SCOPE, tool_body(callId='call-3', name='provider.task.deliver'))
    assert forbidden.value.code == 'forbidden' and calls == []
    adapter.host.drain(2); thread.join(5)


# --- notices, registry integration and lifecycle ------------------------------------------

def test_notice_returns_accepted_and_registry_receives_later_machine_terminal(adapter, tmp_path):
    store = RuntimeStore(tmp_path/'runtime.sqlite', 'http://localhost', ['actor-a'], owner_id='owner')
    try:
        registry = HostRegistry({'codex': adapter.host}, 'codex')
        registry.bind(store, identities=[SimpleNamespace(agent_id='actor-a', profile='profile-a')])
        desktop = adapter.desktop
        def finish():
            client = next(c for c in desktop.calls if c[0] == 'thread/queue/add')[2]
            desktop.consume('new-sid-0', client, status='completed', completed=1700000001, text='read')
        desktop.script = [finish]
        receipt = registry.submit('actor-a', 'business-n', 'notice-1', HostInput('notice body'))
        assert receipt.state == 'accepted' and receipt.delivery == 'accepted' and receipt.terminal is None
        assert registry.context('actor-a', 'business-n')['sessionId'] == 'new-sid-0'
        for _ in range(300):
            stored = registry.get_submission('actor-a', 'business-n', 'notice-1')
            if stored.terminal:
                break
            time.sleep(.01)
        assert stored.state == 'completed' and stored.delivery == 'handled' and stored.request_id == 'turn-1'
    finally:
        adapter.host.close(); store.close()


def test_drain_stops_watchers_and_refuses_new_queueing(adapter):
    adapter.desktop.chats['user-sid'] = {'queue': [], 'turns': []}
    adapter.host.submit('notice-2', HostInput('n', attempt=attempt()), 'user-sid', on_event=Events())
    assert adapter.host.watchers
    assert adapter.host.drain(2) is True and not adapter.host.watchers
    result = adapter.host.submit('notice-3', HostInput('n', attempt=attempt('a3')), 'user-sid', on_event=Events())
    assert result.error.code == 'closed' and result.error.dispatch == 'not_submitted'


def test_product_factory_routes_tool_control_to_route_a_and_setup_to_setup(tmp_path):
    bundle = native_adapter(host_ref='codex', resource_root=tmp_path/'adapter')
    handler = bundle['native_handlers']['codex']
    with pytest.raises(RuntimeFault) as refused:
        handler(SCOPE, tool_body())
    assert refused.value.code == 'not_ready'  # no queued turn: never a callback
    resource = bundle['native_resources']['codex']
    assert resource.companion is bundle['host'] and resource.drain(1) is True
