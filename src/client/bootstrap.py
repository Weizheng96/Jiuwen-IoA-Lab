"""Install and verify the single Git-hosted client in its own Python environment."""
import argparse
import json
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import hashlib
import zipfile

REPOSITORY = 'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git'
DEVELOP_REPOSITORY = 'https://github.com/Weizheng96/Jiuwen-IoA-Lab.git'
HOST_EXTRAS = {'jiuwen': 'jiuwen', 'codex': 'codex', 'claude_code': 'claude-code'}
GIT_EXECUTABLE = 'git'
EXPLICIT_PROXY = None
PYTHON_PROBE = 'import json,sys; print(json.dumps([sys.implementation.name,list(sys.version_info[:2])]))'


class InstallFault(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def utf8_command(command):
    # -I ignores PYTHONUTF8: select the pipe encoding in each Python child itself.
    return [command[0], '-X', 'utf8', *command[1:]] if len(command)>1 and command[1]=='-I' else command


def run(command, log=None, *, binary=False):
    if command[0] == 'git':
        command = [GIT_EXECUTABLE, *(['-c', 'http.proxy='+EXPLICIT_PROXY] if EXPLICIT_PROXY else []), *command[1:]]
    if len(command) > 3 and command[1:4] == ['-I', '-m', 'pip'] and EXPLICIT_PROXY:
        command = [*command[:4], '--proxy', EXPLICIT_PROXY, *command[4:]]
    command = utf8_command(command)
    result = subprocess.run(command, capture_output=True, text=not binary, encoding=None if binary else 'utf-8',
                            env={key: value for key, value in os.environ.items()
                                 if key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE')})
    if log is not None:
        with Path(log).open('a',encoding='utf-8') as stream:
            stream.write(result.stdout+getattr(result,'stderr',''))
    if result.returncode:
        if not binary and ('pip' in command or any('commands=json.loads' in part for part in command)):
            detail = result.stdout + (getattr(result, 'stderr', '') or '')
            if re.search(r'requires a different Python:', detail, re.I):
                raise InstallFault('unsupported_python', 'Dependencies explicitly reject this Python; preserve install.log and select verified private Python 3.12 in an unused directory')
        # Keep detailed pip output local; URLs and environment may contain credentials.
        raise RuntimeError('Command failed: '+Path(command[0]).name+'; exit '+str(result.returncode))
    return result.stdout


def existing_capabilities(python, config, host, host_ref):
    """Probe the old public export into scratch space; never install native files."""
    result = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'status']))
    if result.get('status') != 'completed':
        raise InstallFault('installation_changed', 'Repair the original client integrity before adding a host')
    try:
        with tempfile.TemporaryDirectory(prefix='ioalab-capability-') as temporary:
            packet = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'skills',
                                     '--output', temporary, '--config', str(config),
                                     '--host', host, '--host-ref', host_ref or host]))
            if packet.get('status') != 'completed' or len(packet['data']['files']) != 2:
                raise ValueError('Both source-bound role exports are required')
            for filename in packet['data']['files']:
                path = Path(filename).resolve()
                if not path.is_relative_to(Path(temporary).resolve()): raise ValueError('Unexpected export location')
                with zipfile.ZipFile(path) as archive:
                    names = [name for name in archive.namelist() if name.endswith('/.ioalab-skill.json')]
                    if len(names) != 1: raise ValueError('Missing source binding manifest')
                    record = json.loads(archive.read(names[0]))
                    if (record.get('format') != 'ioalab.skill/2' or
                            record.get('sourceBinding') != {'hostType': host, 'hostRef': host_ref or host}):
                        raise ValueError('Original client lacks source-bound exports')
    except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile):
        raise InstallFault('explicit_upgrade_required', 'Original client lacks host_ref/sourceBinding support; explicitly stop and safely upgrade the same client while preserving its account and data; upgrade is not completed') from None
    return result


def checkout(ref, repository=REPOSITORY):
    root = Path(__file__).resolve().parents[2]
    origin = run(['git', '-C', str(root), 'remote', 'get-url', 'origin']).strip()
    if origin != repository:
        raise RuntimeError('Use a checkout from the selected official repository')
    if repository == DEVELOP_REPOSITORY:
        if not re.fullmatch('[0-9a-f]{40}', ref):
            raise RuntimeError('Development installs require the actual README source commit')
        commit = ref
        # The fixed source must belong to the explicitly fetched development lineage.
        run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, 'refs/remotes/origin/develop'])
        revisions = ('HEAD',)
    else:
        tags = run(['git', 'ls-remote', '--tags', repository, 'refs/tags/'+ref, 'refs/tags/'+ref+'^{}'])
        references = dict(line.split()[::-1] for line in tags.splitlines() if line.strip())
        commit = references.get('refs/tags/'+ref+'^{}', references.get('refs/tags/'+ref, ''))
        if not re.fullmatch('[0-9a-f]{40}', commit):
            raise RuntimeError('Requested published tag was not found')
        revisions = ('HEAD', 'refs/tags/'+ref+'^{commit}')
    for revision in revisions:
        if run(['git', '-C', str(root), 'rev-parse', revision]).strip() != commit:
            raise RuntimeError('The checkout does not match the selected fixed source')
    script = run(['git', '-C', str(root), 'show', commit+':src/client/bootstrap.py'], binary=True)
    if script.replace(b'\r\n', b'\n') != Path(__file__).read_bytes().replace(b'\r\n', b'\n'):
        raise RuntimeError('The bootstrap differs from its published Git object; preserve the checkout')
    return root, commit


def snapshot(root, commit, target):
    manifest = json.loads(run(['git', '-C', str(root), 'show', commit+':vendor/SOURCES.json']))
    paths = ['src/client', 'vendor/SOURCES.json', 'vendor/licenses']
    for item in manifest['components']:
        if item['id'] in ('a2c', 'jiuwenswarm-process-sdk'):
            path = Path('vendor')/item['wheelInfo']['file']
            if path.is_absolute() or '..' in path.parts:
                raise RuntimeError('Invalid audited dependency path')
            paths.append(path.as_posix())
    raw = run(['git', '-C', str(root), 'archive', '--format=tar', commit, *paths], binary=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            path = target/member.name
            if (not path.resolve().is_relative_to(target.resolve()) or
                    not (member.isfile() or member.isdir())):
                raise RuntimeError('Unsafe Git archive member')
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open('xb') as destination:
                    destination.write(source.read())
    return target/'src/client'


def check_python(python):
    implementation, version = json.loads(run([str(python), '-I', '-c', PYTHON_PROBE]))
    if version < [3, 11]:
        raise RuntimeError('Python >=3.11 is required; preserve the installation')
    return implementation, version


def base_python():
    base = Path(getattr(sys, '_base_executable', sys.executable))
    check_python(base)
    return base


def install_dependencies(root, commit, python, directory, host, *, config=None):
    """Build the fixed source without cache, then install its exact wheel and extra."""
    with tempfile.TemporaryDirectory(prefix='ioalab-client-source-') as temporary:
        temporary = Path(temporary)
        source = snapshot(root, commit, temporary/'source')
        wheels = temporary/'wheels'
        run([str(python), '-I', '-m', 'pip', 'wheel', '--disable-pip-version-check',
             '--no-cache-dir', '--no-deps', '--wheel-dir', str(wheels), str(source)], directory/'install.log')
        built = list(wheels.glob('ioalab_client-*.whl'))
        if len(built) != 1: raise RuntimeError('Expected one freshly built client wheel')
        wheel = built[0]
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        install = [str(python), '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                   '--no-cache-dir', str(wheel)+'['+HOST_EXTRAS[host]+']']
        if config is None:
            # A fresh client must come from this build even when versions are unchanged.
            run([str(python), '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                 '--no-cache-dir', '--force-reinstall', '--no-deps', str(wheel)], directory/'install.log')
            run(install, directory/'install.log')
            return {'installSource': wheel.as_uri(), 'wheelSha256': digest}
        # Same-version local wheel requests install missing extra dependencies while preserving
        # the original installed client RECORD/direct_url. The SDK owns the existing OS lease.
        if not config.exists():
            run(install, directory/'install.log')
            run([str(python), '-I', '-m', 'pip', 'check'])
            return {}
        script = ("import json,subprocess,sys; from ioalab_sdk.runtime import RuntimeConfig,Lease; "
                  "c=RuntimeConfig.load(sys.argv[1]); "
                  "lease=Lease(c.home/'client.lock',timeout=0); "
                  "commands=json.loads(sys.argv[2]); "
                  "[(subprocess.run(cmd,check=True)) for cmd in commands]")
        commands = [install, [str(python), '-I', '-m', 'pip', 'check']]
        if EXPLICIT_PROXY:
            commands = [[*cmd[:4], '--proxy', EXPLICIT_PROXY, *cmd[4:]] for cmd in commands]
        commands = [utf8_command(cmd) for cmd in commands]
        run([str(python), '-I', '-c', script, str(config), json.dumps(commands)], directory/'install.log')
        return {}



def install(directory, ref, output, host, config=None, *, repository=REPOSITORY, host_ref=None, reuse_installed=False):
    if sys.version_info[:2] < (3, 11): raise RuntimeError('Python >=3.11 is required')
    if host_ref and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', host_ref):
        raise ValueError('Invalid stable host reference')
    if host not in HOST_EXTRAS: raise ValueError('Select an explicit supported host')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', ref) or '..' in ref:
        raise ValueError('Invalid Git revision')
    directory = Path(directory).expanduser().absolute()
    if directory.is_symlink(): raise RuntimeError('Linked install directory is not allowed')
    directory = directory.resolve()
    if directory == Path(sys.prefix).resolve(): raise RuntimeError('Use a separate client environment')
    environment = directory/'env'
    python = environment/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    supplied_config = config is not None
    config = Path(config).expanduser().resolve() if config else directory/'settings.local.json'
    output = Path(output).expanduser().resolve() if output else directory/'role-skills'/(host_ref or host)
    if config.is_relative_to(environment) or output.is_relative_to(environment):
        raise ValueError('Keep account configuration and role skills outside the program environment')
    marker = directory/'ioalab-bootstrap.json'
    expected = {'format': 2, 'repository': repository, 'tag': ref}
    if directory.exists():
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError('Existing directory has no client ownership; preserve it and use a new directory')
        record = json.loads(marker.read_text(encoding='utf-8'))
        if ((not reuse_installed and any(record.get(key) != value for key, value in expected.items()))
                or record.get('phase') != 'completed'):
            raise RuntimeError('Existing installation differs or is incomplete; preserve it. Bind an existing client through its own skills-install CLI; upgrades require explicit safe migration')
        if reuse_installed:
            if (record.get('format') != 2 or record.get('repository') not in (REPOSITORY, DEVELOP_REPOSITORY)
                    or not re.fullmatch('[0-9a-f]{40}', record.get('commit', ''))):
                raise InstallFault('identity_conflict', 'Original installation has no verified official fixed source; preserve it')
            if not supplied_config:
                config = Path(record['configurationFile']).expanduser().resolve()
            if config.is_relative_to(environment): raise ValueError('Keep the original configuration outside the environment')
        if record.get('configurationFile') != str(config):
            raise RuntimeError('Reuse the original client configuration; do not rebind its account during installation')
    root, resolved = checkout(ref, repository)
    bootstrap_source = {'repository': repository, 'revision': ref, 'commit': resolved}
    if reuse_installed:
        if not directory.exists(): raise InstallFault('not_ready', 'Original client is missing; do not create another account')
        check_python(python)
        prior = existing_capabilities(python, config, host, host_ref)
        origin = prior['data'].get('source', {})
        if (origin.get('url') != record.get('installSource') or
                (record.get('wheelSha256') and origin.get('archive_info', {}).get('hashes', {}).get('sha256') != record['wheelSha256'])):
            raise InstallFault('installation_changed', 'Original client source differs from its installation ownership; preserve it before adding dependencies')
        original = record['commit']
        # Fetch only the recorded immutable object into the disposable source checkout.
        # Never fetch into or change a user's original checkout or default host.
        run(['git', '-C', str(root), 'fetch', '--depth', '1', record['repository'], original])
        if run(['git', '-C', str(root), 'rev-parse', original+'^{commit}']).strip() != original:
            raise InstallFault('identity_conflict', 'Original source object does not match its recorded commit')
        resolved = original
    if not directory.exists():
        directory.mkdir(parents=True)
        marker.write_text(json.dumps({**expected, 'selectedHost': host, 'phase': 'installing', 'commit': resolved}), encoding='utf-8')
        base = base_python()
        run([str(base), '-I', '-m', 'venv', str(environment)])
        check_python(python)
        installed = install_dependencies(root, resolved, python, directory, host)
        record = {**expected, 'selectedHost': host, 'installedHosts': [host],
                  'phase': 'installing', 'commit': resolved, **installed,
                  'configurationFile': str(config)}
        marker.write_text(json.dumps(record), encoding='utf-8')
    else:
        check_python(python)
        record = json.loads(marker.read_text(encoding='utf-8'))
        if host not in record.get('installedHosts', [record['selectedHost']]):
            # Refuse source drift before dependency mutation, keeping original identity.
            if record.get('commit') != resolved:
                raise RuntimeError('Keep the original installed source commit')
            installed = install_dependencies(root, resolved, python, directory, host,
                                             config=config)
            record.update(installed)
            run([str(python), '-I', '-m', 'pip', 'check'])
            # Preserve the last successful host set until all checks and exports complete.

    check_python(python)
    run([str(python), '-I', '-m', 'pip', 'check'])
    result = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'status']))
    if result.get('status') != 'completed': raise RuntimeError('Client verification did not complete')
    origin = result['data']['source']
    record = json.loads(marker.read_text(encoding='utf-8'))
    if (origin.get('url') != record.get('installSource') or
            origin.get('archive_info', {}).get('hashes', {}).get('sha256') != record.get('wheelSha256')):
        raise RuntimeError('Installed client origin differs from the requested repository')
    if record.get('commit') != resolved:
        raise RuntimeError('Installed commit changed; preserve the original environment')
    exported = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'skills', '--output', str(output), '--config', str(config), '--host', host, '--host-ref', host_ref or host]))
    if exported.get('status') != 'completed': raise RuntimeError('Skill export did not complete')
    marker.write_text(json.dumps({**record, 'phase': 'completed',
                                 'installedHosts': list(dict.fromkeys([*record.get('installedHosts', [record['selectedHost']]), host]))}), encoding='utf-8')
    native = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'skills-install', '--host', host,
                             '--output', str(output), '--config', str(config), '--host-ref', host_ref or host]))
    if native.get('status') not in ('completed', 'waiting'): raise RuntimeError('Native skill preparation did not complete; reuse this client and inspect skills-install')
    return {'status': 'completed', 'data': {**result['data'], 'files': exported['data']['files'],
            'clientDirectory': str(directory), 'programDirectory': str(environment), 'configurationFile': str(config),
            'nativeSkills': native['data'], 'checkout': {'repository': record['repository'], 'tag': record['tag'], 'selectedHost': record['selectedHost'], 'commit': resolved},
            'bootstrapSource': bootstrap_source,
            'nextAction': 'account_setup_then_verify_both_roles_and_new_session', 'connected': False}, 'error': None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=Path.home()/'.ioalab/client')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--config', type=Path, help='existing client configuration to preserve and bind')
    revision = parser.add_mutually_exclusive_group(required=True)
    revision.add_argument('--tag', help='published GitCode release tag')
    revision.add_argument('--commit', help='fixed official GitHub develop source commit')
    parser.add_argument('--git', default='git', help='verified Git executable')
    parser.add_argument('--proxy', help='explicit HTTP(S) proxy for this installation only')
    parser.add_argument('--reuse-installed', action='store_true', help='preserve the original official client source and supplement its host extra')
    parser.add_argument('--host-ref', help='stable host registration reference')
    parser.add_argument('--host', required=True, choices=HOST_EXTRAS, help='install only the selected host dependencies')
    args = parser.parse_args(argv)
    global GIT_EXECUTABLE, EXPLICIT_PROXY
    GIT_EXECUTABLE, EXPLICIT_PROXY = args.git, args.proxy
    try: result = install(args.install_dir, args.tag or args.commit, args.output, args.host, args.config,
                         repository=DEVELOP_REPOSITORY if args.commit else REPOSITORY, host_ref=args.host_ref,
                         reuse_installed=args.reuse_installed)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, tarfile.TarError) as error:
        result = {'status': 'rejected', 'data': {'connected': False,
                  'format': 'ioalab.onboarding/1',
                  'handoff': [
                      {'section': '接入状态', 'value': '失败；保留现场，尚未连接'},
                      {'section': '客户端位置', 'value': str(args.install_dir.expanduser().absolute())},
                      {'section': '账号与凭据', 'value': '未读取；通过原绑定account.setup本机页查看'},
                      {'section': '双角色Skill', 'value': '未核实；保留原安装'},
                      {'section': '宿主与通知', 'value': args.host+'；默认与通知未核实'},
                      {'section': '官网', 'value': '使用原配置的平台及同账号进入我的智能体；配置未读取'}]},
                  'error': {'code': getattr(error, 'code', 'installation_failed'), 'message': str(error),
                            'recovery': 'preserve_installation', 'installLog': str(args.install_dir.expanduser().absolute()/'install.log')}}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__': raise SystemExit(main())
