"""D31 Codex fixed-entry hook: PreToolUse stdin/stdout seam, entry->P10->caller seam,
installer and P07 export. Fake client/caller processes and a fake local P10 listener
are system-boundary doubles; no Codex process, model or user config is touched."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

import pytest

from ioalab_codex import native_skills
from ioalab_codex.native_entry import invoke
from ioalab_codex.native_hook import respond, split_command
from ioalab_codex.native_install import install, uninstall, windows_command_line, STATUS_MESSAGE

SOURCE_OPS = ['assistant.delegation.flow', 'provider.flow']
CALLER = r'''import json,sys,pathlib
packet=json.loads(sys.stdin.read())
pathlib.Path(sys.argv[1]).open('a',encoding='utf-8').write(json.dumps(packet)+'\n')
print(json.dumps({'operationId':packet['operationId'],'status':'completed','data':{'echo':packet['operation']},'error':None}))
'''
ENSURE = r'''import json,sys,pathlib
pathlib.Path(sys.argv[1]).open('a',encoding='utf-8').write('ensure\n')
print(json.dumps({'status':sys.argv[2]}))
'''


class Installed:
    """Installed client layout: two skill bundles exported through P07 and one hook config."""

    def __init__(self, root, *, ensure='completed'):
        self.root = Path(root)
        self.home = self.root/'client-data'
        self.calls = self.root/'caller.jsonl'
        self.ensures = self.root/'ensure.log'
        scripts = self.root/'fake-bin'; scripts.mkdir(parents=True)
        (scripts/'caller.py').write_text(CALLER, encoding='utf-8')
        (scripts/'ensure.py').write_text(ENSURE, encoding='utf-8')
        self.python = sys.executable
        descriptors = self.home/'roles'/'.native-sources'
        profiles = {slot: {'profile': slot+'-key', 'descriptorFile': str(descriptors/(slot+'.json')),
                           'ensureCommand': [self.python, str(scripts/'ensure.py'), str(self.ensures), ensure],
                           'callerCommand': [self.python, str(scripts/'caller.py'), str(self.calls)]}
                    for slot in ('assistant', 'provider')}
        self.binding = {'format': 'ioalab.native-entry/1', 'hostRef': 'codex', 'profiles': profiles,
                        'sourceOperations': SOURCE_OPS, 'setupOperation': 'runtime.entry.bind'}
        self.entries = {}
        for skill, role in (('ioalab-delegate', 'assistant'), ('ioalab-provider', 'provider')):
            native = self.root/'skills'/skill/'native'
            for name, data in native_skills.resource_files(self.binding).items():
                (native/name).parent.mkdir(parents=True, exist_ok=True); (native/name).write_bytes(data)
            self.entries[role] = str(native/('entry-'+role+'.py'))
        self.codex_home = self.root/'codex-home'
        self.receipt = install(self.entries, home=self.codex_home)
        self.config = self.receipt['hookConfig']
        self.handoffs = Path(self.config).parent/'handoffs'

    def command(self, role='assistant', operation='assistant.delegation.flow', args=None, prefix='& '):
        args = args or str(self.root/'work'/'args.json')
        return f'{prefix}"{self.python}" "{self.entries[role]}" {operation} --args-file "{args}"'

    def payload(self, command, **changes):
        value = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': command},
                 'session_id': 'real-native-sid', 'turn_id': 'real-turn', 'tool_use_id': 'call-1',
                 'cwd': str(self.root/'work'), 'model': 'm', 'permission_mode': 'default', 'transcript_path': None}
        value.update(changes)
        return value

    def pending(self):
        return sorted(self.handoffs.glob('*')) if self.handoffs.exists() else []


@pytest.fixture
def installed(tmp_path, monkeypatch):
    value = Installed(tmp_path)
    workspace = tmp_path/'work'; workspace.mkdir(exist_ok=True)
    monkeypatch.chdir(workspace)
    return value


@contextmanager
def p10(installed, *, boot='boot-1', grant='grant-secret'):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append({'path': self.path, 'auth': self.headers['Authorization'],
                         'instance': self.headers['X-Ioa-Instance'], 'body': body})
            data = json.dumps({'status': 'completed', 'data': {'entryRef': 'entry-ref-1'}}).encode()
            self.send_response(200); self.send_header('X-Ioa-Instance', boot)
            self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    for slot, profile in installed.binding['profiles'].items():
        path = Path(profile['descriptorFile']); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'url': url+'/v1/native-source', 'controlUrl': url+'/v1/native-control',
            'bootId': boot, 'grant': grant, 'hostRef': 'codex', 'adapterId': 'codex', 'profile': profile['profile']}))
    try:
        yield seen
    finally:
        server.shutdown(); server.server_close()


def args_file(installed, value):
    path = installed.root/'work'/'args.json'; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def run_entry(installed, command):
    """What Codex executes after the hook: the rewritten tokens minus interpreter/script."""
    tokens = split_command(command)
    role = 'assistant' if tokens[1] == installed.entries['assistant'] else 'provider'
    return invoke(Path(tokens[1]).parent, role, tokens[2:])


# --- PreToolUse seam -------------------------------------------------------------------

@pytest.mark.parametrize('prefix', ['& ', ''])
def test_exact_bound_entry_is_allowed_with_one_time_handoff_appended(installed, prefix):
    command = installed.command(prefix=prefix)
    output = respond(installed.payload(command), installed.config)
    rewritten = output['hookSpecificOutput']['updatedInput']['command']
    assert output == {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',
                                             'updatedInput': {'command': rewritten}}}
    assert re.fullmatch(re.escape(command)+r' --native-handoff [0-9a-f]{64}', rewritten)
    assert len(installed.pending()) == 1
    stored = installed.pending()[0].read_text(encoding='utf-8')
    assert 'real-native-sid' in stored and rewritten.rsplit(' ', 1)[1] not in stored


def test_single_quoted_and_provider_entry_match(installed):
    command = f"& '{installed.python}' '{installed.entries['provider']}' provider.flow --args-file '{installed.root}\\a.json'"
    assert respond(installed.payload(command), installed.config)['hookSpecificOutput']['permissionDecision'] == 'allow'


def _variants(i):
    py, entry, op = i.python, i.entries['assistant'], 'assistant.delegation.flow'
    args = str(i.root/'work'/'args.json')
    base = f'& "{py}" "{entry}" {op} --args-file "{args}"'
    other_entry = str(Path(entry).parent.parent.parent/'ioalab-provider'/'native'/'entry-assistant.py')
    return {
        'extra argument': base+' extra',
        'extra flag': base+' --config "C:\\x.json"',
        'already has handoff': base+' --native-handoff '+'a'*64,
        'semicolon': base+'; calc',
        'and-and': base+' && calc',
        'pipe': base+' | more',
        'redirect': base+' > out.txt',
        'subexpression': f'& "{py}" "{entry}" $(calc) --args-file "{args}"',
        'backtick': f'& "{py}" "{entry}" {op}`n --args-file "{args}"',
        'cmd variable': f'& "{py}" "{entry}" {op} --args-file "%TEMP%\\a.json"',
        'ps variable': f'& "{py}" "{entry}" {op} --args-file "$env:TEMP\\a.json"',
        'caret': base.replace('--args-file', '^--args-file'),
        'bang': base.replace('args.json', 'args!.json'),
        'newline': base+'\ncalc',
        'tab separator': base.replace(' --args-file', '\t--args-file'),
        'double space': base.replace(' --args-file', '  --args-file'),
        'leading space': ' '+base,
        'trailing space': base+' ',
        'smart quotes': base.replace(f'"{entry}"', f'\u201c{entry}\u201d'),
        'case-changed python': base.replace(py, py.upper() if py != py.upper() else py.lower()),
        'slash python': base.replace(py, py.replace('\\', '/')) if '\\' in py else base.replace(py, py+'w'),
        'pythonw': base.replace(py, py[:-4]+'w.exe' if py.endswith('.exe') else py+'w'),
        'relative python': f'& "python" "{entry}" {op} --args-file "{args}"',
        'entry backup name': base.replace(entry, entry+'.bak'),
        'entry in other dir': base.replace(entry, other_entry),
        'entry traversal': base.replace(entry, str(Path(entry).parent/'..'/'native'/'entry-assistant.py')),
        'assistant operation on provider entry': base.replace(entry, i.entries['provider']),
        'control operation': base.replace(op, 'runtime.status'),
        'setup operation': f'& "{py}" "{entry}" runtime.entry.bind',
        'invalid operation': base.replace(op, 'Assistant.Delegation'),
        'no args file': f'& "{py}" "{entry}" {op}',
        'relative args file': base.replace(args, 'args.json'),
        'unc args file': base.replace(args, '\\\\server\\share\\a.json'),
        'dotdot args file': base.replace(args, str(i.root/'work'/'..'/'args.json')),
        'unterminated quote': base[:-1],
        'glued suffix': base+'x',
        'glued tokens': base.replace(f'" "{entry}"', f'""{entry}"'),
        'call operator only': '& ',
        'empty': '',
    }


@pytest.mark.parametrize('name', [
    'extra argument', 'extra flag', 'already has handoff', 'semicolon', 'and-and', 'pipe', 'redirect',
    'subexpression', 'backtick', 'cmd variable', 'ps variable', 'caret', 'bang', 'newline', 'tab separator',
    'double space', 'leading space', 'trailing space', 'smart quotes', 'case-changed python', 'slash python',
    'pythonw', 'relative python', 'entry backup name', 'entry in other dir', 'entry traversal',
    'assistant operation on provider entry', 'control operation', 'setup operation', 'invalid operation', 'no args file',
    'relative args file', 'unc args file', 'dotdot args file', 'unterminated quote', 'glued suffix', 'glued tokens', 'call operator only', 'empty'])
def test_expansions_and_similar_commands_keep_original_input_and_decision(installed, name):
    command = _variants(installed)[name]
    assert respond(installed.payload(command), installed.config) is None
    assert installed.pending() == []


@pytest.mark.parametrize('changes', [
    {'tool_name': 'apply_patch'}, {'hook_event_name': 'PermissionRequest'},
    {'tool_input': 'raw text'}, {'session_id': ''}, {'session_id': None}, {'turn_id': None},
    {'tool_use_id': None}, {'cwd': 'relative\\dir'}])
def test_missing_native_metadata_or_other_tools_never_allow(installed, changes):
    assert respond(installed.payload(installed.command(), **changes), installed.config) is None
    assert installed.pending() == []


def test_extra_tool_input_field_is_not_rewritten(installed):
    payload = installed.payload(installed.command())
    payload['tool_input']['workdir'] = 'C:\\'
    assert respond(payload, installed.config) is None


def test_model_tool_workdir_does_not_select_native_project(installed):
    output = respond(installed.payload(installed.command(), cwd=str(installed.root/'model-workdir')), installed.config)
    reference = output['hookSpecificOutput']['updatedInput']['command'].rsplit(' ', 1)[1]
    from ioalab_codex.native_hook import Handoffs
    record = Handoffs(Path(installed.config).parent).claim(reference)
    assert record['workspace'] == str(installed.root/'work')


def test_hook_process_outputs_nothing_and_exits_zero_when_not_matching_or_broken(installed):
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
    def hook(stdin, config):
        return subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c',
            'import sys;sys.path[:0]=' + repr(sys.path) + ';from ioalab_codex.native_hook import main;raise SystemExit(main())',
            str(config)], input=stdin, capture_output=True, text=True, env=env, timeout=60)
    allowed = hook(json.dumps(installed.payload(installed.command())), installed.config)
    assert allowed.returncode == 0 and json.loads(allowed.stdout)['hookSpecificOutput']['permissionDecision'] == 'allow'
    for stdin, config in ((json.dumps(installed.payload('Get-ChildItem')), installed.config), ('not json', installed.config),
                          (json.dumps(installed.payload(installed.command())), installed.root/'missing.json')):
        result = hook(stdin, config)
        assert (result.returncode, result.stdout) == (0, '')


def test_hook_run_breadcrumb_shows_stage_and_shape_without_command_text(installed, monkeypatch):
    import io
    from ioalab_codex import native_hook
    log = Path(installed.config).parent/'audit.jsonl'
    def run(stdin):
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(stdin.encode('utf-8'))))
        assert native_hook.main([str(installed.config)]) == 0
        return json.loads(log.read_text(encoding='utf-8').splitlines()[-1])['hookRun']
    secret = 'Get-ChildItem C:\\private-dir'
    plain = run(json.dumps(installed.payload(secret)))
    assert (plain['stage'], plain['event'], plain['tool'], plain['inputKeys']) == ('no_opinion', 'PreToolUse', 'Bash', ['command'])
    assert run(json.dumps(installed.payload(installed.command())))['stage'] == 'allow'
    broken = run('not json')
    assert (broken['stage'], broken['error']) == ('parse', 'JSONDecodeError')
    assert 'private-dir' not in log.read_text(encoding='utf-8')


def test_audit_records_hits_and_near_misses_without_command_text(installed):
    respond(installed.payload(installed.command()), installed.config)
    respond(installed.payload(installed.command()+' extra'), installed.config)
    respond(installed.payload('Get-ChildItem C:\\secret-dir'), installed.config)
    lines = [json.loads(line) for line in (Path(installed.config).parent/'audit.jsonl').read_text().splitlines()]
    assert [(line['decision'], line.get('reason')) for line in lines] == [('allow', None), ('unchanged', 'not_exact_entry')]
    assert 'secret-dir' not in json.dumps(lines) and 'args.json' not in json.dumps(lines)


# --- entry -> P10 -> common caller seam ------------------------------------------------

def test_hook_handoff_registers_real_native_source_then_calls_common_caller(installed):
    args_file(installed, {'goal': 'x'})
    rewritten = respond(installed.payload(installed.command()), installed.config)['hookSpecificOutput']['updatedInput']['command']
    with p10(installed) as seen:
        code, result = run_entry(installed, rewritten)
    assert (code, result['status']) == (0, 'completed')
    assert seen == [{'path': '/v1/native-source', 'auth': 'Bearer grant-secret', 'instance': 'boot-1',
                     'body': {'sessionId': 'real-native-sid', 'workspace': str(installed.root/'work'), 'captureId': 'call-1'}}]
    packet = json.loads(installed.calls.read_text().splitlines()[0])
    saved = json.loads((installed.root/'work'/'args.json').read_text(encoding='utf-8'))
    assert packet == {'operation': 'assistant.delegation.flow', 'operationId': saved['operationId'],
                      'arguments': {'goal': 'x'}, 'contextRef': {'entryRef': 'entry-ref-1'}}
    assert installed.pending() == [] and 'grant-secret' not in json.dumps(result)


def test_handoff_is_single_use_and_cannot_be_moved_to_another_operation(installed):
    args_file(installed, {'operationId': 'op-1'})
    rewritten = respond(installed.payload(installed.command()), installed.config)['hookSpecificOutput']['updatedInput']['command']
    reference = rewritten.rsplit(' ', 1)[1]
    other = installed.command(role='provider', operation='provider.flow')+' --native-handoff '+reference
    with p10(installed) as seen:
        moved = run_entry(installed, other)
        replay = run_entry(installed, rewritten)
    assert moved[1]['error']['code'] == 'identity_conflict' and moved[1]['error']['dispatch'] == 'not_submitted'
    assert replay[1]['error']['phase'] == 'native_source' and replay[1]['operationId'] == 'op-1'
    assert seen == [] and not installed.calls.exists()


def test_source_operation_without_hook_is_not_submitted_and_does_not_wake_client(installed):
    args_file(installed, {'operationId': 'op-2'})
    with p10(installed) as seen:
        code, result = run_entry(installed, installed.command())
    assert code == 1 and result['status'] == 'rejected'
    assert result['error'] == {'code': 'not_ready', 'phase': 'native_source', 'dispatch': 'not_submitted',
                               'message': result['error']['message']}
    assert seen == [] and not installed.calls.exists() and not installed.ensures.exists()


def test_expired_handoff_is_not_used(installed):
    args_file(installed, {'operationId': 'op-3'})
    rewritten = respond(installed.payload(installed.command()), installed.config, clock=lambda: 0)['hookSpecificOutput']['updatedInput']['command']
    with p10(installed) as seen:
        result = run_entry(installed, rewritten)[1]
    assert result['error']['phase'] == 'native_source' and seen == []


def test_client_not_ready_keeps_business_unsubmitted(tmp_path):
    installed = Installed(tmp_path, ensure='waiting')
    args_file(installed, {'operationId': 'op-4'})
    rewritten = respond(installed.payload(installed.command()), installed.config)['hookSpecificOutput']['updatedInput']['command']
    with p10(installed) as seen:
        code, result = run_entry(installed, rewritten)
    assert (result['status'], result['error']['phase'], result['error']['dispatch']) == ('waiting', 'ensure_client', 'not_submitted')
    assert seen == [] and not installed.calls.exists()


def test_descriptor_of_another_adapter_is_not_registered(installed):
    args_file(installed, {'operationId': 'op-5'})
    rewritten = respond(installed.payload(installed.command()), installed.config)['hookSpecificOutput']['updatedInput']['command']
    with p10(installed) as seen:
        path = Path(installed.binding['profiles']['assistant']['descriptorFile'])
        path.write_text(json.dumps({**json.loads(path.read_text()), 'adapterId': 'claude_code'}))
        result = run_entry(installed, rewritten)[1]
    assert result['error']['dispatch'] == 'not_submitted' and seen == [] and not installed.calls.exists()


def test_control_operation_goes_to_same_caller_without_source(installed):
    code, result = invoke(Path(installed.entries['assistant']).parent, 'assistant', ['runtime.status'])
    packet = json.loads(installed.calls.read_text().splitlines()[0])
    assert result['status'] == 'completed' and packet['contextRef'] is None and not installed.ensures.exists()


def test_lost_caller_receipt_is_unknown_not_retried(installed, monkeypatch):
    args_file(installed, {'operationId': 'op-6'})
    broken = installed.root/'fake-bin'/'caller.py'
    broken.write_text('import sys;sys.stdin.read();print("partial")', encoding='utf-8')
    rewritten = respond(installed.payload(installed.command()), installed.config)['hookSpecificOutput']['updatedInput']['command']
    with p10(installed):
        code, result = run_entry(installed, rewritten)
    assert (code, result['status'], result['operationId'], result['error']['dispatch']) == (1, 'unknown', 'op-6', 'unknown')


def test_setup_and_identity_arguments_are_not_accepted_by_entry(installed):
    root = Path(installed.entries['assistant']).parent
    assert invoke(root, 'assistant', ['runtime.entry.bind'])[1]['error']['phase'] == 'arguments'
    for argv in (['assistant.delegation.flow', '--args-file', 'a', '--session-id', 'x'],
                 ['assistant.delegation.flow', '--args-file', 'a', '--args-file', 'b']):
        assert invoke(root, 'assistant', argv)[1]['error']['code'] == 'invalid_input'
    assert not installed.calls.exists()


def test_fixed_setup_requires_hook_and_empty_business_arguments(installed):
    args_file(installed, {'operationId': 'setup-original'})
    command = installed.command(operation='runtime.entry.bind')
    output = respond(installed.payload(command), installed.config)
    assert output['hookSpecificOutput']['permissionDecision'] == 'allow'
    # No hook handoff never wakes the client even for setup.
    result = run_entry(installed, command)[1]
    assert result['error']['dispatch'] == 'not_submitted' and not installed.ensures.exists()
    args_file(installed, {'operationId': 'setup-original', 'cwd': 'model-supplied'})
    result = run_entry(installed, output['hookSpecificOutput']['updatedInput']['command'])[1]
    assert result['error']['phase'] == 'native_setup' and not installed.calls.exists()


# --- installer and P07 export ------------------------------------------------------------

def test_install_creates_only_our_group_and_uninstall_restores(tmp_path):
    installed = Installed(tmp_path)
    hooks = installed.codex_home/'hooks.json'
    document = json.loads(hooks.read_text(encoding='utf-8'))
    group, = document['hooks']['PreToolUse']
    assert group['matcher'] == 'Bash' and group['hooks'][0]['statusMessage'] == STATUS_MESSAGE
    assert installed.receipt['nativeTrustRequired'] and not installed.receipt['nativeLoaded']
    assert install(installed.entries, home=installed.codex_home)['changed'] is False
    assert not (installed.codex_home/'config.toml').exists()
    assert uninstall(installed.entries, home=installed.codex_home)['removed']
    assert not hooks.exists() and not Path(installed.config).exists()


def test_install_preserves_existing_user_hooks_with_backup(tmp_path):
    home = tmp_path/'codex-home'; home.mkdir()
    user = {'hooks': {'PreToolUse': [{'matcher': 'apply_patch', 'hooks': [{'type': 'command', 'command': 'user-hook'}]}],
                      'Stop': [{'hooks': [{'type': 'command', 'command': 'stop-hook'}]}]}}
    original = json.dumps(user).encode()
    (home/'hooks.json').write_bytes(original)
    installed = Installed(tmp_path)  # installs into the same CODEX_HOME
    receipt = installed.receipt
    after = json.loads((home/'hooks.json').read_text(encoding='utf-8'))
    assert after['hooks']['Stop'] == user['hooks']['Stop'] and after['hooks']['PreToolUse'][0] == user['hooks']['PreToolUse'][0]
    assert Path(receipt['backup']).read_bytes() == original
    uninstall(installed.entries, home=home)
    assert json.loads((home/'hooks.json').read_text(encoding='utf-8')) == user


def test_reinstall_replaces_older_rendering_of_same_binding_only(tmp_path):
    installed = Installed(tmp_path)
    hooks = installed.codex_home/'hooks.json'
    document = json.loads(hooks.read_text(encoding='utf-8'))
    ours = document['hooks']['PreToolUse'][0]
    current = ours['hooks'][0]['command']
    user = {'matcher': 'apply_patch', 'hooks': [{'type': 'command', 'command': 'user-hook'}]}
    ours['hooks'][0]['command'] = f'"{installed.python}" -m ioalab_codex.native_hook "{installed.config}"'  # older rendering
    document['hooks']['PreToolUse'].append(user)
    hooks.write_text(json.dumps(document), encoding='utf-8')
    older = hooks.read_bytes()
    receipt = install(installed.entries, home=installed.codex_home)
    assert receipt['changed'] and receipt['replaced'] and Path(receipt['backup']).read_bytes() == older
    after = json.loads(hooks.read_text(encoding='utf-8'))['hooks']['PreToolUse']
    assert after[0]['hooks'][0]['command'] == current and after[1] == user
    foreign = json.loads(json.dumps(after[0])); foreign['hooks'][0]['command'] = current.replace('codex-hook', 'other-hook')
    hooks.write_text(json.dumps({'hooks': {'PreToolUse': [foreign, user]}}), encoding='utf-8')
    with pytest.raises(ValueError):
        install(installed.entries, home=installed.codex_home)
    assert not uninstall(installed.entries, home=installed.codex_home)['removed']


def test_resource_export_has_entries_binding_and_no_grant(installed):
    from ioalab_sdk.runtime import RuntimeFault
    files = native_skills.resource_files(installed.binding)
    assert set(files) == {'entry-assistant.py', 'entry-provider.py', 'README.md', 'entry-binding.json'}
    assert installed.python.encode() in files['README.md']
    with pytest.raises(RuntimeFault):
        native_skills.resource_files({**installed.binding, 'grant': 'must not export'})


MARKER = r'''import json,sys,pathlib
pathlib.Path(sys.argv[1]).write_text(json.dumps({'argv':sys.argv[2:],'stdin':sys.stdin.read()}),encoding='utf-8')
'''


@pytest.mark.skipif(os.name != 'nt', reason='Windows hook shells')
@pytest.mark.parametrize('shell', ['cmd', 'powershell'])
def test_windows_hook_line_runs_spaced_paths_identically_in_cmd_and_powershell(tmp_path, shell):
    # Codex may launch the hook line through cmd.exe /C or the session PowerShell -Command.
    folder = tmp_path/'dir with space 中文'; folder.mkdir()
    script = folder/'marker script.py'; script.write_text(MARKER, encoding='utf-8')
    record = folder/'out file.json'
    # The bound interpreter itself may live under a profile with spaces: copy the venv launcher there.
    source = Path(sys.executable)
    if not (source.parent.parent/'pyvenv.cfg').is_file():
        pytest.skip('needs a venv launcher to place the interpreter under a spaced path')
    python = folder/'venv dir'/source.parent.name/source.name
    python.parent.mkdir(parents=True)
    shutil.copy2(source, python); shutil.copy2(source.parent.parent/'pyvenv.cfg', python.parent.parent/'pyvenv.cfg')
    line = windows_command_line([python, '-I', str(script), str(record), 'plain', str(folder/'hook config.json')])
    args = (os.environ['COMSPEC']+' /C "'+line+'"') if shell == 'cmd' else ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', line]
    result = subprocess.run(args, input=b'{"payload":1}', capture_output=True, timeout=60, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(record.read_text(encoding='utf-8')) == {'argv': ['plain', str(folder/'hook config.json')], 'stdin': '{"payload":1}'}


@pytest.mark.parametrize('bad', [r'C:\a&calc\python.exe', r'C:\%TEMP%\python.exe', r'C:\a"b\python.exe',
                                 r'C:\a|b\python.exe', r'C:\a^b\python.exe', r'C:\a!b\python.exe', 'C:\a\nb'])
def test_windows_hook_line_refuses_shell_interpreted_characters(bad):
    with pytest.raises(ValueError):
        windows_command_line([bad, '-I'], system_root=r'C:\Windows')


# --- Route A SDK tool relay through the same fixed entry ---------------------------------

@contextmanager
def control(installed, reply, *, status=200, boot='boot-1'):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append({'path': self.path, 'body': body})
            data = json.dumps(reply).encode()
            self.send_response(status); self.send_header('X-Ioa-Instance', boot)
            self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    for profile in installed.binding['profiles'].values():
        path = Path(profile['descriptorFile']); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'url': url+'/v1/native-source', 'controlUrl': url+'/v1/native-control',
            'bootId': boot, 'grant': 'g', 'hostRef': 'codex', 'adapterId': 'codex', 'profile': profile['profile']}))
    try:
        yield seen
    finally:
        server.shutdown(); server.server_close()


def test_tool_relay_binds_actual_turn_and_call_without_ensure_or_caller(installed):
    args = args_file(installed, {'name': 'ioa_reply', 'arguments': {'text': 'hi'}})
    command = installed.command(operation='assistant.native.tool', args=str(args))
    output = respond(installed.payload(command), installed.config)
    rewritten = output['hookSpecificOutput']['updatedInput']['command']
    reply = {'status': 'completed', 'data': {'toolResult': {'value': {'status': 'submitted'}, 'resources': [], 'error': None}}}
    with control(installed, reply) as seen:
        code, result = run_entry(installed, rewritten)
    assert code == 0 and result == {'status': 'completed', 'data': {'status': 'submitted'}, 'error': None}
    assert seen == [{'path': '/v1/native-control', 'body': {'kind': 'tool', 'sessionId': 'real-native-sid', 'turnId': 'real-turn',
                                                             'callId': 'call-1', 'name': 'ioa_reply', 'arguments': {'text': 'hi'}}}]
    assert not installed.ensures.exists() and not installed.calls.exists()
    assert json.loads(args.read_text()) == {'name': 'ioa_reply', 'arguments': {'text': 'hi'}}  # no operationId written


def test_tool_relay_requires_hook_and_role_and_reports_refusal(installed):
    args = args_file(installed, {'name': 'ioa_reply', 'arguments': {}})
    with control(installed, {}) as seen:
        code, result = run_entry(installed, installed.command(operation='assistant.native.tool', args=str(args)))
    assert code == 1 and result['error']['phase'] == 'native_source' and seen == []
    # The provider tool operation never matches the assistant entry.
    command = installed.command(operation='provider.native.tool', args=str(args))
    assert respond(installed.payload(command), installed.config) is None
    rewritten = respond(installed.payload(installed.command(operation='assistant.native.tool', args=str(args))),
                        installed.config)['hookSpecificOutput']['updatedInput']['command']
    refusal = {'status': 'rejected', 'data': None, 'error': {'code': 'identity_conflict', 'message': 'not this turn'}}
    with control(installed, refusal, status=400):
        code, result = run_entry(installed, rewritten)
    assert code == 1 and result['status'] == 'rejected'
    assert result['error']['code'] == 'identity_conflict' and result['error']['dispatch'] == 'not_submitted'
