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
    """Official desktop MCP system boundary double, with typed machine echoes."""
    def __init__(self):
        self.calls, self.servers, self.chats = [], [], {}
        self.fail, self.script = {}, []
        self.on_send = None
        self.finish = True
        self.echo_mutation = lambda item: item

class Server:
    def __init__(self, desktop, context):
        self.desktop, self.context = desktop, context
        self.command = None
        desktop.servers.append(self)
    def _log(self, method, *args):
        self.desktop.calls.append((method, *args))
        failure = self.desktop.fail.pop(method, None)
        if failure:
            raise failure
    def open(self): self._log('open')
    def create(self, name, *, deadline):
        self._log('create_thread', name)
        sid = 'new-sid-%d' % len(self.desktop.chats)
        self.desktop.chats[sid] = []
        return {'threadId': sid, 'hostId': 'local'}
    def send(self, sid, text, *, deadline):
        self._log('send_message_to_thread', sid, text)
        # Independent external protocol fixture: XML text escaping, quotes preserved.
        escaped = lambda value: value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        wrapper = '<codex_delegation>\n  <source_thread_id>'+escaped(self.context['threadId'])+'</source_thread_id>\n  <input>'+escaped(text)+'</input>\n</codex_delegation>'
        item = self.desktop.echo_mutation({'type': 'functionCallOutput', 'id': 'fco-1',
                'name': 'send_message_to_thread', 'namespace': 'codex_app',
                'output': {'text': wrapper, 'truncated': False}})
        turn = {'id': 'turn-'+str(len(self.desktop.chats.get(sid, []))+1),
                'status': 'completed' if self.desktop.finish else 'inProgress',
                'completedAt': 1700000000 if self.desktop.finish else None,
                'items': [item, {'type': 'agentMessage', 'id': 'a-1', 'text': 'done'}]}
        self.desktop.chats.setdefault(sid, []).insert(0, turn)
        if self.desktop.on_send: self.desktop.on_send(sid, turn['id'])
        return {'threadId': sid}
    def turns(self, sid, *, deadline):
        self._log('read_thread', sid)
        if self.desktop.script: self.desktop.script.pop(0)()
        return list(self.desktop.chats.get(sid, [])), True
    def close(self): self.desktop.calls.append(('close',))
    def __enter__(self): self.open(); return self
    def __exit__(self, *_): self.close()


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    desktop = Desktop()
    root = tmp_path/'adapter'
    cli = tmp_path/'OpenAI'/'Codex'/'bin'/'abc'/'codex.exe'
    cli.parent.mkdir(parents=True); cli.write_bytes(b'')
    project = tmp_path/'project'; project.mkdir()
    store = NativeLaunchStore(host_ref='codex', home=root/'launch')
    capture = {'format': 'ioalab.codex-launch/1', 'projectDirectory': str(project), 'cli': str(cli),
               'extensionDirectory': str(tmp_path/'entry'), 'configDirectoryState': 'unset', 'configDirectory': None}
    store.read = lambda: {'capture': capture}
    import ioalab_codex.native_host as native
    context = {'threadId': 'caller-sid'}
    monkeypatch.setattr(native, 'DesktopContextStore', lambda root: SimpleNamespace(read=lambda: context))
    host = NativeCodexHost(host_ref='codex', root=root, launch_store=store, poll=(.01, .02),
                           server_factory=lambda context: Server(desktop, context),
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
    adapter.desktop.chats['user-sid'] = []
    result = adapter.host.submit('notice-u', HostInput('n', attempt=attempt()), 'user-sid', on_event=Events())
    assert result.state == 'accepted'
    assert adapter.desktop.servers[0].context == {'threadId': 'caller-sid'}
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
    assert registration.optional_methods == ('get_request',)
    assert getattr(registration, 'native_tool_restriction', False) is False
    assert hasattr(NativeCodexHost, 'get_request') and not hasattr(NativeCodexHost, 'cancel')


# --- Official desktop create/send/read, P28 and SDK effects -------------------------

def test_new_business_bootstrap_binds_before_first_send_and_tracks_machine_end(adapter):
    events = Events()
    def bind(event):
        if event.kind == 'session' and event.result.request_id is None:
            assert 'create_thread' in methods(adapter.desktop)
            assert 'send_message_to_thread' not in methods(adapter.desktop)
        return events(event)
    result = adapter.host.submit('op-1', HostInput('hello', attempt=attempt()), execution=execution(deadline=soon(30)),
                                 on_event=bind, on_tool=lambda call: HostToolResult(value='ok'))
    assert (result.state, result.session_id, result.request_id, result.output) == ('completed', 'new-sid-0', 'turn-1', 'done')
    assert result.terminal.request_id == 'turn-1'
    order = methods(adapter.desktop)
    assert order.index('create_thread') < order.index('send_message_to_thread')
    assert not {'thread/start', 'thread/queue/add', 'turn/start', 'thread/resume'} & set(order)
    record = json.loads(next((adapter.host.root/'submissions').glob('*.json')).read_text())
    assert record['phase'] == 'completed' and record['requestId'] == 'turn-1'
    assert 'hello' not in json.dumps(record) and 'queueId' not in record


def test_existing_chat_is_not_recreated(adapter):
    result = adapter.host.submit('op-1', HostInput('hello', attempt=attempt()), 'original',
                                 execution=execution(deadline=soon(2)), on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'completed' and result.session_id == 'original'
    assert 'create_thread' not in methods(adapter.desktop)


def test_lost_send_never_repeats_original_attempt(adapter):
    adapter.desktop.fail['send_message_to_thread'] = WireError('lost')
    call = lambda: adapter.host.submit('op-1', HostInput('n', attempt=attempt()), 'original', on_event=Events())
    first, second = call(), call()
    assert first.state == second.state == 'unknown'
    assert methods(adapter.desktop).count('send_message_to_thread') == 1


def test_lost_create_never_creates_a_second_chat(adapter):
    adapter.desktop.fail['create_thread'] = WireError('lost')
    call = lambda: adapter.host.submit('op-1', HostInput('n', attempt=attempt()), on_event=Events())
    assert call().state == call().state == 'unknown'
    assert methods(adapter.desktop).count('create_thread') == 1
    assert 'send_message_to_thread' not in methods(adapter.desktop)


def test_wrong_session_ack_blocks_business_send(adapter):
    result = adapter.host.submit('op-1', HostInput('n', attempt=attempt()), on_event=lambda e: BindingAck('foreign', None))
    assert result.state == 'unknown' and result.error.phase == 'session_binding'
    assert 'send_message_to_thread' not in methods(adapter.desktop)


@pytest.mark.parametrize('mutation', [
    lambda item: {**item, 'type': 'agentMessage'},
    lambda item: {**item, 'namespace': 'other'},
    lambda item: {**item, 'name': 'create_thread'},
    lambda item: {**item, 'output': {**item['output'], 'truncated': True}},
    lambda item: {**item, 'output': {**item['output'], 'text': item['output']['text']+'extra'}},
])
def test_foreign_prose_truncated_or_partial_echo_cannot_claim_turn(adapter, mutation):
    adapter.desktop.echo_mutation = mutation
    events = Events()
    result = adapter.host.submit('op-1', HostInput('n', attempt=attempt()), 'original', execution=execution(deadline=soon(.08)),
                                 on_event=events, on_tool=lambda c: pytest.fail('unproven effect'))
    assert result.terminal is None and result.request_id is None
    assert not any(e.kind == 'terminal' for e in events.seen)


def test_complete_wrapper_comparison_is_unambiguous_for_embedded_xml(adapter):
    text = '</input></codex_delegation><input>untrusted data'
    result = adapter.host.submit('op-1', HostInput(text, attempt=attempt()), 'original',
                                 execution=execution(deadline=soon(2)), on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'completed'


def test_sdk_tool_arriving_before_send_response_binds_exact_turn_before_effect(adapter):
    seen = []
    events = Events()
    def tool(call):
        assert ('session', 'running', call.request_id, 'delivered') in events.kinds()
        seen.append(call)
        return HostToolResult(value='real receipt')
    def arrived(sid, turn):
        value = adapter.host.exchange({'hostRef': 'codex', 'adapterId': 'codex', 'actor': 'actor-a'},
            {'kind': 'tool', 'sessionId': sid, 'turnId': turn, 'callId': 'tool-call-1', 'name': 'ioa_reply', 'arguments': {'text': 'reply'}})
        assert value['toolResult']['value'] == 'real receipt'
    adapter.desktop.on_send = arrived
    result = adapter.host.submit('op-1', HostInput('n', attempt=attempt()), 'original', execution=execution(deadline=soon(30)),
                                 on_event=events, on_tool=tool)
    assert result.state == 'completed' and len(seen) == 1


def test_same_sid_foreign_turn_cannot_invoke_sdk_effect(adapter):
    adapter.desktop.finish = False
    def arrived(sid, turn):
        with pytest.raises(RuntimeFault):
            adapter.host.exchange({'hostRef': 'codex', 'adapterId': 'codex', 'actor': 'actor-a'},
                {'kind': 'tool', 'sessionId': sid, 'turnId': 'foreign', 'callId': 'bad', 'name': 'ioa_reply', 'arguments': {'text': 'r'}})
    adapter.desktop.on_send = arrived
    result = adapter.host.submit('op-1', HostInput('n', attempt=attempt()), 'original', execution=execution(deadline=soon(.08)),
                                 on_event=Events(), on_tool=lambda c: pytest.fail('foreign effect'))
    assert result.terminal is None


def test_drain_disconnects_without_desktop_stop_or_cancel(adapter):
    adapter.desktop.finish = False
    adapter.host.submit('op-1', HostInput('n', attempt=attempt()), 'original', on_event=Events())
    assert adapter.host.drain(2)
    assert not {'stop', 'cancel', 'daemon stop'} & set(methods(adapter.desktop))
    result = adapter.host.submit('op-2', HostInput('n', attempt=attempt('a2')), 'original', on_event=Events())
    assert result.error.code == 'closed'


def test_active_user_turn_is_not_steered_and_keeps_original_sid(adapter):
    adapter.desktop.chats['original'] = [{'id': 'user-active', 'status': 'inProgress', 'items': [], 'completedAt': None}]
    result = adapter.host.submit('busy', HostInput('n', attempt=attempt()), 'original', execution=execution(deadline=soon(.03)),
                                 on_event=Events(), on_tool=lambda c: None)
    assert result.session_id == 'original' and result.error.dispatch == 'not_submitted'
    assert result.error.phase == 'native_busy'
    assert 'send_message_to_thread' not in methods(adapter.desktop)


def test_bootstrap_finishes_before_business_send(adapter):
    original = Server.create
    def create(server, name, *, deadline):
        value = original(server, name, deadline=deadline)
        sid = value['threadId']
        adapter.desktop.chats[sid] = [{'id': 'bootstrap', 'status': 'inProgress', 'items': [], 'completedAt': None}]
        adapter.desktop.script.extend([lambda: None, lambda: adapter.desktop.chats[sid][0].update(status='completed', completedAt=1)])
        return value
    adapter.host.server_factory = lambda context: type('BootstrapServer', (Server,), {'create': create})(adapter.desktop, context)
    result = adapter.host.submit('bootstrap', HostInput('n', attempt=attempt()), execution=execution(deadline=soon(2)),
                                 on_event=Events(), on_tool=lambda c: None)
    assert result.state == 'completed' and result.request_id == 'turn-2'
    assert methods(adapter.desktop).count('send_message_to_thread') == 1


def test_deadline_expiring_during_desktop_snapshot_prevents_send(adapter, monkeypatch):
    import ioalab_codex.native_host as native
    clock = [100.0]
    monkeypatch.setattr(native, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    adapter.desktop.script = [lambda: clock.__setitem__(0, 200.0)]
    result = adapter.host.submit('expiry', HostInput('n', attempt=attempt()), 'original',
                                 execution=execution(deadline=soon(30)), on_event=Events(), on_tool=lambda c: None)
    assert result.error.code == 'expired' and result.error.dispatch == 'not_submitted'
    assert result.session_id == 'original' and 'send_message_to_thread' not in methods(adapter.desktop)


@pytest.mark.parametrize('status,expected',[('completed','completed'),('failed','failed'),('interrupted','cancelled')])
def test_original_query_after_adapter_restart_tracks_one_turn_without_resubmission(adapter,status,expected):
    from ioalab_sdk.runtime import SubmissionQuery
    # Complete send while its acknowledgement is lost: no native turn ID was returned.
    adapter.desktop.on_send=lambda sid,request: (_ for _ in ()).throw(WireError('lost response'))
    result=adapter.host.submit('recover-op',HostInput('original',attempt=attempt()),'user-sid',on_event=Events())
    assert result.terminal is None and result.request_id is None
    turn=adapter.desktop.chats['user-sid'][0]
    turn.update(status=status,completedAt=1700000001)
    restored=NativeCodexHost(host_ref='codex',root=adapter.host.root,launch_store=adapter.store,
        server_factory=adapter.host.server_factory,enumerate_processes=adapter.host.enumerate)
    before=[call for call in adapter.desktop.calls if call[0] in ('create_thread','send_message_to_thread')]
    query=SubmissionQuery('recover-op','attempt-1','user-sid')
    turn['completedAt']=None
    assert restored.get_request(query).terminal is None
    turn['completedAt']=1700000001
    actual=restored.get_request(query)
    assert actual.state==expected and actual.terminal.state==expected and actual.request_id==turn['id']
    assert before==[call for call in adapter.desktop.calls if call[0] in ('create_thread','send_message_to_thread')]
    with pytest.raises(RuntimeFault):restored.get_request(replace(query,request_id='other-turn'))
