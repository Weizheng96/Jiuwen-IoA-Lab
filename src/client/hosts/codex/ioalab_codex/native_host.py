"""Route A product adapter: business turns run only in the user's own Codex desktop.

Business messages use the official desktop MCP create/send/read tools. A new
chat first receives harmless bootstrap context, then P28 binds its real SID
before any business is sent. Exact typed delegation input echoes correlate
the unique turn; machine completedAt proves its end. Acceptance is only
acceptance; SDK tools arrive through the fixed entry and narrow hook
as native control calls. Native approvals stay in the desktop UI. S04/S05 are
not declared and native tool restriction is not declared (D37).
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import copy
import hashlib
from importlib import metadata
import json
from pathlib import Path
import threading
import time
import uuid

from jsonschema import Draft202012Validator
from ioalab_sdk.runtime import (HostRegistration, HostResource, HostInput, HostExecution, HostToolCall,
                               HostToolResult, BindingAck, MachineTerminal, RuntimeFault,
                               SubmissionResult, SubmissionError, SubmissionEvent, private_json)
from . import native_facts
from .native_desktop import DesktopHealth, current_cli, windows_processes
from .native_hook import entry_binding, load_config
from .native_process import NativeLaunchStore
from .native_render import execution_text, notice_text
from .native_setup import NativeSetup
from .native_skills import NATIVE_FILES
from .native_app_tools import NativeAppTools
from .desktop_context import DesktopContextStore
from .transport import WireError, WireRejected

STEP_SECONDS = 30
TOOL_FIELDS = {'kind', 'sessionId', 'turnId', 'callId', 'name', 'arguments'}


def _registration():
    try:
        version = metadata.version('ioalab-codex')
    except metadata.PackageNotFoundError:
        version = 'uninstalled'
    # No optional S04/S05 and no native_tool_restriction: route A cannot prove them (D32/D37).
    return HostRegistration('codex', version, readonly_context='business_session', resources=tuple(
        HostResource('codex-native:'+name, name, 'application/octet-stream', None, 'installation',
                     'package:ioalab_codex/resources/native/'+name) for name in NATIVE_FILES))


class _Refused(Exception):
    def __init__(self, code, phase, message, action=None):
        super().__init__(message)
        self.code, self.phase, self.message, self.action = code, phase, message, action


def _deadline(execution):
    if execution is None or execution.deadline is None:
        return None
    try:
        value = datetime.fromisoformat(execution.deadline.replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise _Refused('invalid_input', 'deadline', 'Use the original timezone-aware deadline') from None
    remaining = (value-datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise _Refused('expired', 'deadline', 'The original execution deadline has passed')
    return time.monotonic()+remaining


class _Entry:
    """One accepted desktop message; only its own watcher or tool calls change it."""

    def __init__(self, operation_id, attempt, actor, execution, sid, client, path, record, on_event, on_tool, deadline):
        self.operation_id, self.attempt, self.actor, self.execution = operation_id, attempt, actor, execution
        self.sid, self.client, self.path, self.record = sid, client, path, record
        self.on_event, self.on_tool, self.deadline = on_event, on_tool, deadline
        self.request = None
        self.result = None
        self.finished = False
        self.calls = {}
        self.lock = threading.RLock()


class NativeCodexHost:
    registration = _registration()

    def __init__(self, *, host_ref, root, launch_store, server_factory=NativeAppTools,
                 enumerate_processes=windows_processes, poll=(2., 15.), health_samples=3):
        self.host_ref, self.root = host_ref, Path(root)
        self.launch_store = launch_store
        self.server_factory = server_factory
        self.poll = poll
        self.enumerate = enumerate_processes
        self.health = DesktopHealth(self._desktop_cli, enumerate_processes=enumerate_processes, samples=health_samples)
        self.condition = threading.Condition(threading.RLock())
        self.entries, self.watchers = {}, set()
        self.tool_calls = 0
        self.closing = threading.Event()

    # --- S02 -----------------------------------------------------------------------

    def _desktop_cli(self):
        record = self.launch_store.read()
        return None if record is None else record['capture']['cli']

    def check(self):
        return self.health.check()

    # --- S01 -----------------------------------------------------------------------

    def _launch(self):
        try:
            record = self.launch_store.read()
        except RuntimeFault as exc:
            raise _Refused(exc.code, 'desktop_location', str(exc), exc.recovery) from None
        if record is None:
            raise _Refused('not_ready', 'desktop_location', 'Run the explicit Codex setup from the desktop chat first',
                           'run_native_setup')
        capture = dict(record['capture'])
        try:
            cli = current_cli(capture['cli'], () if Path(capture['cli']).is_file() else self.enumerate())
        except Exception:
            cli = None
        if cli is None:
            raise _Refused('not_ready', 'desktop_location', 'The captured desktop server is not installed and no running '
                           'desktop server of the same installation was found', 'open_codex_desktop')
        capture['cli'] = cli
        try:
            capture['desktopContext'] = DesktopContextStore(self.root).read()
        except (RuntimeFault, OSError, ValueError) as exc:
            raise _Refused('not_ready', 'desktop_context', 'Refresh the desktop binding from the real fixed entry: '+str(exc), 'run_native_setup') from None
        if capture['desktopContext'] is None:
            raise _Refused('not_ready', 'desktop_context', 'Bind the official desktop MCP context from the fixed entry', 'run_native_setup')
        return capture

    def _server(self, capture):
        return self.server_factory(capture['desktopContext'])

    def _tool_entry(self, profile):
        try:
            _, bound = load_config(self.root/'codex-hook'/'hook.json')
            for role, value in bound.items():
                if entry_binding(value['entry'], role)[1]['profile'] == profile:
                    return value['python'], value['entry'], role
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return None

    def submit(self, operation_id, input, session_id=None, *, execution=None, on_event=None, on_tool=None):
        if not isinstance(input, HostInput) or input.attempt is None:
            raise RuntimeFault('invalid_input', 'Use the original SDK-owned native attempt')
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise RuntimeFault('invalid_input', 'Only None selects a new native chat')
        attempt = input.attempt
        if execution is not None:
            if not isinstance(execution, HostExecution):
                raise RuntimeFault('invalid_input', 'Use the frozen SDK execution')
            if (execution.actor, execution.profile) != (attempt.actor, attempt.profile):
                raise RuntimeFault('identity_conflict', 'Execution actor/profile differs from the original attempt')
        try:
            prepared = self._prepare(operation_id, input, execution, on_event, on_tool)
        except _Refused as refused:
            return SubmissionResult(operation_id, attempt.nonce, 'failed', session_id, error=SubmissionError(
                refused.code, refused.phase, 'not_submitted', refused.message, refused.action))
        return self._dispatch(operation_id, input, session_id, execution, on_event, on_tool, *prepared)

    def _prepare(self, operation_id, input, execution, on_event, on_tool):
        if not callable(on_event):
            raise _Refused('not_ready', 'native_events', 'Bind the original SDK P28 events before queueing')
        if self.closing.is_set():
            raise _Refused('closed', 'native_queue', 'The Codex adapter is draining')
        deadline, entry = None, None
        if execution is not None:
            if type(execution.authorization.get('nativeToolsEnabled', False)) is not bool:
                raise _Refused('invalid_input', 'native_permissions', 'SDK native-tool authorization must be a strict boolean')
            if execution.definition.resources:
                raise _Refused('not_ready', 'resource_mapping', 'Map the original frozen P26 resources before queueing',
                               'bind_native_files')
            schema = execution.proposal_schema
            if schema is not None and not any(tool.input_schema == schema for tool in execution.tools):
                # No structured output channel; only a same-effect SDK tool may carry the proposal.
                raise _Refused('not_ready', 'proposal_schema', 'Route A has no structured proposal channel for this turn')
            deadline = _deadline(execution)
            if execution.tools:
                if not callable(on_tool):
                    raise _Refused('not_ready', 'sdk_tools', 'Bind the original SDK tool callback')
                entry = self._tool_entry(input.attempt.profile)
                if entry is None:
                    raise _Refused('not_ready', 'sdk_tools', 'Install and verify the fixed IoA entry hook for this role',
                                   'install_native_hook')
        if input.resources:
            raise _Refused('not_ready', 'resource_mapping', 'Map the original input resources before queueing', 'bind_native_files')
        capture = self._launch()
        text = (notice_text(input, operation_id=operation_id) if execution is None else
                execution_text(input, execution, operation_id=operation_id, entry=entry))
        return capture, text, deadline

    def _record_path(self, operation_id, attempt):
        return self.root/'submissions'/(hashlib.sha256(json.dumps([operation_id, attempt]).encode()).hexdigest()+'.json')

    def _dispatch(self, operation_id, input, session_id, execution, on_event, on_tool, capture, text, deadline):
        attempt = input.attempt.nonce
        path = self._record_path(operation_id, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('x', encoding='utf-8'):
                pass  # Exclusive durable claim; a crash keeps this attempt ambiguous.
        except FileExistsError:
            return SubmissionResult(operation_id, attempt, 'unknown', session_id, error=SubmissionError(
                'unknown_result', 'native_send', 'unknown', 'The original attempt is already claimed; inspect it', 'inspect_original_request'))
        record = {'format': 'ioalab.codex-route-a/1', 'operation': operation_id, 'attempt': attempt,
                  'kind': execution.kind if execution else 'notice', 'sessionId': session_id, 'created': session_id is None,
                  'submissionNonce': str(uuid.uuid4()), 'textSha256': hashlib.sha256(text.encode()).hexdigest(),
                  'phase': 'claimed', 'requestId': None}
        text = text+'\n[IoA submission '+record['submissionNonce']+'] attempt '+attempt
        digest = native_facts.delegation_digest(capture['desktopContext']['threadId'], text)
        record['delegationDigest'] = digest
        private_json(path, record)
        def save(**changes):
            record.update(changes)
            private_json(path, record)
        def fail(code, phase, dispatch, message, action=None, sid=None):
            save(phase='not_submitted' if dispatch == 'not_submitted' else 'unknown', failure={'code': code, 'phase': phase})
            state = 'failed' if dispatch == 'not_submitted' else 'unknown'
            return SubmissionResult(operation_id, attempt, state, sid, error=SubmissionError(code, phase, dispatch, message, action))
        sid = session_id
        if sid is None:
            name = 'IoA Lab · '+record['kind']+' · '+hashlib.sha256(
                (execution.business_ref if execution else operation_id).encode()).hexdigest()[:8]
            server = self._server(capture)
            try:
                server.open()
            except Exception:
                return fail('not_ready', 'native_session', 'not_submitted', 'The official desktop MCP connection did not open; no chat was created')
            try:
                save(phase='creating')
                created = server.create(name, deadline=time.monotonic()+STEP_SECONDS)
                sid = created['threadId']
                named = True
            except WireRejected:
                return fail('not_ready', 'native_session', 'not_submitted', 'Codex refused to create the persistent chat')
            except Exception:
                return fail('unknown_result', 'native_session', 'unknown',
                            'Chat creation outcome is unknown; no business message was queued and no other chat is created',
                            'inspect_original_request')
            finally:
                server.close()  # Owned writer exits before the SID is bound or used.
            save(sessionId=sid, named=named, phase='created')
            try:
                ack = on_event(SubmissionEvent(self._event_id(operation_id, attempt, 'session', sid, None), 'session',
                                               SubmissionResult(operation_id, attempt, 'unknown', sid)))
                if not isinstance(ack, BindingAck) or ack.session_id != sid or ack.request_id is not None:
                    raise RuntimeFault('identity_conflict', 'SDK P28 did not bind the real native chat')
            except Exception as exc:
                return fail('unknown_result', 'session_binding', 'unknown', 'The real chat exists but its P28 binding is not confirmed: '
                            + type(exc).__name__, 'inspect_original_request', sid=sid)
        server = self._server(capture)
        try:
            server.open()
        except Exception:
            return fail('not_ready', 'native_queue', 'not_submitted', 'The official desktop MCP connection did not open; nothing was sent', sid=sid)
        entry = _Entry(operation_id, attempt, input.attempt.actor, execution, sid, digest, path,
                       record, on_event, on_tool, deadline)
        send_started = False
        try:
            turns, complete = server.turns(sid, deadline=time.monotonic()+STEP_SECONDS)
            if not complete:
                return fail('not_ready', 'native_snapshot', 'not_submitted', 'The original desktop turn snapshot is incomplete', sid=sid)
            active = lambda: any(turn.get('status') == 'inProgress' for turn in turns)
            if active():
                # Finish harmless initialization; never steer it with business work.
                until = deadline if deadline is not None else time.monotonic()+STEP_SECONDS
                while active() and time.monotonic() < until and not self.closing.wait(.25):
                    turns, complete = server.turns(sid, deadline=min(until, time.monotonic()+STEP_SECONDS))
                    if not complete:
                        break
            if not complete or active() or self.closing.is_set():
                return fail('not_ready', 'native_busy', 'not_submitted',
                            'The original desktop chat is still running; no message was sent or steered', 'wait_original_chat', sid=sid)
            save(beforeTurnIds=[turn['id'] for turn in turns], phase='sending')
            if deadline is not None and time.monotonic() >= deadline:
                return fail('expired', 'deadline', 'not_submitted', 'The original execution deadline passed before desktop submission', sid=sid)
            # Register before send: native SDK calls can arrive before the MCP response.
            with self.condition:
                self.entries[(operation_id, attempt)] = entry
            send_started = True
            send_deadline = time.monotonic()+STEP_SECONDS
            if deadline is not None:
                send_deadline = min(send_deadline, deadline)
            sent = server.send(sid, text, deadline=send_deadline)
            if sent.get('threadId') != sid:
                raise WireError('Official desktop send returned a different chat')
        except Exception as exc:
            with self.condition:
                self.entries.pop((operation_id, attempt), None)
            dispatch = 'unknown' if send_started else 'not_submitted'
            return fail('unknown_result' if dispatch == 'unknown' else 'not_ready', 'native_send', dispatch,
                        'Inspect the original desktop submission; do not send it again: '+type(exc).__name__,
                        'inspect_original_request', sid=sid)
        finally:
            server.close()  # Disconnect only; the official desktop owns the running chat.
        with entry.lock:
            save(phase='delivered' if entry.request else 'sent')
        with entry.lock:
            if entry.request is None:
                entry.result = SubmissionResult(operation_id, attempt, 'accepted', sid, delivery='accepted')
        try:
            with entry.lock:
                if entry.request is None:
                    self._publish(entry, 'accepted', entry.result)
        except Exception as exc:
            return self._lost(entry, exc)
        with self.condition:
            self.entries[(operation_id, attempt)] = entry
            watcher = threading.Thread(target=self._watch, args=(entry, capture), daemon=True)
            self.watchers.add(watcher)
        watcher.start()
        if execution is None:
            return entry.result  # Notice: accepted now; the watcher keeps following the original turn.
        with self.condition:
            while not (entry.finished or self.closing.is_set() or (deadline is not None and time.monotonic() >= deadline)):
                self.condition.wait(60 if deadline is None else min(60, max(0, deadline-time.monotonic())))
        with entry.lock:
            return entry.result

    @staticmethod
    def _event_id(operation_id, attempt, kind, sid, request, state=None):
        return hashlib.sha256(json.dumps([operation_id, attempt, kind, sid, request, state]).encode()).hexdigest()

    def _publish(self, entry, kind, result):
        ack = entry.on_event(SubmissionEvent(self._event_id(entry.operation_id, entry.attempt, kind, result.session_id,
                                                            result.request_id, result.state), kind, result))
        if kind == 'session' and (not isinstance(ack, BindingAck) or
                                  (ack.session_id, ack.request_id) != (result.session_id, result.request_id)):
            raise RuntimeFault('identity_conflict', 'SDK P28 did not acknowledge the exact chat and turn')
        return ack

    def _lost(self, entry, exc):
        with entry.lock:
            entry.result = replace(entry.result, state='unknown' if entry.result.terminal is None else entry.result.state,
                                   error=SubmissionError('unknown_result', 'event_reporting', 'accepted',
                                                         'Original queue item is kept; event reporting failed: '+type(exc).__name__,
                                                         'inspect_original_request'))
            entry.finished = True
            entry.record.update(phase='unknown', failure={'code': 'event_reporting', 'type': type(exc).__name__})
            private_json(entry.path, entry.record)
        with self.condition:
            self.condition.notify_all()
        return entry.result

    def _bind_turn(self, entry, turn_id):
        """P28 exact SID/turn ACK once, before any SDK tool of that turn."""
        with entry.lock:
            if entry.request is not None:
                if entry.request != turn_id:
                    raise native_facts.IdentityConflict('queued_turn_changed')
                return
            result = SubmissionResult(entry.operation_id, entry.attempt, 'running', entry.sid, turn_id, 'delivered')
            self._publish(entry, 'session', result)
            entry.request, entry.result = turn_id, result
            entry.record.update(phase='delivered', requestId=turn_id)
            private_json(entry.path, entry.record)

    def _finish(self, entry, turn):
        with entry.lock:
            terminal = MachineTerminal(entry.operation_id, entry.attempt, entry.sid, entry.request, 'completed',
                                       native_facts.evidence(turn))
            result = replace(entry.result, state='completed', delivery='handled', output=native_facts.output(turn),
                             terminal=terminal)
            entry.result = result
            entry.record.update(phase='completed', turn=native_facts.summary(turn))
            private_json(entry.path, entry.record)
            try:
                self._publish(entry, 'terminal', result)
            except Exception as exc:
                entry.result = replace(result, error=SubmissionError('host_event_error', 'event_callback', 'accepted', type(exc).__name__))
            entry.finished = True

    def _poll(self, entry, capture):
        """One bounded read-only pass; True when the original turn reached its machine end."""
        with self._server(capture) as server:
            deadline = time.monotonic()+STEP_SECONDS
            turns, complete = server.turns(entry.sid, deadline=deadline)
            if not complete:
                return False
            turn = native_facts.correlate_delegation(turns, entry.client, entry.record.get('beforeTurnIds', ()))
            if turn is None:
                return False
        self._bind_turn(entry, turn['id'])
        with entry.lock:
            entry.record['turn'] = native_facts.summary(turn)
            private_json(entry.path, entry.record)
        if native_facts.terminal(turn):
            self._finish(entry, turn)
            return True
        return False

    def _watch(self, entry, capture):
        delay, ceiling = self.poll
        try:
            while not entry.finished:
                timeout = delay if entry.deadline is None else min(delay, entry.deadline-time.monotonic())
                if timeout <= 0 or self.closing.wait(timeout):
                    break
                try:
                    if self._poll(entry, capture):
                        break
                except native_facts.IdentityConflict as conflict:
                    with entry.lock:
                        entry.result = replace(entry.result, state='unknown', error=SubmissionError(
                            'unknown_result', 'native_correlation', 'accepted', 'Persisted history does not prove one original turn: '+str(conflict),
                            'inspect_original_request'))
                        entry.record.update(phase='unknown', failure={'code': 'native_correlation', 'reason': str(conflict)})
                        private_json(entry.path, entry.record)
                        entry.finished = True
                    break
                except (WireError, OSError):
                    pass  # A failed read is not a fact; the original item is followed again.
                except Exception as exc:
                    self._lost(entry, exc)  # Event reporting or binding failed; keep the original item.
                    break
                delay = min(ceiling, delay*1.5)
        finally:
            with self.condition:
                self.watchers.discard(threading.current_thread())
                if entry.finished:
                    self.entries.pop((entry.operation_id, entry.attempt), None)
                self.condition.notify_all()

    # --- SDK tools through the fixed entry (native control) --------------------------

    def exchange(self, scope, body):
        if not isinstance(scope, dict) or scope.get('hostRef') != self.host_ref or scope.get('adapterId') != 'codex':
            raise RuntimeFault('identity_conflict', 'Use the original authenticated Codex control')
        if (not isinstance(body, dict) or set(body) != TOOL_FIELDS or body['kind'] != 'tool' or
                not all(isinstance(body[key], str) and 1 <= len(body[key]) <= 1024 for key in ('sessionId', 'turnId', 'callId', 'name')) or
                not isinstance(body['arguments'], dict)):
            raise RuntimeFault('invalid_input', 'Use the fixed Codex SDK tool envelope')
        with self.condition:
            if self.closing.is_set():
                raise RuntimeFault('closed', 'The Codex adapter is draining')
            candidates = [entry for entry in self.entries.values() if entry.sid == body['sessionId'] and
                          entry.actor == scope.get('actor') and not entry.finished and entry.execution is not None and
                          entry.request in (None, body['turnId'])]
            self.tool_calls += 1
        try:
            entry = self._tool_owner(candidates, body)
            return self._tool(entry, body)
        finally:
            with self.condition:
                self.tool_calls -= 1
                self.condition.notify_all()

    def _tool_owner(self, candidates, body):
        bound = [entry for entry in candidates if entry.request == body['turnId']]
        if len(bound) == 1:
            return bound[0]
        open_entries = [entry for entry in candidates if entry.request is None]
        if bound or not open_entries:
            raise RuntimeFault('not_ready', 'No queued IoA turn of this role is bound to this chat turn')
        capture = self._launch_or_fault()
        try:
            with self._server(capture) as server:
                turns, complete = server.turns(body['sessionId'], deadline=time.monotonic()+STEP_SECONDS)
                if not complete:
                    raise WireError('Incomplete desktop turn history')
        except (WireError, OSError, ValueError):
            raise RuntimeFault('not_ready', 'The persisted chat turn could not be read; the SDK tool was not called') from None
        owners = []
        for entry in open_entries:
            try:
                turn = native_facts.correlate_delegation(turns, entry.client, entry.record.get('beforeTurnIds', ()))
            except native_facts.IdentityConflict:
                continue
            if turn is not None and turn.get('id') == body['turnId']:
                owners.append(entry)
        if len(owners) != 1:
            raise RuntimeFault('identity_conflict', 'This tool call is not proven to belong to one queued IoA turn')
        try:
            self._bind_turn(owners[0], body['turnId'])
        except native_facts.IdentityConflict:
            raise RuntimeFault('identity_conflict', 'The queued IoA turn changed') from None
        return owners[0]

    def _launch_or_fault(self):
        try:
            return self._launch()
        except _Refused as refused:
            raise RuntimeFault(refused.code, refused.message, refused.action) from None

    def _tool(self, entry, body):
        tools = {tool.name: tool for tool in entry.execution.tools}
        tool = tools.get(body['name'])
        if tool is None:
            raise RuntimeFault('forbidden', 'Use only the original frozen SDK tools')
        arguments = body['arguments']
        fingerprint = hashlib.sha256(json.dumps({'name': body['name'], 'arguments': arguments}, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
        with entry.lock:
            old = entry.calls.get(body['callId'])
            if old is not None:
                if old[0] != fingerprint:
                    raise RuntimeFault('id_conflict', 'The original native tool arguments changed')
                event, owner = old[1], False
            else:
                event = {'done': threading.Event(), 'value': None}
                entry.calls[body['callId']] = (fingerprint, event)
                owner = True
        if owner:
            try:
                problem = next(Draft202012Validator(tool.input_schema).iter_errors(arguments), None)
                if problem is not None:
                    value = HostToolResult(error=SubmissionError('invalid_input', 'sdk_tool', 'not_submitted', problem.message))
                else:
                    value = entry.on_tool(HostToolCall(entry.operation_id, entry.attempt, entry.sid, entry.request,
                                                       body['callId'], tool.name, copy.deepcopy(arguments)))
                    if not isinstance(value, HostToolResult):
                        raise RuntimeFault('invalid_tool_output', 'Use the original SDK tool result')
                    if value.resources:
                        value = HostToolResult(error=SubmissionError('not_ready', 'resource_mapping', 'accepted',
                                               'SDK tool output resources need the P26 output mapper'))
                event['value'] = asdict(value)
            except Exception as exc:
                event['value'] = asdict(HostToolResult(error=SubmissionError(
                    'unknown_result', 'sdk_tool', 'unknown', 'Inspect the original SDK tool receipt: '+type(exc).__name__)))
            finally:
                event['done'].set()
        event['done'].wait()
        return {'toolResult': copy.deepcopy(event['value'])}

    # --- lifecycle ------------------------------------------------------------------

    def drain(self, timeout):
        """Stop new queueing and watchers; keep ownership while an SDK tool callback runs."""
        self.closing.set()
        with self.condition:
            self.condition.notify_all()
            return self.condition.wait_for(lambda: not self.watchers and not self.tool_calls, max(0, timeout))

    def close(self):
        self.closing.set()
        with self.condition:
            self.condition.notify_all()
            self.condition.wait_for(lambda: not self.watchers and not self.tool_calls)


def native_adapter(*, host_ref, resource_root, plugin_directory=None, resource_factory=None):
    root = Path(resource_root)
    if not root.is_absolute():
        raise RuntimeFault('invalid_input', 'Use the SDK-owned absolute resource directory')
    store = NativeLaunchStore(host_ref=host_ref, home=root/'launch')
    host = NativeCodexHost(host_ref=host_ref, root=root, launch_store=store)
    setup = NativeSetup(host_ref=host_ref, home=root/'launch', store=store, companion=host)
    def handler(scope, body):
        if isinstance(body, dict) and body.get('kind') == 'tool':
            return host.exchange(scope, body)
        return setup.exchange(scope, body)
    return {'host': host, 'native_handlers': {host_ref: handler}, 'native_resources': {host_ref: setup}}
