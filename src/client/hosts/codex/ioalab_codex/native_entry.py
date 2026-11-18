"""Fixed Codex role entry: hook handoff -> ensure -> SDK P10 source -> common caller.

The model runs ``<bound python> <installed entry> <operation> --args-file <file>``.
Only the PreToolUse hook appends ``--native-handoff <ref>``; the entry never
accepts a session, cwd, entryRef or contextRef from the command or arguments.
Without a valid one-time handoff a source operation is not submitted.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid

from .native_hook import ARGS_FLAG, HANDOFF_FLAG, ENTRY_FILES, TOOL_SUFFIX, Handoffs, entry_binding, state_root

_LIMIT = 128*1024
_TOOL_SECONDS = 900


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('native_endpoint_redirect')


class _Stop(Exception):
    def __init__(self, status, code, phase, message, recovery=None):
        super().__init__(message)
        self.status, self.code, self.phase, self.message = status, code, phase, message
        self.recovery = recovery


def _permission(phase):
    return _Stop('rejected', 'permission_denied', phase,
                 'The host denied access required by the fixed IoA entry before the SDK effect was submitted. '
                 'Request access through the host permission flow for this same fixed entry and original arguments; '
                 'keep the original files and do not reinstall or change permissions automatically.',
                 'request_native_entry_access')


def _stop_error(stop):
    error = {'code': stop.code, 'phase': stop.phase, 'dispatch': 'not_submitted', 'message': stop.message}
    if stop.recovery is not None:
        error['recovery'] = stop.recovery
    return error


def _parse(argv):
    if not argv or not isinstance(argv[0], str):
        raise _Stop('rejected', 'invalid_input', 'arguments', 'Use: <operation> --args-file <absolute file>')
    operation, rest, options = argv[0], list(argv[1:]), {}
    while rest:
        if len(rest) < 2 or rest[0] not in (ARGS_FLAG, HANDOFF_FLAG) or rest[0] in options:
            raise _Stop('rejected', 'invalid_input', 'arguments', 'Use only <operation> --args-file <absolute file>')
        options[rest[0]] = rest[1]; rest = rest[2:]
    return operation, options.get(ARGS_FLAG), options.get(HANDOFF_FLAG)


def _arguments(path):
    """Original arguments plus the persisted logical operationId (written back once)."""
    if path is None:
        return {}, str(uuid.uuid4())
    file = Path(path)
    try:
        value = json.loads(file.read_text(encoding='utf-8-sig'))
    except PermissionError:
        raise _permission('arguments') from None
    except (OSError, ValueError):
        raise _Stop('rejected', 'invalid_input', 'arguments', 'The arguments file must be a readable UTF-8 JSON object') from None
    if not isinstance(value, dict):
        raise _Stop('rejected', 'invalid_input', 'arguments', 'The arguments file must be a JSON object')
    operation_id = value.pop('operationId', None)
    if operation_id is None:
        operation_id = str(uuid.uuid4())
        # Persist before any dispatch so a retry reuses the same business identity.
        try:
            handle, temporary = tempfile.mkstemp(prefix='.ioalab-', dir=file.parent)
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                json.dump({**value, 'operationId': operation_id}, stream, ensure_ascii=False)
            os.replace(temporary, file)
        except PermissionError:
            raise _permission('arguments') from None
    elif not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
        raise _Stop('rejected', 'invalid_input', 'arguments', 'operationId must be the original 1-128 character identifier')
    return value, operation_id


def _run(command, body=None, timeout=None):
    response = subprocess.run(command, input=None if body is None else json.dumps(body, ensure_ascii=False),
                              capture_output=True, text=True, encoding='utf-8', shell=False, timeout=timeout)
    if len(response.stdout.encode('utf-8')) > _LIMIT:
        raise ValueError('native_receipt_too_large')
    return response.returncode, response.stdout


def _post(descriptor, body, *, control=False, timeout=15):
    url = descriptor['controlUrl'] if control else descriptor['url']; parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path != ('/v1/native-control' if control else '/v1/native-source') or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('native_endpoint_invalid')
    request = Request(url, data=json.dumps(body).encode(), method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer '+descriptor['grant'],
        'X-Ioa-Instance': descriptor['bootId']})
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        if response.headers.get('X-Ioa-Instance') != descriptor['bootId']:
            raise ValueError('native_instance_mismatch')
        encoded = response.read(_LIMIT+1)
    if len(encoded) > _LIMIT:
        raise ValueError('native_source_receipt_too_large')
    value = json.loads(encoded)
    if control:
        if value.get('status') != 'completed':
            raise ValueError('native_control_unverified')
        return value.get('data')
    reference = value.get('data', {}).get('entryRef') if value.get('status') == 'completed' else None
    if not isinstance(reference, str) or not reference:
        raise ValueError('native_source_unverified')
    return reference


def _claim(binding, role, root, invocation, reference):
    if reference is None:
        raise _Stop('rejected', 'not_ready', 'native_source',
                    'The Codex IoA hook did not bind this call to the current chat. Run exactly '
                    '<bound python> <installed entry> <operation> --args-file <absolute file> after the hook is installed, '
                    'trusted and reloaded; do not add identity arguments.')
    try:
        record = Handoffs(state_root(binding)).claim(reference)
    except PermissionError:
        raise _permission('native_handoff') from None
    except LookupError as error:
        reason = 'expired while waiting for host approval' if error.args == ('native_handoff_expired',) else 'missing or already used'
        raise _Stop('rejected', 'not_ready', 'native_source',
                    'The native handoff is '+reason+'. Execute the original fixed entry with the unchanged arguments '
                    'through the normal host approval flow so the real hook issues a fresh handoff. '
                    'Do not repeat external effects or a tool whose SDK result is unknown.', 'refresh_native_handoff') from None
    except ValueError:
        raise _Stop('rejected', 'not_ready', 'native_source',
                    'The native handoff is invalid. Execute the original fixed entry with unchanged arguments '
                    'so the real hook issues a fresh handoff; do not supply identity or handoff arguments.',
                    'refresh_native_handoff') from None
    expected = {'role': role, 'operation': invocation['operation'], 'argsFile': invocation['argsFile']}
    if ({key: record.get(key) for key in expected} != expected or
            not isinstance(record.get('entry'), str) or
            os.path.normcase(str(Path(record['entry']).resolve())) != os.path.normcase(str(root/ENTRY_FILES[role]))):
        raise _Stop('rejected', 'identity_conflict', 'native_source', 'The native handoff belongs to another entry call')
    return record


def _source(binding, profile, role, root, invocation, reference):
    """Claim the hook's own metadata, wake the bound client, then P10-register it."""
    record = _claim(binding, role, root, invocation, reference)
    try:
        code, stdout = _run(profile['ensureCommand'], timeout=180)
        ready = json.loads(stdout)
    except PermissionError:
        raise _permission('ensure_client') from None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise _Stop('waiting', 'not_ready', 'ensure_client', 'The bound client did not confirm startup; no business call was submitted') from None
    if code or not isinstance(ready, dict) or ready.get('status') != 'completed':
        raise _Stop('waiting', 'not_ready', 'ensure_client', 'Inspect the original client startup; no business call was submitted')
    phase = 'native_descriptor'
    try:
        # Fresh SDK capability after every ensure; never printed or put into arguments.
        descriptor = json.loads(Path(profile['descriptorFile']).read_text(encoding='utf-8'))
        if (descriptor.get('adapterId') != 'codex' or descriptor.get('hostRef') != binding['hostRef'] or
                descriptor.get('profile') != profile['profile'] or not descriptor.get('grant') or not descriptor.get('bootId')):
            raise ValueError('native_binding_mismatch')
        phase = 'native_source'
        context = {'entryRef': _post(descriptor, {'sessionId': record['sessionId'], 'workspace': record['workspace'],
                                                 'captureId': record['toolUseId']})}
    except PermissionError:
        raise _permission(phase) from None
    except Exception:
        raise _Stop('rejected', 'native_entry_unavailable', 'source', 'The SDK did not accept this native source; no business call was submitted') from None
    if invocation['operation'] != binding.get('setupOperation'):
        _refresh_desktop(descriptor, record['sessionId'])
    return context, record['workspace'], record['sessionId']


def _refresh_desktop(descriptor, session_id):
    from .desktop_context import capture_desktop
    from ioalab_sdk.runtime import RuntimeFault
    try:
        captured = capture_desktop(session_id)
        _post(descriptor, {'kind': 'entry_desktop', 'capture': captured}, control=True)
    except RuntimeFault as error:
        raise _Stop('rejected', error.code, 'desktop_context', str(error)) from None
    except PermissionError:
        raise _permission('desktop_context') from None
    except Exception:
        raise _Stop('rejected', 'not_ready', 'desktop_context',
                    'Refresh the IoA desktop connection through the fixed native setup entry; no business call was submitted') from None


def _tool(binding, profile, role, root, operation, args_file, reference):
    """Relay one SDK tool of the queued route A turn to the running adapter; no ensure, no caller."""
    if args_file is None:
        raise _Stop('rejected', 'invalid_input', 'arguments', 'SDK tools require --args-file with {"name", "arguments"}')
    try:
        value = json.loads(Path(args_file).read_text(encoding='utf-8-sig'))
    except PermissionError:
        raise _permission('arguments') from None
    except (OSError, ValueError):
        raise _Stop('rejected', 'invalid_input', 'arguments', 'The arguments file must be a readable UTF-8 JSON object') from None
    if (not isinstance(value, dict) or set(value) != {'name', 'arguments'} or not isinstance(value['name'], str) or
            not value['name'] or not isinstance(value['arguments'], dict)):
        raise _Stop('rejected', 'invalid_input', 'arguments', 'Use exactly {"name": <declared SDK tool>, "arguments": {...}}')
    record = _claim(binding, role, root, {'operation': operation, 'argsFile': args_file}, reference)
    try:
        descriptor = json.loads(Path(profile['descriptorFile']).read_text(encoding='utf-8'))
        if (descriptor.get('adapterId') != 'codex' or descriptor.get('hostRef') != binding['hostRef'] or
                descriptor.get('profile') != profile['profile'] or not descriptor.get('grant') or not descriptor.get('bootId')):
            raise ValueError('native_binding_mismatch')
    except PermissionError:
        raise _permission('native_descriptor') from None
    except (OSError, ValueError):
        raise _Stop('rejected', 'not_ready', 'sdk_tool', 'The IoA client of this queued turn is not running; the SDK tool was not called') from None
    body = {'kind': 'tool', 'sessionId': record['sessionId'], 'turnId': record['turnId'], 'callId': record['toolUseId'],
            'name': value['name'], 'arguments': value['arguments']}
    try:
        data = _post(descriptor, body, control=True, timeout=_TOOL_SECONDS)
    except HTTPError as error:
        try:
            detail = json.loads(error.read(_LIMIT)).get('error') or {}
        except (OSError, ValueError, AttributeError):
            detail = {}
        code = detail.get('code') if isinstance(detail.get('code'), str) else 'native_entry_unavailable'
        raise _Stop('rejected', code, 'sdk_tool', 'The SDK tool was not called: '+str(detail.get('message') or 'refused')) from None
    except Exception:
        return 1, {'status': 'unknown', 'data': None, 'error': {'code': 'unknown_result', 'phase': 'sdk_tool', 'dispatch': 'unknown',
                   'message': 'The SDK tool result is unknown; do not repeat it'}}
    result = data.get('toolResult') if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return 1, {'status': 'unknown', 'data': None, 'error': {'code': 'unknown_result', 'phase': 'sdk_tool', 'dispatch': 'unknown',
                   'message': 'The SDK tool result is unknown; do not repeat it'}}
    error = result.get('error')
    status = 'completed' if not error else 'unknown' if error.get('dispatch') == 'unknown' else 'rejected'
    return (1 if error else 0), {'status': status, 'data': result.get('value'), 'error': error}


def invoke(root, role, argv):
    root = Path(root).resolve()
    operation_id = None
    try:
        operation, args_file, reference = _parse(argv)
        binding, profile = entry_binding(root/ENTRY_FILES[role], role)
        if operation == role+TOOL_SUFFIX:
            return _tool(binding, profile, role, root, operation, args_file, reference)
        source = operation in binding['sourceOperations']
        setup = operation == binding.get('setupOperation')
        if (source or setup) and args_file is None:
            raise _Stop('rejected', 'invalid_input', 'arguments', 'Business operations require --args-file with the original operationId')
        if not source and not setup and reference is not None:
            raise _Stop('rejected', 'invalid_input', 'arguments', 'Control operations do not take a native handoff')
        arguments, operation_id = _arguments(args_file)
        if setup and arguments:
            raise _Stop('rejected', 'invalid_input', 'native_setup', 'Explicit setup takes no business arguments')
        context, workspace, session_id = _source(binding, profile, role, root, {'operation': operation, 'argsFile': args_file}, reference) if source or setup else (None, None, None)
    except _Stop as stop:
        return 0 if stop.status == 'waiting' else 1, {'operationId': operation_id, 'status': stop.status, 'data': None,
            'error': _stop_error(stop)}
    except PermissionError:
        return 1, {'operationId': operation_id, 'status': 'rejected', 'data': None,
                   'error': _stop_error(_permission('native_binding'))}
    except (OSError, ValueError, KeyError, TypeError):
        return 1, {'operationId': operation_id, 'status': 'rejected', 'data': None, 'error': {
            'code': 'native_entry_unavailable', 'phase': 'binding', 'dispatch': 'not_submitted',
            'message': 'Repair the installed Codex native entry binding'}}
    try:
        code, stdout = _run(profile['callerCommand'], {'operation': operation, 'operationId': operation_id,
                                                      'arguments': arguments, 'contextRef': context})
        result = json.loads(stdout)
        if not isinstance(result, dict) or result.get('operationId') != operation_id:
            raise ValueError('caller_receipt_mismatch')
        if setup and result.get('status') == 'waiting':
            descriptor = json.loads(Path(profile['descriptorFile']).read_text(encoding='utf-8'))
            # Recover before any recapture; an unknown claim never selects a new project.
            restored = _post(descriptor, {'kind': 'entry_recover'}, control=True)
            if restored is None and result.get('data', {}).get('phase') == 'intent':
                from .native_setup import capture
                # Original source workspace is captured by the trusted hook, not model arguments.
                # SDK source lookup is deliberately not exposed as a public identity method.
                actual = capture(workspace, root)
                _post(descriptor, {'kind': 'entry_setup', 'entryRef': context['entryRef'],
                                   'capture': actual, 'workspace': workspace}, control=True)
            code, stdout = _run(profile['callerCommand'], {'operation': operation, 'operationId': operation_id,
                                                          'arguments': {}, 'contextRef': context})
            result = json.loads(stdout)
            if not isinstance(result, dict) or result.get('operationId') != operation_id:
                raise ValueError('caller_receipt_mismatch')
        if setup and result.get('status') == 'completed':
            descriptor = json.loads(Path(profile['descriptorFile']).read_text(encoding='utf-8'))
            try:
                _refresh_desktop(descriptor, session_id)
            except _Stop as stop:
                return 1, {'operationId': operation_id, 'status': stop.status, 'data': None,
                           'error': _stop_error(stop)}
        return code, result
    except Exception:
        # A lost common-caller receipt never proves non-execution; keep the original operation.
        return 1, {'operationId': operation_id, 'status': 'unknown', 'data': None, 'error': {
            'code': 'unknown_result', 'phase': 'caller', 'dispatch': 'unknown',
            'message': 'Keep the arguments file and query the original operation; do not resubmit'}}


def main(root, role, argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    code, result = invoke(root, role, sys.argv[1:] if argv is None else argv)
    print(json.dumps(result, ensure_ascii=False))
    return code
