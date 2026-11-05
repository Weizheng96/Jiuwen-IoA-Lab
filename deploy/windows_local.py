"""Persistent, trusted Windows-local assembly of the ordinary platform and Jiuwen host.

This is not an online-trial sandbox. It never resets data or force-kills processes.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler

from .configuration import load as input_configuration
from .configuration import platform as platform_configuration
from .configs import Configuration, local_url
from .environment import Environment, writer
from .layout import Layout, read_environment
from .processes import child_environment
from .release import HEX, load, official_catalog, read_json
from .workspace import Workspace, path_without_links, save


FORMAT = 'ioalab.windows-local/1'
ROLES = ('core', 'swarm-agent', 'swarm-gateway', 'swarm-web', 'official', 'assistant')


class LocalFailure(RuntimeError):
    """Only fixed codes cross the diagnostic boundary; never upstream text."""
    def __init__(self, code, *, stage=None, role=None, win32=None):
        super().__init__(code)
        self.diagnostic = {'code':code, **({'stage':stage} if stage else {}),
                           **({'role':role} if role else {}), **({'win32':win32} if win32 is not None else {})}


def process_key(value):
    return tuple(value[name] for name in ('pid','created','executable'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def http_open(url):
    return build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=3)


def private_directory(path):
    """Restrict only a newly claimed deployment directory, before writing secrets."""
    path.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                            timeout=5, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    import csv
    import re
    rows = list(csv.reader(result.stdout.decode('utf-8', errors='replace').splitlines()))
    sid = rows[0][1] if rows and len(rows[0]) > 1 else ''
    if not re.fullmatch(r'S-[0-9-]+', sid):
        raise RuntimeError('Cannot identify the Windows deployment owner')
    result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                             '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Cannot protect the private deployment directory')


class WindowsProcesses:
    """Ownership checks and console interrupts for this fixed local assembly only."""
    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.GetCurrentProcess.restype = wintypes.HANDLE
        self.api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        self.api.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        self.api.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                       ctypes.POINTER(wintypes.DWORD))
        self.api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        self.api.OpenJobObjectW.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.api.IsProcessInJob.argtypes = (wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL))
        self.api.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        self.jobs = {}

    def resume(self, pid):
        class Thread(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('tid', wintypes.DWORD),
                        ('pid', wintypes.DWORD), ('base', wintypes.LONG), ('delta', wintypes.LONG),
                        ('flags', wintypes.DWORD)]
        self.api.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(Thread))
        self.api.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(Thread))
        self.api.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.api.OpenThread.restype = wintypes.HANDLE
        self.api.ResumeThread.argtypes = (wintypes.HANDLE,)
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise RuntimeError('Cannot inspect the suspended owned process')
        try:
            row = Thread(); row.size = ctypes.sizeof(row); tids = []
            more = self.api.Thread32First(snapshot, ctypes.byref(row))
            while more:
                if row.pid == pid: tids.append(row.tid)
                more = self.api.Thread32Next(snapshot, ctypes.byref(row))
            if len(tids) != 1:
                raise RuntimeError('Owned suspended process does not have its original single thread')
            thread = self.api.OpenThread(2, False, tids[0])
            if not thread:
                raise RuntimeError('Cannot resume the owned local component')
            try:
                if self.api.ResumeThread(thread) != 1:
                    raise RuntimeError('Owned process suspension state changed')
            finally:
                self.api.CloseHandle(thread)
        finally:
            self.api.CloseHandle(snapshot)

    def assign(self, name, pid):
        if name in self.jobs:
            raise RuntimeError('This component already has an owned Windows job')
        ctypes.set_last_error(0)
        job = self.api.CreateJobObjectW(None, name)
        if not job or ctypes.get_last_error() == 183:
            if job: self.api.CloseHandle(job)
            raise RuntimeError('Windows component job is already owned; no process was released')
        process = self.api.OpenProcess(0x100 | 0x1, False, pid)  # SET_QUOTA | TERMINATE, assign permission only
        try:
            if not process or not self.api.AssignProcessToJobObject(job, process):
                self.api.CloseHandle(job)
                raise RuntimeError('Cannot record all native descendants in an owned Windows job')
        finally:
            if process: self.api.CloseHandle(process)
        # No KILL_ON_JOB_CLOSE or limits: persistent local data and processes are
        # not a trial sandbox. A job is only reliable OS-provided membership.
        self.jobs[name] = job

    def members(self, name):
        job = self.jobs.get(name)
        if not job:
            job = self.api.OpenJobObjectW(0x4, False, name)  # QUERY
            if not job:
                raise RuntimeError('Original Windows job membership is unknown; no process is considered released')
            self.jobs[name] = job
        capacity = 64
        while capacity <= 4096:
            # JOBOBJECT_BASIC_PROCESS_ID_LIST has two DWORDs then ULONG_PTR[].
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            if self.api.QueryInformationJobObject(job, 3, buffer, len(buffer), None):
                count = ctypes.c_ulong.from_buffer(buffer, 4).value
                values = (ctypes.c_size_t * count).from_buffer(buffer, 8)
                return list(values)
            if ctypes.get_last_error() != 234:
                raise RuntimeError('Cannot verify all component job members')
            capacity *= 2
        raise RuntimeError('Too many native job members; preserve and inspect the instance')

    def listener(self, port):
        library = ctypes.WinDLL('iphlpapi', use_last_error=True)
        library.GetExtendedTcpTable.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                                wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG)
        size = wintypes.DWORD()
        result = library.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
        if result != 122:
            raise RuntimeError('Cannot inspect the local TCP listener ownership')
        buffer = ctypes.create_string_buffer(size.value)
        if library.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 3, 0):
            raise RuntimeError('Cannot inspect the local TCP listener ownership')
        count = ctypes.c_ulong.from_buffer(buffer).value
        rows = (ctypes.c_ulong * (6 * count)).from_buffer(buffer, 4)
        return {rows[i + 5] for i in range(0, count * 6, 6) if socket.ntohs(rows[i + 2] & 0xffff) == port}

    def identity(self, pid, *, job=None):
        handle = self.api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            if ctypes.get_last_error() == 87:
                return None
            raise RuntimeError('Cannot verify an owned Windows process')
        try:
            if job is not None:
                # Query membership on this same process handle, not on a PID
                # list that could race an external process reusing that PID.
                if job not in self.jobs:self.members(job)
                belongs=wintypes.BOOL()
                if not self.api.IsProcessInJob(handle,self.jobs[job],ctypes.byref(belongs)):
                    raise RuntimeError('Cannot verify process job ownership')
                if not belongs.value:return None
            exit_code = wintypes.DWORD()
            if not self.api.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                raise RuntimeError('Cannot verify process exit')
            if exit_code.value != 259:  # STILL_ACTIVE
                return None
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not self.api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                raise RuntimeError('Cannot verify process creation time')
            size = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(size.value)
            if not self.api.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                raise RuntimeError('Cannot verify process executable')
            return {'pid': pid, 'created': (created.dwHighDateTime << 32) | created.dwLowDateTime,
                    'executable': str(Path(name.value).resolve())}
        finally:
            self.api.CloseHandle(handle)

    def alive(self, record):
        current = self.identity(record['pid'])
        if current is None:
            return False
        if current != {k: record[k] for k in ('pid', 'created', 'executable')}:
            raise RuntimeError('Recorded PID was replaced; no signal was sent')
        return True

    def descendants(self, records):
        if any('job' in row for row in records):
            known = {process_key(row): row for row in records}
            for root in [row for row in records if 'job' in row]:
                for pid in self.members(root['job']):
                    actual = self.identity(pid,job=root['job'])
                    if actual is None:
                        continue
                    key=process_key(actual)
                    if key in known:
                        if known[key]['role']!=root['role']:
                            raise RuntimeError('An owned job process changed component ownership')
                    else:
                        # A later generation proved by the same kernel Job is
                        # another owned member; retain old roots and history.
                        known[key] = {**actual, 'role': root['role'], 'parent': root['pid']}
            return list(known.values())
        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                        ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                        ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
        snapshot = self.api.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise RuntimeError('Cannot inspect owned process descendants')
        self.api.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
        self.api.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
        try:
            entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
            rows = []
            more = self.api.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                rows.append((entry.th32ProcessID, entry.th32ParentProcessID))
                more = self.api.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.api.CloseHandle(snapshot)
        known = {row['pid']: row for row in records}
        changed = True
        while changed:
            changed = False
            for pid, parent in rows:
                if pid in known or parent not in known:
                    continue
                # Parent identity must still match, not just its recycled PID.
                if not self.alive(known[parent]):
                    continue
                child = self.identity(pid)
                if child and child['created'] >= known[parent]['created']:
                    known[pid] = {**child, 'role': known[parent]['role'], 'parent': parent}
                    changed = True
        return list(known.values())

    def interrupt(self, record):
        if not self.alive(record):
            return
        result = subprocess.run([sys.executable, '-c',
                    'import json,sys; from deploy.windows_local import WindowsProcesses; '
                    '\ntry:\n'
                    ' result=WindowsProcesses()._console_interrupt(json.loads(sys.argv[1])); print(json.dumps(result))\n'
                    'except Exception as error:\n'
                    ' print(json.dumps(getattr(error,"diagnostic",{"code":"console_helper_unknown"}))); sys.exit(1)', json.dumps(record)],
                    cwd=Path(__file__).resolve().parents[1], env=child_environment(),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        try:receipt=json.loads(result.stdout)
        except ValueError:receipt={}
        if not isinstance(receipt,dict):receipt={}
        if result.returncode or receipt.get('code') not in ('signal_sent','process_exited'):
            code=receipt.get('code')
            if code not in ('console_attach_failed','console_event_failed','console_identity_changed'):
                code='console_helper_unknown'
            raise LocalFailure(code,stage='console.interrupt',role=record.get('role'),
                               win32=receipt.get('win32') if type(receipt.get('win32')) is int else None)

    def _console_interrupt(self, record):
        if not self.alive(record):
            return {'code':'process_exited','signalSent':False}
        if record.get('job'):
            current=self.identity(record['pid'],job=record['job'])
            if current is None or process_key(current)!=process_key(record):
                raise LocalFailure('console_identity_changed')
        # Every root is started in its own hidden console. No host/user console
        # is attached here; never send a console-wide event to an unknown PID.
        self.api.FreeConsole()
        if not self.api.AttachConsole(record['pid']):
            error=ctypes.get_last_error()
            if self.identity(record['pid']) is None:
                return {'code':'process_exited','signalSent':False}
            raise LocalFailure('console_attach_failed',win32=error)
        try:
            current=self.identity(record['pid'],job=record.get('job'))
            if current is None or process_key(current)!=process_key(record):
                raise LocalFailure('console_identity_changed')
            self.api.SetConsoleCtrlHandler(None, True)
            if not self.api.GenerateConsoleCtrlEvent(0, 0):  # CTRL_C_EVENT, attached console only
                raise LocalFailure('console_event_failed',win32=ctypes.get_last_error())
            time.sleep(.1)
            return {'code':'signal_sent','signalSent':True}
        finally:
            self.api.FreeConsole()
            self.api.SetConsoleCtrlHandler(None, False)


class Local:
    def __init__(self, home):
        self.home = path_without_links(home)
        if self.home == self.home.parent:
            raise ValueError('Use a dedicated local deployment directory')
        self.layout = Layout(self.home / 'runtime', self.home / 'data', self.home / 'config')
        self.record_path = self.layout.private / 'windows-local.json'
        self.api = WindowsProcesses() if os.name == 'nt' else None

    def read(self):
        identity = self.layout.inspect()
        value = read_json(self.record_path)
        if (value.get('format') != FORMAT or value.get('deploymentId') != identity['deploymentId']
                or set(value.get('environments', {})) != {'core', 'participant', 'swarm'}):
            raise RuntimeError('Restore the original Windows-local deployment record')
        return value

    def write(self, value):
        save(self.record_path, value)

    def failure(self, record, error, *, stage, started):
        detail={'code':'local_operation_failed','stage':stage,'type':type(error).__name__,
                'elapsedSeconds':round(max(0,time.monotonic()-started),3)}
        detail.update(getattr(error,'diagnostic',{}))
        jobs=[]
        for row in record['processes']:
            if 'job' not in row:continue
            try:jobs.append({'role':row['role'],'members':sorted(self.api.members(row['job']))})
            except (OSError,RuntimeError):jobs.append({'role':row['role'],'membership':'unknown'})
        detail['jobs']=jobs
        record.update(phase='failed',failure=detail);self.write(record)

    def install(self, release, environments, configuration, *, retry_failed=False):
        document = load(release, require_target=True)
        if document['target'] != 'windows-x86_64':
            raise ValueError('Use a reviewed windows-x86_64 release')
        if self.home.exists() and any(p.name not in ('runtime', 'data', 'config') for p in self.home.iterdir()):
            raise ValueError('Use a dedicated local deployment home; unrelated files are present')
        identity = self.layout.claim()
        for path in (self.layout.root, self.layout.state, self.layout.config, self.layout.private):
            private_directory(path)
        with writer(self.layout.private / 'local-install.lock'), writer(self.layout.private / 'local-runtime.lock'):
            if self.record_path.exists():
                old = self.read()
                if old['releaseId'] != document['releaseId']:
                    raise RuntimeError('Preserve this deployment; an explicit reviewed local upgrade is required')
                self.require_empty(old)
            value = input_configuration(configuration)
            core=platform_configuration(value['platform_config'])
            if core['bind'] != '127.0.0.1' or core['public_url'] != local_url('127.0.0.1', core['port']):
                raise ValueError('Trusted local deployment uses its explicit 127.0.0.1 core URL')
            results = {unit: Environment(release, unit, environments).install(
                index=value['pypi_index'], retry_failed=retry_failed) for unit in ('core', 'participant', 'swarm')}
            # Check the real DACL, not just icacls's exit code, before persisting
            # credentials. Existing unrelated explicit grants are never adopted.
            paths = [self.layout.root, self.layout.state, self.layout.config, self.layout.private,
                     *(p / '.ioalab-deployment.json' for p in (self.layout.root, self.layout.state, self.layout.config))]
            checked = subprocess.run([results['participant']['python'], '-I', '-c',
                'import sys; from ioalab_admin_assistant.local_owner import owned; '
                '[owned(p) for p in sys.argv[1:]]', *map(str, paths)],
                env=child_environment(), capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            if checked.returncode:
                raise RuntimeError('Deployment ownership or ACL verification failed; no model configuration was written')
            stable = self.assets({'releaseId': document['releaseId']})
            if stable.exists():
                if load(stable) != document:
                    raise RuntimeError('Established release assets changed; preserve the original deployment')
            else:
                stable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(release, stable)
                if load(stable) != document:
                    raise RuntimeError('Copied release failed validation')
            config = Configuration(self.layout, identity, value, assets=stable)
            config.base()
            record = {'format': FORMAT, 'deploymentId': identity['deploymentId'],
                      'releaseId': document['releaseId'], 'phase': 'installed',
                      'environments': results, 'processes': [], 'prepared': {}}
            if self.record_path.exists():
                old = self.read(); record['prepared'] = old['prepared']
                record['initialReadinessVerified'] = old.get('initialReadinessVerified', False)
            self.write(record)
            return self.public(record)

    def config(self):
        return Configuration(self.layout, self.layout.inspect(), read_json(self.layout.private / 'initial.json')['input'],
                             assets=self.assets(self.read()))

    def assets(self, record):
        identifier = record.get('releaseId')
        if not isinstance(identifier, str) or not HEX.fullmatch(identifier):
            raise ValueError('Restore the original content-addressed release identity')
        return self.layout.environments / 'releases' / identifier

    def command(self, record, unit, module, *args):
        return [record['environments'][unit]['python'], '-I', '-X', 'utf8', '-m', module, *map(str, args)]

    def run(self, record, module, *args, environment=None, accepted=('completed',)):
        record['stage'] = module + ':' + str(args[0]); self.write(record)
        if args[0]=='call' and len(args)>1:
            record['stage']+=':'+str(args[1]);self.write(record)
        result = subprocess.run(self.command(record, 'participant', module, *args),
                                cwd=self.layout.state, env={**child_environment(), **(environment or {})},
                                capture_output=True, text=True, encoding='utf-8',
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            answer = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError('Public deployment command returned no valid receipt: ' + module) from None
        if result.returncode not in (0, 2) or answer.get('status') not in accepted:
            code = (answer.get('error') or {}).get('code')
            if code not in ('authenticate', 'not_ready', 'forbidden', 'configuration_changed', 'identity_conflict','unknown_result','instance_changed'):
                code = 'rejected'
            raise LocalFailure('public_command_'+code,stage=record['stage'])
        return answer

    def plans(self, record):
        configs = self.layout.config / 'agents'; agents = self.layout.state / 'agents'
        result = {'core': ('core', 'ioalab', ['--config',self.layout.config/'core/platform.json','serve'], self.layout.state / 'core', None),
                  'official': ('participant', 'ioalab_services', ['serve', '--config', configs / 'official.json'], agents / 'official', configs / 'official.env')}
        if self.config().input['admin_assistant']['enabled']:
            for role, module in (('swarm-agent', 'jiuwenswarm.server.app_agentserver'),
                                 ('swarm-gateway', 'jiuwenswarm.gateway.app_gateway'),
                                 ('swarm-web', 'jiuwenswarm.channels.web.app_web')):
                result[role] = ('swarm', module, [], agents / 'swarm-admin', configs / 'swarm-admin.env')
            result['assistant'] = ('participant', 'ioalab_jiuwen', ['serve', '--config', agents / 'admin-assistant/config.json'],
                                   agents / 'admin-assistant', configs / 'assistant.env')
        return result

    def spawn(self, record, role):
        unit, module, arguments, cwd, environment = self.plans(record)[role]
        cwd.mkdir(parents=True, exist_ok=True)
        options = subprocess.STARTUPINFO(); options.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # Do not let a fast-exiting Front orphan an unrecorded Worker. The fixed
        # wrapper waits until its OS job and creation-time receipt are recorded.
        gate = ('import runpy,sys; '
                'ok=sys.stdin.buffer.read(1); '
                'sys.stdin.close(); '
                'sys.exit(1) if ok!=b"1" else None; '
                'sys.argv=sys.argv[1:]; '
                'runpy.run_module(sys.argv[0],run_name="__main__",alter_sys=True)')
        command = [record['environments'][unit]['python'], '-I', '-X', 'utf8', '-c', gate, module, *map(str, arguments)]
        process = subprocess.Popen(command, cwd=cwd,
                    env={**child_environment(), **(read_environment(environment) if environment else {})}, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=options,
                    creationflags=subprocess.CREATE_NEW_CONSOLE | 0x4)  # CREATE_SUSPENDED
        actual = self.api.identity(process.pid)
        if actual is None:
            raise RuntimeError('Local component exited before ownership could be recorded: ' + role)
        resumed = False
        try:
            job = 'Local\\ioalab-' + record['deploymentId'] + '-' + role + '-' + uuid.uuid4().hex
            self.api.assign(job, process.pid)
            record['processes'].append({**actual, 'role': role, 'job': job})
            self.write(record)
            self.api.resume(process.pid); resumed = True
            process.stdin.write(b'1'); process.stdin.flush()
        finally:
            process.stdin.close()  # EOF exits an unreleased gate normally.
            if not resumed:
                self.api.resume(process.pid)

    def refresh(self, record):
        record['processes'] = self.api.descendants(record['processes'])
        self.write(record)

    def alive(self, record, process):
        roots = [p for p in record['processes'] if p['role'] == process['role'] and 'job' in p]
        if roots and process['pid'] not in self.api.members(roots[0]['job']):
            return False  # A recycled PID outside our job is never touched.
        try:return self.api.alive(process)
        except RuntimeError:
            if not roots:raise
            actual=self.api.identity(process['pid'],job=roots[0]['job'])
            if actual and process_key(actual)!=process_key(process) and any(
                    row['role']==process['role'] and process_key(row)==process_key(actual) for row in record['processes']):
                return False  # This historical generation exited; never signal its replacement.
            raise

    def role_alive(self,record,role):
        return any(self.alive(record,row) for row in record['processes'] if row['role']==role
                   and Path(row.get('executable','')).name.lower()!='conhost.exe')

    def components_alive(self,record):
        roles={row['role'] for row in record['processes'] if 'parent' not in row}
        return all(self.role_alive(record,role) for role in roles)

    def require_empty(self, record):
        # The previous supervisor may have died before its first refresh. Its
        # persisted Job, not a dead Front PID, is the authoritative membership.
        self.refresh(record)
        if (any(self.api.members(p['job']) for p in record['processes'] if 'job' in p)
                or any(self.alive(record, p) for p in record['processes'])):
            raise RuntimeError('Owned local processes are still present; inspect or stop the original instance')

    def wait(self, record, query, *, seconds=180):
        started=time.monotonic();deadline = started + seconds;attempts=0
        while True:
            self.refresh(record)
            try:
                return query()
            except (OSError, ValueError, RuntimeError) as error:
                attempts+=1
                record['lastReadinessObservation']={'elapsedSeconds':round(time.monotonic()-started,3),
                    'attempts':attempts,'stage':record.get('stage'),'errorType':type(error).__name__,
                    'code':getattr(error,'diagnostic',{}).get('code','readiness_pending')}
                self.write(record)
                if time.monotonic() >= deadline:
                    raise LocalFailure('readiness_observation_timeout',stage=record.get('stage')) from None
                time.sleep(.5)

    def core_ready(self, config, record):
        self.refresh(record)
        roots = [p for p in record['processes'] if p['role'] == 'core' and 'parent' not in p]
        if len(roots) != 1 or not self.role_alive(record,'core'):
            raise RuntimeError('Core listener is not the owned local process; no bootstrap credentials were submitted')
        members = self.api.members(roots[0]['job'])
        listeners = self.api.listener(config.input['port'])
        if len(listeners) != 1 or not listeners.issubset(set(members)):
            raise RuntimeError('Core listener is not the owned local process; no bootstrap credentials were submitted')
        with http_open(local_url(config.input['bind'], config.input['port']) + '/api/config') as response:
            value = json.load(response)
        if value.get('publicUrl') != config.input['public_url']:
            raise RuntimeError('The listening core belongs to another platform identity')
        if not self.role_alive(record,'core') or self.api.listener(config.input['port']) != listeners:
            raise RuntimeError('Owned core listener changed during readiness')
        return value

    def check_ports(self, config):
        # Refuse other local instances before starting any owned component.
        for port in (config.input['port'], *config.input['swarm_ports'].values()):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(('127.0.0.1', port))

    def prepare(self, record):
        config = self.config()
        self.check_ports(config)
        stable = self.assets(record)
        for unit, installed in record['environments'].items():
            actual = Environment(stable, unit, Path(installed['environment']).parent.parent).install(verify=True)
            if actual['environmentId'] != installed['environmentId'] or actual['python'] != installed['python']:
                raise RuntimeError('Installed environment identity changed; no local component was started')
        python = record['environments']['swarm']['python']
        official = Workspace(python, self.layout.state / 'agents/swarm-official', record['deploymentId']).prepare(config.input['model'])
        config.official(official)
        self.spawn(record, 'core'); self.wait(record, lambda: self.core_ready(config, record))
        args = ('--config', self.layout.config / 'agents/official.json')
        env = read_environment(self.layout.config / 'agents/official.env')
        for stage in ('prepare', 'authorize'):
            if not record['prepared'].get('official-' + stage):
                extra = ('--admin-username',config.core()['admin']['username'],'--admin-password-env', 'IOALAB_BOOTSTRAP_ADMIN_PASSWORD') if stage == 'authorize' else ()
                self.core_ready(config, record)
                self.run(record, 'ioalab_services', stage, *args, *extra,
                         environment={**env, **({'IOALAB_BOOTSTRAP_ADMIN_PASSWORD': config.core()['admin']['password']} if extra else {})})
                record['prepared']['official-' + stage] = True; self.write(record)
        if config.input['admin_assistant']['enabled']:
            admin = Workspace(python, self.layout.state / 'agents/swarm-admin', record['deploymentId']).prepare(config.input['model'])
            recipe = config.administrator(admin)
            if not record['prepared'].get('administrator'):
                self.core_ready(config, record)
                self.run(record, 'ioalab_admin_assistant', 'prepare', '--config', recipe)
                record['prepared']['administrator'] = True; self.write(record)
        for role in ROLES[1:]:
            if role not in self.plans(record):
                continue
            if role == 'assistant':
                self.core_ready(config, record)
                self.run(record, 'ioalab_sdk.runtime', 'login', '--config', self.layout.state / 'agents/admin-assistant/config.json')
            self.spawn(record, role)

    def readiness(self, record):
        config = self.config()
        self.core_ready(config, record)
        self.refresh(record)
        if not self.components_alive(record):
            raise RuntimeError('A local component exited before readiness')
        official = self.run(record, 'ioalab_services', 'status', '--config', self.layout.config / 'agents/official.json')
        expected = official_catalog(self.assets(record), load(self.assets(record)))
        rows = official.get('accounts')
        observed=[row for row in rows if isinstance(row,dict)] if isinstance(rows,list) else []
        services=[item.get('current',{}) for row in observed for item in row.get('services',[]) if isinstance(item,dict)]
        record['officialReadinessProgress']={'observedAt':time.time(),'accounts':len(observed),'expectedAccounts':len(expected),
            'connected':sum(bool(row.get('runtime',{}).get('platformConnected')) for row in observed),
            'hostAvailable':sum(bool(row.get('runtime',{}).get('host',{}).get('available')) for row in observed),
            'services':len(services),'indexReady':sum(bool(item.get('indexReady')) for item in services),
            'executionReady':sum(bool(item.get('localExecution',{}).get('executionReady')) for item in services)}
        self.write(record)
        if (not isinstance(rows, list) or sorted(row.get('account', '') for row in rows) != sorted(expected)
                or any(row.get('error') or not row.get('runtime', {}).get('platformConnected')
                       or not row.get('runtime', {}).get('host', {}).get('available') for row in rows)):
            raise RuntimeError('Official SDK/host connectivity is not ready')
        if any(sorted(item.get('profile', '') for item in row.get('services', [])) != sorted(expected[row['account']])
               or any(item.get('error') or not item.get('current', {}).get('indexReady')
                      or not item.get('current', {}).get('templateReady')
                      or not item.get('current', {}).get('localExecution', {}).get('executionReady')
                      for item in row.get('services', [])) for row in rows):
            raise RuntimeError('The declared official service catalog is not ready')
        if not record.get('initialReadinessVerified') and any(
                not item.get('current', {}).get('admissionPolicy', {}).get('accepting')
                for row in rows for item in row['services']):
            raise RuntimeError('Initial official service admission is not ready')
        result = {'core': True, 'officialCatalog': True}
        if config.input['admin_assistant']['enabled']:
            native = local_url('127.0.0.1', config.input['swarm_ports']['frontend']) + '/?user_id=ioa-admin'
            with http_open(native) as response:
                if b'<html' not in response.read(100000).lower():
                    raise RuntimeError('Original prebuilt native page is not available')
            live = self.run(record, 'ioalab_sdk.runtime', 'call', 'runtime.status', '--config',
                            self.layout.state / 'agents/admin-assistant/config.json')['data']
            if (live.get('account', {}).get('username') != 'ISC_001' or not live.get('platformConnected')
                    or not live.get('host', {}).get('available')):
                raise RuntimeError('Personal assistant SDK/host is not ready')
            if record.get('assistantBootId') not in (None, live['bootId']):
                raise RuntimeError('Personal assistant runtime was replaced')
            record['assistantBootId'] = live['bootId']; self.write(record)
            result.update(nativeWeb=native, assistantConnected=True)
            from .assistant_skill import ensure_assistant_skill
            skill=ensure_assistant_skill(self.assets(record),record['environments']['participant']['python'],
                self.layout.state/'agents/swarm-admin',self.layout.state/'agents/admin-assistant/config.json',
                local_url('127.0.0.1',config.input['swarm_ports']['frontend']),live['bootId'])
            result.update(personalAssistantSkill=skill)
        return result

    def public(self, record):
        return {'status': 'completed' if record['phase'] in ('installed', 'running', 'stopped') else 'waiting',
                'phase': record['phase'], 'deploymentId': record['deploymentId'], 'releaseId': record['releaseId'],
                'home': str(self.home), 'dataPreserved': True,
                'runtimeReady': record['phase'] == 'running' and bool(record.get('readiness')),
                'readiness': record.get('readiness'), 'browserAcceptance': 'unverified',
                'failure': record.get('failure'), 'allOwnedJobsEmpty': record.get('allOwnedJobsEmpty', False),
                'processes': [{k: p[k] for k in ('pid', 'role')} for p in record.get('processes', [])]}

    def serve(self):
        with writer(self.layout.private / 'local-runtime.lock'):
            record = self.read()
            self.require_empty(record)
            record.update(processes=[], phase='starting', readiness=None, assistantBootId=None,
                          allOwnedJobsEmpty=False, failure=None, lastReadinessObservation=None,
                          officialReadinessProgress=None, runtimeFailure=None); self.write(record)
            stop = False
            def request(*_):
                nonlocal stop
                stop = True
            previous = signal.signal(signal.SIGINT, request)
            started=time.monotonic(); startup_ready=False
            try:
                self.prepare(record)
                record['readiness'] = self.wait(record, lambda: self.readiness(record))
                record['initialReadinessVerified'] = True
                record['startupElapsedSeconds']=round(time.monotonic()-started,3)
                record['phase'] = 'running'; self.write(record)
                startup_ready=True
            except Exception as error:
                self.failure(record,error,stage=record.get('stage'),started=started)
                # Keep the original Job handles while the operator inspects a
                # failed setup. Missing handles are never proof of empty jobs.
            failed_request=None; fault_stop_attempted=False
            try:
                while True:
                    try:
                        self.refresh(record)
                        if (startup_ready and not fault_stop_attempted
                                and not self.components_alive(record)):
                            failed_roles=sorted({p['role'] for p in record['processes'] if 'parent' not in p
                                                 and not self.role_alive(record,p['role'])})
                            record['runtimeFailure']={'code':'owned_component_exited','roles':failed_roles}
                            self.failure(record,LocalFailure('owned_component_exited'),
                                         stage='runtime.monitor',started=started)
                            # Use the same owner-fenced drain exactly once. If it
                            # fails, retain this supervisor and its Job handles
                            # until an explicit fresh stop request arrives.
                            fault_stop_attempted=True;stop=True
                    except Exception as error:
                        self.failure(record,error,stage='ownership.monitor',started=started)
                    path=self.layout.private/'stop-request.json'
                    stamp=None
                    if path.exists():
                        try:
                            request_record=read_json(path);stamp=path.stat().st_mtime_ns
                            if request_record=={'deploymentId':record['deploymentId']} and stamp!=failed_request:stop=True
                        except (OSError,ValueError) as error:
                            self.failure(record,error,stage='stop.request',started=started)
                    if stop:
                        shutdown_started=time.monotonic()
                        try:
                            self.shutdown(record)
                            break
                        except Exception as error:
                            self.failure(record,error,stage=record.get('stage') or 'shutdown',started=shutdown_started)
                            # Keep the same supervisor, Job handles and request.
                            # A fresh public stop rewrites that request; no busy
                            # automatic retry of an unclassified outcome.
                            failed_request=stamp;stop=False
                    time.sleep(.25)
            finally:
                signal.signal(signal.SIGINT, previous)

    def shutdown(self, record):
        record['phase'] = 'stopping'; self.refresh(record)
        for role in ('official', 'assistant'):
            roots = [p for p in record['processes'] if p['role'] == role and 'parent' not in p]
            if not roots or not any(self.alive(record, p) for p in record['processes'] if p['role'] == role):
                continue
            if role == 'assistant':
                config_path = self.layout.state / 'agents/admin-assistant/config.json'
                live = self.run(record, 'ioalab_sdk.runtime', 'call', 'runtime.status', '--config', config_path)['data']
                boot = live.get('bootId')
                if (not isinstance(boot, str) or str(uuid.UUID(boot)) != boot
                        or record.get('assistantBootId') not in (None, boot)
                        or live.get('account', {}).get('username') != 'ISC_001'):
                    raise RuntimeError('Personal assistant runtime was replaced; no stop was submitted')
                record['assistantBootId'] = boot; self.write(record)
                arguments = self.layout.private / ('stop-' + boot + '.json')
                if not arguments.exists():
                    save(arguments, {'bootId': boot})
                self.run(record, 'ioalab_sdk.runtime', 'call', 'runtime.stop', '--config',
                         config_path, '--args-file', arguments,
                         accepted=('completed', 'waiting'))
            else:
                # The public coordinator's SIGINT handler drains all owned SDK
                # runtimes before releasing its deployment lock.
                if self.alive(record, roots[0]):
                    self.api.interrupt(roots[0])
                else:
                    self.run(record, 'ioalab_services', 'stop', '--config',
                             self.layout.config / 'agents/official.json', '--wait-seconds', '30',
                             accepted=('completed', 'waiting'))
        deadline = time.monotonic() + 30
        while any(self.alive(record, p) for p in record['processes'] if p['role'] in ('official', 'assistant')):
            self.refresh(record)
            if time.monotonic() >= deadline:
                self.write(record)
                raise RuntimeError('SDK draining is still pending; native host and core were preserved')
            time.sleep(.2)
        for roles in (('swarm-web', 'swarm-gateway', 'swarm-agent'), ('core',)):
            self.refresh(record)
            for process in record['processes']:
                if (process['role'] in roles and Path(process['executable']).name.lower() != 'conhost.exe'
                        and self.alive(record, process)):
                    record['stage']='console.interrupt:'+process['role'];self.write(record)
                    root=next(row for row in record['processes'] if row['role']==process['role'] and 'job' in row)
                    self.api.interrupt({**process,'job':root['job']})
            deadline = time.monotonic() + 30
            while any(self.alive(record, p) for p in record['processes'] if p['role'] in roles):
                self.refresh(record)
                if time.monotonic() >= deadline:
                    self.write(record)
                    raise RuntimeError('An owned native process has not exited; no force kill or reset was used')
                time.sleep(.2)
        if any(self.api.members(p['job']) for p in record['processes'] if 'job' in p):
            raise RuntimeError('An original Windows job is not empty; shutdown remains incomplete')
        record['lastStoppedProcesses'] = record['processes']
        record.update(phase='stopped', processes=[], allOwnedJobsEmpty=True); self.write(record)
        request = self.layout.private / 'stop-request.json'
        if request.exists():
            request.unlink()
        return self.public(record)

    def stop(self):
        record = self.read()
        # If no supervisor owns the runtime lock, resume an interrupted setup's
        # explicit shutdown here. Otherwise ask the original supervisor to drain.
        try:
            guard = writer(self.layout.private / 'local-runtime.lock'); guard.__enter__()
        except RuntimeError:
            save(self.layout.private / 'stop-request.json', {'deploymentId': record['deploymentId']})
            return {'status': 'waiting', 'phase': 'stop_requested', 'dataPreserved': True}
        try:
            return self.shutdown(record)
        finally:
            guard.__exit__(None, None, None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'start', 'serve', 'status', 'stop'))
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--release', type=Path)
    parser.add_argument('--environments', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('This entry assembles trusted native Windows; Linux uses its existing deployment entry')
    local = Local(args.home)
    try:
        if args.command == 'install':
            if not all((args.release, args.environments, args.config)):
                parser.error('install requires --release, --environments and a private --config JSON')
            result = local.install(args.release, args.environments, args.config, retry_failed=args.retry_failed)
        elif any((args.release, args.environments, args.config, args.retry_failed)):
            parser.error('Installation inputs are only accepted by install')
        elif args.command == 'serve':
            local.serve(); result = local.public(local.read())
        elif args.command == 'start':
            record = local.read()
            if record['phase'] not in ('installed', 'stopped'):
                raise RuntimeError('Inspect or stop the original instance before starting')
            subprocess.Popen([sys.executable, '-m', 'deploy.windows_local', 'serve', '--home', str(local.home)],
                             cwd=Path(__file__).resolve().parents[1], env=child_environment(),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            result = {'status': 'waiting', 'phase': 'starting', 'dataPreserved': True}
        elif args.command == 'stop':
            result = local.stop()
        else:
            record = local.read(); result = local.public(record)
            result['processes'] = [{**row, 'alive': local.alive(record, source)}
                                   for row, source in zip(result['processes'], record['processes'])]
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'completed' else 2
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Never echo upstream output or initial configuration containing secrets.
        print(json.dumps({'status': 'failed', 'dataPreserved': True,
                          'recovery': 'inspect_original_local_deployment'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
