"""Fresh, disposable Linux/systemd install and upgrade proof; no model work or mail.

This experiment invokes the default product commands. It never treats a fake
manager, a Windows result or a recorded PID as proof of service ownership.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy import activation, backup, units
from deploy.jobs import SOURCE_DIRECTORIES, SOURCE_FILES, source_identity
from deploy.layout import Layout, read_environment
from deploy.linux import Systemd, require_host
from deploy.processes import child_environment
from deploy.release import read_json
from deploy.files import save, sha


def require_fresh_host(output):
    require_host()
    import grp
    import pwd
    for name in ('ioalab-core', 'ioalab-agents'):
        for lookup in (pwd.getpwnam, grp.getgrnam):
            try: lookup(name)
            except KeyError: pass
            else: raise RuntimeError('Use a fresh disposable runner, without existing IoA accounts or groups')
    names = units.plans(Layout('/unused-runtime', '/unused-state', '/unused-config'), administrator=True)
    manager = Systemd(uuid.uuid4().hex, {}, lambda: None)
    for row in names.values():
        live = manager.inspect(row['unit'])
        if live.get('LoadState') != 'not-found':
            raise RuntimeError('Use a fresh runner, without existing IoA services')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.parent.resolve() != Path('/tmp'):
        raise ValueError('Choose a new direct /tmp child; existing data will not be adopted or deleted')
    if not output.name.startswith('ioalab-default-'):
        raise ValueError('Use the explicit isolated experiment prefix')
    if ROOT.is_relative_to(output) or output.is_relative_to(ROOT):
        raise ValueError('Keep experiment state separate from the checkout')
    return output


def free_ports(count):
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for current in sockets: current.bind(('127.0.0.1', 0))
        return [current.getsockname()[1] for current in sockets]


class NoModel:
    """Only the model metadata endpoint exists; a production call fails the proof."""
    def __init__(self):
        self.calls = 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                value = {'object':'list', 'data':[{'id':'installation-fixture', 'object':'model'}]}
                encoded = json.dumps(value).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
            def do_POST(self):
                owner.calls += 1
                self.send_error(503, 'Model execution is outside this lifecycle proof')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start(); return self
    def __exit__(self, *_):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
    @property
    def url(self): return 'http://127.0.0.1:' + str(self.server.server_port) + '/v1'


def parsed(result):
    if result.returncode: raise RuntimeError('An isolated command failed; inspect its private original log')
    for line in reversed(result.stdout.splitlines()):
        try: value = json.loads(line)
        except ValueError: continue
        if isinstance(value, dict): return value
    raise RuntimeError('An isolated command returned no JSON receipt')


class Proof:
    def __init__(self, output, index, seconds):
        self.output, self.index, self.seconds = output, index, seconds
        self.layout = Layout(output/'runtime', output/'state', output/'config')
        self.configuration = output/'initial.json'
        self.report = {'format':'ioalab.default-deployment-proof/1', 'status':'running',
            'startedAt':datetime.now(timezone.utc).isoformat(), 'stage':'preflight',
            'scope':'real root identities, default commands, systemd/cgroups, installed core/SDK/native web, safe upgrade',
            'excludes':['model business work', 'mail delivery', 'native user confirmation', 'four new demo films'],
            'checks':{}}
        self.manager = None

    def checkpoint(self, stage, **values):
        self.report.update(stage=stage, **values); save(self.output/'report.json', self.report)
        # This explicit allowlist report contains no credentials, raw status or
        # business state. The non-root artifact uploader reads this file only.
        os.chmod(self.output/'report.json', 0o644)
        print(json.dumps({'stage':stage, 'status':self.report['status']}), flush=True)

    def command(self, argv, *, cwd=ROOT, role=None, env=None, body=None):
        context = {}; environment = child_environment()
        if role:
            import pwd
            account = pwd.getpwnam(role)
            context = {'user':account.pw_uid, 'group':account.pw_gid, 'extra_groups':[]}
            environment.update(HOME=account.pw_dir, USER=role, LOGNAME=role)
            cwd = Path(account.pw_dir)
        environment.update(env or {})
        result = subprocess.run(list(map(str, argv)), cwd=cwd, env=environment,
            input=json.dumps(body) if body is not None else '', capture_output=True,
            text=True, encoding='utf-8', timeout=120, **context)
        with (self.output/'commands.private.log').open('a', encoding='utf-8') as log:
            log.write(result.stdout); log.write(result.stderr)
        return parsed(result)

    def submit(self, action, source):
        return self.command(['bash', source/'deploy'/('install.sh' if action=='install' else 'upgrade.sh'),
            '--config', self.configuration, '--root', self.layout.root, '--state', self.layout.state,
            '--config-dir', self.layout.config], cwd=source,
            env={'PYTHON':sys.executable})

    def observe(self, source=ROOT):
        return self.command([sys.executable, '-X', 'utf8', '-B', '-m', 'deploy', 'status',
            '--state', self.layout.state], cwd=source)

    def await_task(self, original, source):
        deadline = time.monotonic() + self.seconds
        while True:
            live = self.observe(source); task = live['task']
            if task.get('operationId') != original['operationId']:
                raise RuntimeError('Another installation operation replaced the original one')
            if task['status'] == 'completed': return live
            if task['status'] != 'waiting':
                raise RuntimeError('The original installer is terminal but incomplete; retain its private records')
            if time.monotonic() > deadline:
                raise TimeoutError('Observation expired; the original task is not restarted or terminated')
            time.sleep(3)

    def reload_manager(self):
        self.metadata = read_json(self.layout.private/'installation.json')
        self.manager = Systemd(self.layout.inspect()['deploymentId'], self.metadata['units'], lambda: None)
        self.plans = units.plans(self.layout, administrator=True)

    def stop(self, key):
        name = self.plans[key]['unit']; before = self.manager.ready(name)
        self.manager.request_stop(name, before)
        deadline = time.monotonic() + 120
        while not self.manager.released(name, before):
            if time.monotonic() > deadline: raise TimeoutError('Original runtime writers have not released')
            time.sleep(.2)
        return before

    def official(self):
        python = self.layout.environments/'current/participant/bin/python'
        return self.command([python, '-I', '-m', 'ioalab_services', 'status', '--config',
            self.layout.config/'agents/official.json'], role='ioalab-agents',
            env=read_environment(self.layout.config/'agents/official.env'))

    def rpc(self, config, operation, arguments):
        code = '''import json,sys
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.ipc import LocalClient
c=RuntimeConfig.load(sys.argv[1]);live=LocalClient(c.home,timeout=2).call('runtime.status')['data']
reply=LocalClient(c.home,timeout=10,boot_id=live['bootId']).call(sys.argv[2],json.load(sys.stdin))
print(json.dumps(reply))
'''
        return self.command([self.layout.environments/'current/participant/bin/python', '-I', '-c', code,
            config, operation], role='ioalab-agents', body=arguments,
            env=read_environment(self.layout.config/'agents/official.env'))

    def installed_uid_checks(self):
        import pwd
        from deploy.environment import validation_code
        current = activation.current(self.layout)
        for unit, entry in current['units'].items():
            prefix = Path(entry['environment']); versions = read_json(prefix/'deployment.json')['versions']
            arguments = validation_code(unit, versions)
            arguments[1] += "\nprint(json.dumps({'status':'completed'}))\n"
            role = 'ioalab-core' if unit == 'core' else 'ioalab-agents'
            self.command([prefix/'bin/python', '-I', *arguments], role=role)
        for key, row in self.plans.items():
            live = self.manager.ready(row['unit'])
            expected = pwd.getpwnam(row['user']).pw_uid
            if live['ActiveState'] != 'active' or os.stat('/proc/'+live['MainPID']).st_uid != expected:
                raise RuntimeError('A running unit does not use its actual dedicated role UID')
        for path in (self.layout.private, self.layout.config/'core/platform.json'):
            code = "import json,os,sys; print(json.dumps({'status':'completed','readable':os.access(sys.argv[1],os.R_OK)}))"
            if self.command([self.layout.environments/'current/participant/bin/python', '-I', '-c', code, path],
                            role='ioalab-agents')['readable']:
                raise RuntimeError('A participant role can read private deployment/core credentials')
        self.report['checks']['dedicatedRoleExecutionAndPrivateBoundaries'] = True

    def upgrade_source(self):
        target = self.output/'upgrade-source'; target.mkdir(mode=0o700)
        ignored = shutil.ignore_patterns('__pycache__', '*.pyc', '.git', 'node_modules', '*.egg-info')
        for name in SOURCE_DIRECTORIES: shutil.copytree(ROOT/name, target/name, ignore=ignored)
        for name in SOURCE_FILES:
            path = target/name; path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ROOT/name, path)
        # Only the disposable copy changes. A different wheel proves a real set
        # switch; no user checkout, dependency declaration or protocol is altered.
        path = target/'src/platform/ioalab/__init__.py'
        with path.open('a', encoding='utf-8') as stream:
            stream.write('\n# Isolated lifecycle upgrade fixture: '+uuid.uuid4().hex+'\n')
        return target

    def watch_release(self, before, stopped, observed, errors):
        # Capture kernel emptiness while the old invocation is stopped. After
        # restart systemd can reuse its cgroup path for the new invocation.
        try:
            while not stopped.is_set():
                for name, original in before.items():
                    if observed.get(name): continue
                    live = self.manager.inspect(name)
                    if (live.get('ActiveState') in ('inactive','failed')
                            and live.get('InvocationID') in ('',original['InvocationID'])
                            and self.manager.empty(original['ControlGroup'])):
                        observed[name] = True
                if len(observed) == len(before): return
                stopped.wait(.02)
        except Exception as error: errors.append(type(error).__name__)

    def run(self, model):
        ports = free_ports(5); base = 'http://127.0.0.1:'+str(ports[0])
        from deploy.configure import initialize
        platform_path=initialize(self.layout)
        platform=read_json(platform_path)
        platform['runtime'].update(public_url=base,bind='127.0.0.1',port=ports[0],mode='dev')
        platform['llm'].update(model='installation-fixture',discovery_model='installation-fixture',base_url=model.url,
            api_key='isolated-lifecycle-not-a-production-key')
        save(platform_path,platform)
        save(self.configuration, {'platform_config':str(platform_path),'pypi_index':self.index,
            'swarm_ports':dict(zip(('agent_server','gateway','web','frontend'), ports[1:]))})
        assert stat.S_IMODE(self.configuration.stat().st_mode) == 0o600
        self.checkpoint('install')
        first = self.submit('install', ROOT)
        if first['status'] != 'waiting': raise RuntimeError('Fresh default install was not detached')
        again = self.submit('install', ROOT)
        if (again['operationId'],again['attempt'],again['unit']) != (first['operationId'],first['attempt'],first['unit']):
            raise RuntimeError('Repeated CLI submission created a second installer')
        initial = self.await_task(first, ROOT)
        self.reload_manager(); self.installed_uid_checks()
        ready = initial['lastReadiness']
        expected_services=sum(len(domain['services']) for domain in read_json(ROOT/'deploy/default-seed.json')['domains'])
        if not ready.get('runtimeReady') or ready['official']['availableServices'] != expected_services:
            raise RuntimeError('The default installer did not prove all official services ready')
        if not ready.get('nativeWeb') or not ready.get('administrator',{}).get('currentInterfaceQuerySucceeded'):
            raise RuntimeError('The original native web and administrator SDK were not proved')
        self.report['checks'].update(detachedOriginalTask=True, fullDefaultInstall=True,
            nativePrebuiltWeb=True, officialAccounts=8, officialServices=expected_services)
        before_status = self.official(); before_accounts = before_status['accounts']
        identities = {row['account']:row['runtime']['identities'] for row in before_accounts}
        chosen = before_accounts[0]; profile = chosen['services'][0]['profile']
        config = self.layout.state/'agents/official/accounts'/chosen['account']/'config.json'
        pause = self.rpc(config, 'provider.pause', {'profile':profile})
        if pause['status'] != 'completed' or pause['data']['currentPolicy']['accepting']:
            raise RuntimeError('The original provider pause was not accepted')
        self.stop('official')
        subprocess.run(['systemctl','disable',self.plans['official']['unit']], check=True,
            capture_output=True, text=True, timeout=30)
        before = self.manager.freeze([row['unit'] for row in self.plans.values()])
        credentials = sha(self.layout.private/'initial.json')
        original_set = activation.current(self.layout)
        source = self.upgrade_source()
        self.checkpoint('upgrade')
        stopped = threading.Event(); observed = {}; errors = []
        monitor = threading.Thread(target=self.watch_release, args=(before,stopped,observed,errors), daemon=True)
        monitor.start()
        try:
            request = self.submit('upgrade', source)
            self.await_task(request, source)
        finally:
            stopped.set(); monitor.join(35)
        if monitor.is_alive() or errors or set(observed) != set(before):
            raise RuntimeError('Independent kernel evidence of every original writer release is incomplete')
        self.reload_manager()
        current = activation.current(self.layout)
        if current['setId'] == original_set['setId']: raise RuntimeError('Upgrade did not switch to a different complete set')
        if current['release'] == original_set['release']: raise RuntimeError('Upgrade retained the old release resource anchor')
        if sha(self.layout.private/'initial.json') != credentials: raise RuntimeError('Upgrade changed initial credentials/configuration')
        for key,row in self.plans.items():
            live = self.manager.ready(row['unit']); old = before[row['unit']]
            if live['UnitFileState'] != old['UnitFileState']: raise RuntimeError('Upgrade changed boot enablement')
            if key == 'official':
                if live['ActiveState'] != 'inactive': raise RuntimeError('Upgrade restarted a manually stopped service')
            elif live['ActiveState'] != 'active' or not live['InvocationID'] or live['InvocationID'] == old['InvocationID']:
                raise RuntimeError('An originally running service was not actually restarted')
            if key == 'official' and not self.manager.empty(old['ControlGroup']):
                raise RuntimeError('Stopped original service still has a live control group')
        pointer = read_json(self.layout.private/'current-job.json')
        progress = read_json(self.layout.private/'jobs'/pointer['operationId']/'progress.json')
        archived, _ = backup.verify(Path(progress['upgrade']['backup']['path']))
        if archived['phase'] != 'ready' or archived['previousSet'] != original_set:
            raise RuntimeError('The original consistent backup is incomplete')
        self.report['checks'].update(changedSetActivated=True, privateInitialInputPreserved=True,
            manualStopAndBootEnablementPreserved=True, backupVerified=True,
            originalCgroupsObservedEmptyBeforeRestart=True)
        # Deliberately start the previously stopped service only after verifying
        # that upgrade preserved its manual stop; inspect original SDK identity
        # and policy through normal authenticated IPC, without replaying publish.
        self.manager.start(self.plans['official']['unit'])
        deadline = time.monotonic()+120
        while True:
            after = self.official()['accounts']
            if len(after)==8 and all(not row.get('error') and row.get('runtime',{}).get('platformConnected') for row in after): break
            if time.monotonic()>deadline: raise TimeoutError('Restored original official SDKs are not connected')
            time.sleep(1)
        if {row['account']:row['runtime']['identities'] for row in after} != identities:
            raise RuntimeError('Upgrade replaced original participant identities')
        policy = self.rpc(config, 'provider.status', {'profile':profile})
        if policy['data']['admissionPolicy']['accepting']: raise RuntimeError('Upgrade overrode the original owner pause')
        self.report['checks'].update(participantIdentitiesPreserved=True, ownerPausePreserved=True)
        if model.calls: raise RuntimeError('Lifecycle verification unexpectedly invoked model business work')
        self.checkpoint('drain')
        for group in units.STOP_GROUPS:
            for key in group: self.stop(key)
        from deploy.installation import execution_leases
        with execution_leases(self.layout, administrator=True): pass
        self.report['checks'].update(actualCgroupsEmptyAndSdkLocksReleased=True, modelExecutionCalls=0,
            sourceSnapshotStable=source_identity(ROOT)['digest']==self.source['digest'])
        if not self.report['checks']['sourceSnapshotStable']: raise RuntimeError('Original checkout inputs changed')
        self.checkpoint('completed', status='passed', finishedAt=datetime.now(timezone.utc).isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-runner', action='store_true', required=True,
                        help='explicitly acknowledge disposable root/systemd host; never a production machine')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--index', default='https://pypi.org/simple')
    parser.add_argument('--seconds', type=int, default=3600)
    args = parser.parse_args()
    if not args.index.startswith('https://') or not 60 <= args.seconds <= 5400:
        parser.error('Use an HTTPS package index and an observation budget of 60..5400 seconds')
    output = require_fresh_host(args.output); output.mkdir(mode=0o755); os.chmod(output,0o755)
    os.umask(0o077)
    proof = Proof(output, args.index, args.seconds)
    proof.source = source_identity(ROOT)
    try:
        with NoModel() as model: proof.run(model)
    except Exception as error:
        proof.checkpoint(proof.report['stage'], status='failed', errorType=type(error).__name__,
            recovery='retain_original_task_and_private_records; never restart on observation timeout')
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
