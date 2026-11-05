"""Native ownership and shutdown ordering, with no business model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from deploy.windows_local import Local, WindowsProcesses


@pytest.mark.parametrize('root_reused',[False,True])
def test_job_owned_pid_generations_keep_history_and_original_root(tmp_path,root_reused):
    api=object.__new__(WindowsProcesses)
    root={'pid':1,'created':100,'executable':'python.exe','role':'core','job':'owned-job'}
    old={'pid':2,'created':101,'executable':'python.exe','role':'core','parent':1}
    pid=1 if root_reused else 2
    current={1:{key:root[key] for key in ('pid','created','executable')},
             2:{key:old[key] for key in ('pid','created','executable')}}
    current[pid]={'pid':pid,'created':200,'executable':'python.exe'}
    api.members=lambda job:[1,2]
    api.identity=lambda pid,job=None:current[pid]
    rows=api.descendants([root,old])
    assert len(rows)==3 and root in rows and old in rows
    assert next(row for row in rows if row['created']==100)['job']=='owned-job'
    replacement=next(row for row in rows if row['created']==200)
    assert replacement['role']=='core' and 'job' not in replacement
    local=Local(tmp_path/'local');local.api=api;record={'processes':rows}
    historical=root if root_reused else old
    assert not local.alive(record,historical)
    assert local.alive(record,replacement)
    assert local.components_alive(record)
    assert api.descendants(rows)==rows


def test_pid_reused_outside_original_job_is_never_adopted_or_signalled(tmp_path,monkeypatch):
    api=object.__new__(WindowsProcesses)
    root={'pid':1,'created':100,'executable':'python.exe','role':'core','job':'owned-job'}
    old={'pid':2,'created':101,'executable':'python.exe','role':'core','parent':1}
    api.members=lambda job:[2]  # stale PID snapshot before the old process exits
    api.identity=lambda pid,job=None:None if job else {'pid':2,'created':200,'executable':'outside.exe'}
    assert api.descendants([root,old])==[root,old]
    local=Local(tmp_path/'local');local.api=api
    with pytest.raises(RuntimeError,match='replaced'):local.alive({'processes':[root,old]},old)
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:pytest.fail('An external PID must not receive any signal'))
    with pytest.raises(RuntimeError,match='replaced'):api.interrupt(old)


def test_missing_job_after_pid_history_remains_unknown():
    api=object.__new__(WindowsProcesses)
    def missing(name):raise RuntimeError('Original Windows job membership is unknown')
    api.members=missing
    root={'pid':1,'created':100,'executable':'python.exe','role':'core','job':'lost-job'}
    with pytest.raises(RuntimeError,match='unknown'):api.descendants([root])


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows process identity and console semantics')
def test_owned_console_interrupt_and_pid_replacement(tmp_path):
    api = WindowsProcesses()
    marker = tmp_path / 'clean-exit'
    code = ('import signal,time,pathlib,sys; '
            'signal.signal(signal.SIGINT,lambda *_:(pathlib.Path(sys.argv[1]).write_text("clean"),sys.exit(0))); '
            'pathlib.Path(sys.argv[1]+".ready").write_text("ready"); '
            '\nwhile True: time.sleep(.1)')
    options = subprocess.STARTUPINFO(); options.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    process = subprocess.Popen([sys.executable, '-c', code, str(marker)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=options)
    try:
        deadline = time.monotonic() + 10
        while not Path(str(marker) + '.ready').exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.05)
        owned = api.identity(process.pid)
        with pytest.raises(RuntimeError, match='replaced'):
            api.interrupt({**owned, 'created': owned['created'] + 1})
        assert process.poll() is None
        api.interrupt(owned)
        assert process.wait(timeout=10) == 0
        assert marker.read_text() == 'clean'
        assert not api.alive(owned)
    finally:
        # Test-only process is controlled; failure cleanup still uses its normal
        # console signal, never terminate/kill a possibly unrelated process.
        if process.poll() is None:
            api.interrupt(api.identity(process.pid))
            process.wait(timeout=10)


def test_sdk_drain_failure_preserves_native_and_core(tmp_path, monkeypatch):
    local = Local(tmp_path / 'local')
    local.layout.private.mkdir(parents=True)
    class Processes:
        def descendants(self, values): return values
        def alive(self, value): return True
        def interrupt(self, value): pytest.fail('No native/core signal before SDK draining succeeds')
    local.api = Processes()
    record = {'phase': 'running', 'processes': [{'pid': 1, 'role': 'assistant'}, {'pid': 2, 'role': 'core'}]}
    sentinel = local.layout.state / 'existing-contract.sqlite'
    sentinel.write_bytes(b'preserved-state')
    def blocked(*args, **kwargs):
        assert args[2] == 'call'
        if args[3] == 'runtime.status':
            return {'data': {'bootId': '11111111-1111-4111-8111-111111111111', 'account': {'username': 'ISC_001'}}}
        assert args[3] == 'runtime.stop'
        assert kwargs['accepted'] == ('completed', 'waiting')
        raise RuntimeError('Original SDK is still draining')
    monkeypatch.setattr(local, 'run', blocked)
    with pytest.raises(RuntimeError, match='still draining'):
        local.shutdown(record)
    assert sentinel.read_bytes() == b'preserved-state'
    assert json.loads(local.record_path.read_text())['phase'] == 'stopping'


def test_mismatched_local_identity_not_adopted(tmp_path):
    local = Local(tmp_path / 'local')
    identity = local.layout.claim()
    value = {'format': 'ioalab.windows-local/1', 'deploymentId': 'other',
             'environments': dict.fromkeys(('core', 'participant', 'swarm'), {})}
    local.record_path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match='Restore the original'):
        local.read()
    assert json.loads(local.record_path.read_text()) == value
    assert identity['deploymentId'] != 'other'


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows Job ownership')
def test_fast_exiting_front_cannot_hide_owned_worker(tmp_path):
    api = WindowsProcesses(); marker = tmp_path / 'worker'
    child = ('import signal,sys,time,pathlib,socket; '
             'signal.signal(signal.SIGINT,lambda *_:sys.exit(0)); '
             's=socket.socket(); s.bind(("127.0.0.1",0)); s.listen(); '
             'pathlib.Path(sys.argv[1]).write_text(str(s.getsockname()[1])); '
             '\nwhile True: time.sleep(.1)')
    parent = ('import subprocess,sys; '
              'ok=sys.stdin.buffer.read(1); '
              'subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]]) if ok==b"1" else None')
    options = subprocess.STARTUPINFO(); options.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    process = subprocess.Popen([sys.executable, '-c', parent, child, str(marker)], stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NEW_CONSOLE | 0x4, startupinfo=options)
    root = {**api.identity(process.pid), 'role': 'swarm-agent', 'job': 'Local\\ioa-test-' + uuid.uuid4().hex}
    try:
        api.assign(root['job'], process.pid)
        api.resume(process.pid)
        process.stdin.write(b'1'); process.stdin.flush(); process.stdin.close()
        assert process.wait(timeout=10) == 0
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(.05)
        members = api.descendants([root])
        workers = [p for p in members if p['pid'] != process.pid and Path(p['executable']).name.lower() == 'python.exe']
        assert workers  # Windows venv redirector + actual CPython may both exist.
        listeners = api.listener(int(marker.read_text()))
        assert len(listeners) == 1
        assert listeners.issubset(set(api.members(root['job'])))
        assert api.alive(workers[0])
        api.interrupt(workers[0])
        while api.members(root['job']):
            assert time.monotonic() < deadline
            time.sleep(.05)
    finally:
        if not process.stdin.closed: process.stdin.close()
        for pid in api.members(root['job']):
            current = api.identity(pid)
            if current and Path(current['executable']).name.lower() == 'python.exe': api.interrupt(current)


@pytest.mark.parametrize('replaced', [False, True])
def test_shutdown_uses_current_boot_and_refuses_replacement(tmp_path, monkeypatch, replaced):
    local = Local(tmp_path / 'local'); local.layout.private.mkdir(parents=True)
    class Processes:
        def descendants(self, values): return values
        def alive(self, value): return True
        def interrupt(self, value): pytest.fail('Unfinished SDK must preserve native/core')
    local.api = Processes()
    first = '11111111-1111-4111-8111-111111111111'
    second = '22222222-2222-4222-8222-222222222222'
    (local.layout.private / ('stop-' + first + '.json')).write_text(json.dumps({'bootId': first, 'operationId': 'old'}))
    record = {'processes': [{'pid': 1, 'role': 'assistant'}], 'assistantBootId': first if replaced else second}
    called = []
    def command(*args, **kwargs):
        if args[3] == 'runtime.status':
            return {'data': {'bootId': second, 'account': {'username': 'ISC_001'}}}
        called.append(args)
        assert json.loads(Path(args[-1]).read_text()) == {'bootId': second}
        assert Path(args[-1]).name == 'stop-' + second + '.json'
        raise RuntimeError('pending SDK drain')
    monkeypatch.setattr(local, 'run', command)
    with pytest.raises(RuntimeError, match='replaced' if replaced else 'pending SDK'):
        local.shutdown(record)
    assert bool(called) is not replaced
    assert json.loads((local.layout.private / ('stop-' + first + '.json')).read_text())['operationId'] == 'old'


def test_unknown_core_listener_cannot_receive_bootstrap(tmp_path, monkeypatch):
    local = Local(tmp_path / 'local')
    local.layout.private.mkdir(parents=True)
    class Processes:
        def alive(self, value): return True
        def listener(self, port): return {999}
        def members(self, name): return [1]
        def descendants(self, values): return values
    local.api = Processes()
    from types import SimpleNamespace
    config = SimpleNamespace(input={'port': 8750, 'bind': '127.0.0.1', 'public_url': 'http://127.0.0.1:8750'})
    monkeypatch.setattr('deploy.windows_local.http_open', lambda *_: pytest.fail('Unknown listener must not receive HTTP'))
    with pytest.raises(RuntimeError, match='listener is not the owned'):
        local.core_ready(config, {'processes': [{'role': 'core', 'pid': 1, 'job': 'owned'}]})


def test_recovery_cannot_forget_worker_after_dead_front(tmp_path, monkeypatch):
    local = Local(tmp_path / 'local'); local.layout.private.mkdir(parents=True)
    record = {'processes': [{'pid': 1, 'role': 'swarm-agent', 'job': 'persisted-job'}], 'phase': 'failed'}
    class Processes:
        def descendants(self, values):
            return [*values, {'pid': 2, 'role': 'swarm-agent', 'parent': 1}]
        def members(self, name): return [2]
        def alive(self, value): return value['pid'] == 2
    local.api = Processes()
    monkeypatch.setattr(local, 'read', lambda: record)
    monkeypatch.setattr(local, 'prepare', lambda *_: pytest.fail('An old live Job must block a new startup'))
    with pytest.raises(RuntimeError, match='still present'):
        local.serve()
    persisted = json.loads(local.record_path.read_text())
    assert persisted['processes'][0]['job'] == 'persisted-job'
    assert any(p['pid'] == 2 for p in persisted['processes'])
    assert persisted['phase'] == 'failed'


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows deployment directory ACL and writer lock')
def test_install_cannot_race_running_supervisor(tmp_path, monkeypatch):
    from deploy.environment import writer
    local = Local(tmp_path / 'local'); local.layout.claim()
    sentinel = local.record_path
    sentinel.write_text('{"existing":"startup-state"}')
    monkeypatch.setattr('deploy.windows_local.load', lambda *_a, **_k: {'target': 'windows-x86_64'})
    monkeypatch.setattr('deploy.windows_local.input_configuration', lambda *_: pytest.fail('Busy install must stop before input'))
    with writer(local.layout.private / 'local-runtime.lock'):
        with pytest.raises(RuntimeError, match='writer lock'):
            local.install(tmp_path / 'release', tmp_path / 'envs', tmp_path / 'input.json')
    assert sentinel.read_text() == '{"existing":"startup-state"}'


def test_source_scope_keeps_native_owners_without_linux_installer_matrix():
    from tools.check_scope import select_tests
    paths = ('deploy/windows_local.py',
             'src/official/admin-assistant/ioalab_admin_assistant/deployment.py',
             'src/official/admin-assistant/ioalab_admin_assistant/local_owner.py')
    for source in paths:
        selected = select_tests([source])['tests']
        assert 'tests/test_windows_local_owner.py' in selected
        assert 'tests/test_deployment_entry.py' not in selected
        assert 'tests/test_windows_local.py' in selected if source.startswith('deploy/') else 'tests/test_admin_assistant_preparation.py' in selected
    unrelated = select_tests(['src/platform/ioalab/storage.py'])['tests']
    assert 'tests/test_windows_local.py' not in unrelated
    assert 'tests/test_windows_local_owner.py' not in unrelated


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows named Job lookup')
def test_missing_job_is_unknown_not_empty():
    api=WindowsProcesses()
    with pytest.raises(RuntimeError,match='membership is unknown'):
        api.members('Local\\ioa-never-created-'+uuid.uuid4().hex)


def test_setup_failure_retains_supervisor_until_explicit_stop(tmp_path,monkeypatch):
    local=Local(tmp_path/'local');local.layout.private.mkdir(parents=True)
    record={'processes':[],'phase':'installed','deploymentId':'owned-local'}
    class Processes:
        def descendants(self,values):return values
        def members(self,name):return [1]
        def alive(self,value):return True
    local.api=Processes()
    monkeypatch.setattr(local,'read',lambda:record)
    def fail(value):
        value.update(processes=[{'pid':1,'role':'core','job':'still-owned'}],stage='fixed-public-prepare')
        raise RuntimeError('Public setup rejected')
    monkeypatch.setattr(local,'prepare',fail)
    original_refresh=local.refresh
    def refresh(value):
        original_refresh(value)
        if value['phase']=='failed':
            (local.layout.private/'stop-request.json').write_text(json.dumps({'deploymentId':'owned-local'}))
    monkeypatch.setattr(local,'refresh',refresh)
    stopped=[]
    def shutdown(value):
        assert value['phase']=='failed'
        assert value['failure']=={'type':'RuntimeError','stage':'fixed-public-prepare'}
        assert value['processes'][0]['job']=='still-owned'
        stopped.append(True)
    monkeypatch.setattr(local,'shutdown',shutdown)
    local.serve()
    assert stopped==[True]
