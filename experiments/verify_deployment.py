"""Verify installed Linux release boundaries and HTTP/WS lifecycle, without a model or mail."""
from __future__ import annotations

import argparse
from decimal import Decimal
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.activation import activate, current, prepare_set
from deploy.builder import Builder, run
from deploy.environment import Environment, writer
from deploy.layout import Layout
from deploy.processes import child_environment
from deploy.release import load, sha
from deploy.workspace import Workspace, save


def http(base, path, body=None, *, cookie=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = Request(base + path, data=data, headers={'Content-Type':'application/json'})
    if cookie: request.add_header('Cookie', cookie)
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)



def seed_accounts(base, password):
    """Bootstrap administrator is a separate cookie channel, not a user account."""
    cookies = CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(cookies))
    def post(path, value):
        request = Request(base + path, data=json.dumps(value).encode('utf-8'),
                          headers={'Content-Type':'application/json'})
        with opener.open(request, timeout=15) as response:
            return json.load(response)
    post('/api/admin/login', {'password':password})
    if not any(cookie.name == 'ioalab_session' for cookie in cookies):
        raise RuntimeError('Bootstrap administrator did not receive its required session cookie')
    return post('/api/admin/seed', {})


def rpc(python, home, operation, arguments=None):
    code = '''import json,sys
from ioalab_sdk.runtime.ipc import LocalClient
print(json.dumps(LocalClient(sys.argv[1],timeout=2).call(sys.argv[2],json.loads(sys.argv[3]))))
'''
    result = subprocess.run([str(python), '-I', '-c', code, str(home), operation, json.dumps(arguments or {})],
                            cwd=home.parent, env=child_environment(), capture_output=True, timeout=12)
    if result.returncode: raise RuntimeError('Installed SDK RPC unavailable; inspect the original process')
    return json.loads(result.stdout)


def account_login(python, config, home):
    result = subprocess.run([str(python), '-I', '-m', 'ioalab_sdk.runtime', 'login', '--config', str(config)],
                            cwd=Path(home).parent, env=child_environment(), capture_output=True, timeout=25)
    if result.returncode: raise RuntimeError('Installed explicit login failed; retain its original account state')
    value = json.loads(result.stdout)
    if value['status'] != 'completed' or not value['data']['authenticated']:
        raise RuntimeError('Installed explicit login did not prove authentication')
    return value['data']


def invalidate_verification_credential(python, config, home, log):
    # Only the new isolated experiment calls this SDK-owned fault; no product backdoor.
    code = """import sys
from ioalab_sdk.lease import Lease
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.session import AccountSession
config=RuntimeConfig.load(sys.argv[1])
with Lease(config.home/'client.lock',timeout=0):
    session=AccountSession(config.platform,config.username,config.password,config.home,connect=False)
    try:
        session.account.update(token='invalid-isolated-verification-credential',expiresAt='2000-01-01T00:00:00Z')
        session.cache.put('session','current',session.account)
    finally:session.close()
"""
    run([python, '-I', '-c', code, config], cwd=Path(home).parent, log=log)


def await_status(python, home, child, timeout=45):
    until = time.monotonic() + timeout
    while True:
        if child.poll() is not None: raise RuntimeError('Installed SDK exited; inspect its original log')
        try:
            status = rpc(python, home, 'runtime.status')['data']
            if status['platformConnected']: return status
        except (RuntimeError, subprocess.TimeoutExpired): pass
        if time.monotonic() > until: raise RuntimeError('SDK readiness still unproved; retain original process')
        time.sleep(.2)


def exercise(layout, release, installations, report):
    document = load(release, require_target=True)
    candidate = prepare_set(layout, release, installations)
    assert current(layout) is None
    assert activate(layout, candidate, None)['changed']
    assert not activate(layout, candidate, None)['changed']
    report['setId'] = candidate['setId']
    stable = layout.environments/'current'
    core_python = stable/'core/bin/python'
    sdk_python = stable/'participant/bin/python'
    home = layout.state/'resident'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    base = 'http://127.0.0.1:' + str(port)
    env = {**child_environment(), 'IOALAB_MODE':'dev', 'IOALAB_BIND':'127.0.0.1', 'IOALAB_PORT':str(port),
           'IOALAB_PUBLIC_URL':base, 'IOALAB_DATA':str(layout.state/'core'), 'IOALAB_AUTOSEED':'0',
           'IOALAB_SEED_FILE':str(Path(release)/document['assets']['seed']['path']),
           'IOALAB_ADMIN_PASSWORD':'isolated-verification-password', 'IOALAB_LOGIN_CREDIT':'100', 'A2C_CACHE_DIR':str(layout.state/'cache')}
    sdk = None; status = None
    with (layout.private/'core.log').open('w', encoding='utf-8') as core_log:
        core = subprocess.Popen([str(core_python), '-I', '-m', 'ioalab', 'serve'],
                                cwd=layout.state, env=env, stdout=core_log, stderr=core_log)
        try:
            until = time.monotonic() + 60
            while True:
                if core.poll() is not None: raise RuntimeError('Installed core exited; inspect core.log')
                try: http(base, '/api/config'); break
                except OSError:
                    if time.monotonic() > until: raise
                    time.sleep(.2)
            user = 'deployment-verification-' + uuid.uuid4().hex[:10]
            registered = http(base, '/api/auth/register', {'username':user, 'password':'verification-password',
                                                         'operationId':str(uuid.uuid4())})
            cookie = 'ioalab_session=' + registered['token']
            config = layout.config/'resident.json'
            save(config, {'platform_url':base, 'username':user, 'password':'verification-password',
                          'data_dir':str(home), 'identities':[{'key':'personal', 'role':'assistant',
                                                             'name':'Isolated verification assistant'}]})
            initial = account_login(sdk_python, config, home)
            assert initial['newLogin']
            identities = None; boots = []; balances = []
            for attempt in range(3):
                with (layout.private/('sdk-' + str(attempt) + '.log')).open('w', encoding='utf-8') as sdk_log:
                    sdk = subprocess.Popen([str(sdk_python), '-I', '-m', 'ioalab_sdk.runtime', 'serve', '--config', str(config)],
                                           cwd=layout.state, env=child_environment(), stdout=sdk_log, stderr=sdk_log)
                    status = await_status(sdk_python, home, sdk)
                    assert status['installation']['releaseId'] == installations['participant']['environmentId']
                    if identities is None: identities = status['identities']
                    else: assert status['identities'] == identities
                    boots.append(status['bootId']); balances.append(http(base, '/api/me', cookie=cookie)['wallet'])
                    stopped = rpc(sdk_python, home, 'runtime.stop', {'bootId':status['bootId']})
                    assert stopped['status'] in ('waiting', 'completed')
                    assert sdk.wait(timeout=30) == 0
                if attempt == 0:
                    invalidate_verification_credential(sdk_python, config, home, layout.private/'credential-fault.log')
                    restored = account_login(sdk_python, config, home)
                    repeated = account_login(sdk_python, config, home)
                    assert restored['newLogin'] and not repeated['newLogin']
                    assert restored['operationId'] == repeated['operationId']
            assert len(set(boots)) == 3 and balances[1] == balances[2]
            assert Decimal(balances[1]) == Decimal(balances[0]) + Decimal('100')
            assert not (home/'runtime.json').exists()
            code = '''import sys
from pathlib import Path
from ioalab_sdk.lease import Lease
with Lease(Path(sys.argv[1])/'client.lock',timeout=0):pass
'''
            run([sdk_python, '-I', '-c', code, home], cwd=layout.state, log=layout.private/'released.log')
            # Explicit isolated seeding: the core never runs an internal assistant.
            seeded=seed_accounts(base,'isolated-verification-password')
            assert seeded['isc']>=1
            command=[str(sdk_python),'-I',str(ROOT/'experiments/verify_admin_assistant.py'),
                     '--output',str(layout.state/'admin-preset-probe'),
                     '--swarm-python',str(installations['swarm']['python']),'--platform',base]
            if os.geteuid()!=0:command=['sudo','-n','--',*command]
            probe=subprocess.run(command,cwd=layout.state,env=child_environment(),capture_output=True,text=True,
                                 encoding='utf-8',timeout=240)
            (layout.private/'administrator-probe.log').write_text(probe.stdout+probe.stderr,encoding='utf-8')
            if probe.returncode:raise RuntimeError('Installed Linux root administrator preset probe failed; inspect its bounded result')
            report['administratorPreparation']=json.loads(probe.stdout.splitlines()[-1])
            assert report['administratorPreparation']['status']=='passed'
            report['runtime'] = {'HTTP':True, 'WebSocket':True, 'stableActivatedEntry':True,
                                 'sameIdentityAfterRestart':True, 'sameCreditAfterRestart':True,
                                 'explicitLoginRecovery':True, 'reauthenticationCreditedOnce':True,
                                 'originalExecutionLockReleased':True}
        finally:
            if sdk is not None and sdk.poll() is None:
                if status is not None:
                    try: rpc(sdk_python, home, 'runtime.stop', {'bootId':status['bootId']})
                    except (RuntimeError, subprocess.TimeoutExpired): pass
                sdk.wait(timeout=30)
            if core.poll() is None: core.terminate()
            core.wait(timeout=25)
    # Configuration-only original APIs, deliberately without a live model or production job.
    workspace = Workspace(installations['swarm']['python'], layout.state/'swarm', layout.inspect()['deploymentId'])
    result = workspace.prepare(None)
    assert result['status'] == 'not_ready'
    report['originalSwarm'] = {'installed':True, 'ownedWorkspacePrepared':True, 'missingModelNotReady':True}
    report['releaseId'] = document['releaseId']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--index', default='https://pypi.tuna.tsinghua.edu.cn/simple')
    args = parser.parse_args()
    if sys.platform != 'linux' or sys.version_info[:2] != (3, 11):
        parser.error('Use Linux x86_64 CPython 3.11; Windows results do not prove Linux deployment')
    output = args.output.absolute()
    if output.exists() and any(output.iterdir()):
        parser.error('Use a new dedicated output; existing evidence and processes are preserved')
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    layout = Layout(output/'runtime', output/'state', output/'configuration')
    report = {'status':'running', 'scope':'Linux installed release and native configuration; not full systemd deployment',
              'callsModel':False, 'sendsMail':False, 'installsGitDependencies':False}
    try:
        layout.claim()
        with writer(layout.private/'deployment.lock'):
            builder = Builder(layout.root/'build', ROOT/'deploy/locks/build.lock')
            python = builder.ensure(args.index)
            report['builderId'] = builder.id
            run([python, '-m', 'tools.build_deployment', '--target', 'linux-x86_64', '--out', layout.root/'releases'],
                cwd=ROOT, log=layout.private/'build.log')
            folders = [path for path in (layout.root/'releases').iterdir() if path.is_dir() and len(path.name) == 64]
            if len(folders) != 1: raise RuntimeError('The release result is ambiguous')
            installations = {unit:Environment(folders[0], unit, layout.environments).install(index=args.index)
                             for unit in ('core', 'participant', 'swarm')}
            report['units'] = {unit:result['environmentId'] for unit, result in installations.items()}
            exercise(layout, folders[0], installations, report)
            report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', errorType=type(exc).__name__)
        raise
    finally:
        save(output/'report.json', report)
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
