"""Install and verify the single Git-hosted client in its own Python environment."""
import argparse
import json
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import hashlib
import uuid
import zipfile

REPOSITORY = 'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git'
DEVELOP_REPOSITORY = 'https://github.com/Weizheng96/Jiuwen-IoA-Lab.git'
# Formal source: GitCode main; development verification: GitHub develop.
CHANNELS = {'main': REPOSITORY, 'develop': DEVELOP_REPOSITORY}
STOP_WAIT = 60
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


def run(command, log=None, *, binary=False, stdin=None):
    if command[0] == 'git':
        command = [GIT_EXECUTABLE, *(['-c', 'http.proxy='+EXPLICIT_PROXY] if EXPLICIT_PROXY else []), *command[1:]]
    if len(command) > 3 and command[1:4] == ['-I', '-m', 'pip'] and EXPLICIT_PROXY:
        command = [*command[:4], '--proxy', EXPLICIT_PROXY, *command[4:]]
    command = utf8_command(command)
    result = subprocess.run(command, capture_output=True, text=not binary, encoding=None if binary else 'utf-8',
                            env={key: value for key, value in os.environ.items()
                                 if key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE')},
                            **({'input': stdin} if stdin is not None else {}))
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


def official(url):
    """Compare clone origins as typed by users: with or without .git or a trailing slash."""
    url = url.strip().rstrip('/')
    return (url[:-4] if url.endswith('.git') else url).casefold()


def checkout(ref, repository=REPOSITORY, channel=None):
    """Verify the official (possibly --depth 1) clone this bootstrap runs from; the source is its HEAD commit."""
    root = Path(__file__).resolve().parents[2]
    origin = run(['git', '-C', str(root), 'remote', 'get-url', 'origin'])
    if official(origin) != official(repository) or (channel and CHANNELS[channel] != repository):
        raise RuntimeError('Run bootstrap from a clone of the selected official repository')
    if channel:
        if not re.fullmatch('[0-9a-f]{40}', ref):
            raise RuntimeError('Channel installs require the actual resolved full commit')
        commit = ref
        # The fixed source must belong to the cloned channel lineage; a shallow clone tip is its own ancestor.
        run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, 'refs/remotes/origin/'+channel])
    else:
        # A shallow clone need not hold local tag refs; the published tag must name the cloned HEAD.
        tags = run(['git', 'ls-remote', '--tags', repository, 'refs/tags/'+ref, 'refs/tags/'+ref+'^{}'])
        references = dict(line.split()[::-1] for line in tags.splitlines() if line.strip())
        commit = references.get('refs/tags/'+ref+'^{}', references.get('refs/tags/'+ref, ''))
        if not re.fullmatch('[0-9a-f]{40}', commit):
            raise RuntimeError('Requested published tag was not found')
    if run(['git', '-C', str(root), 'rev-parse', 'HEAD']).strip() != commit:
        raise RuntimeError('The clone HEAD does not match the selected fixed source')
    script = run(['git', '-C', str(root), 'show', commit+':src/client/bootstrap.py'], binary=True)
    if script.replace(b'\r\n', b'\n') != Path(__file__).read_bytes().replace(b'\r\n', b'\n'):
        raise RuntimeError('The bootstrap differs from its published Git object; preserve the checkout')
    return root, commit


def design_tag(repository, commit):
    """Record the design tag naming this exact commit when one is published; absence is not an error."""
    try: listing = run(['git', 'ls-remote', '--tags', repository, 'refs/tags/design-v*'])
    except RuntimeError: return None
    targets = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith('refs/tags/design-v'): continue
        name = parts[1][len('refs/tags/'):]
        # An annotated tag's peeled line names the commit itself.
        if name.endswith('^{}'): targets[name[:-3]] = parts[0]
        else: targets.setdefault(name, parts[0])
    matches = sorted(name for name, value in targets.items() if value == commit)
    return matches[-1] if matches else None


def source_lease(root):
    """Load the SDK's own OS lease from the verified fixed source; no second lock implementation."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('ioalab_fixed_lease', root/'src/client/sdk/ioalab_sdk/runtime/lease.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.Lease


def snapshot(root, commit, target):
    """Write the installer's fixed source paths from Git objects of the clone HEAD.

    Works in the one-sentence partial clone (--depth 1 --filter=blob:none --sparse): trees are local,
    so the needed blobs are listed first and fetched in one sparse-checkout batch. git archive is not
    used because it lazily fetches every blob of the commit (about 350MB) even with a pathspec.
    """
    # The only list of installer source paths; README_AGENT never repeats it.
    manifest = json.loads(run(['git', '-C', str(root), 'show', commit+':vendor/SOURCES.json']))
    paths = ['src/client', 'src/a2c', 'vendor/SOURCES.json', 'vendor/licenses']
    for item in manifest['components']:
        if item['id'] == 'jiuwenswarm-process-sdk':
            path = Path('vendor')/item['wheelInfo']['file']
            if path.is_absolute() or '..' in path.parts:
                raise RuntimeError('Invalid audited dependency path')
            paths.append(path.as_posix())
    entries = []
    for record in run(['git', '-C', str(root), 'ls-tree', '-r', '-z', '--full-tree', commit, '--', *paths]).split('\0'):
        if not record: continue
        meta, name = record.split('\t', 1)
        mode, kind, oid = meta.split()
        destination = target/name
        if (kind != 'blob' or mode not in ('100644', '100755') or not re.fullmatch('[0-9a-f]{40}', oid)
                or not destination.resolve().is_relative_to(target.resolve())):
            raise RuntimeError('Unsafe Git tree entry')
        entries.append((destination, oid))
    if any(not any(name == path or name.startswith(path+'/') for name in (d.relative_to(target).as_posix() for d, _ in entries))
           for path in paths):
        raise RuntimeError('The fixed source lacks an installer path')
    try: sparse = run(['git', '-C', str(root), 'config', '--get', 'core.sparseCheckout']).strip() == 'true'
    except RuntimeError: sparse = False
    if sparse:
        # Materialise exactly these paths (plus root files) in one batch; keeps the onboarding clone small.
        patterns = ['/*', '!/*/', *('/'+name+('' if name.endswith(('.json', '.whl')) else '/') for name in paths)]
        run(['git', '-C', str(root), 'sparse-checkout', 'set', '--no-cone', *patterns])
    batch = run(['git', '-C', str(root), 'cat-file', '--batch'], binary=True,
                stdin=''.join(oid+'\n' for _, oid in entries).encode('ascii'))
    offset = 0
    for destination, oid in entries:
        header_end = batch.index(b'\n', offset)
        header = batch[offset:header_end].split()
        if len(header) != 3 or header[0].decode() != oid or header[1] != b'blob':
            raise RuntimeError('Fixed source object is unavailable')
        start = header_end+1; offset = start+int(header[2])
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream: stream.write(batch[start:offset])
        offset += 1
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


def install_dependencies(root, commit, python, directory, hosts, *, config=None):
    """Build the fixed source without cache, then install its exact wheel and host extras."""
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
                   '--no-cache-dir', str(wheel)+'['+','.join(HOST_EXTRAS[host] for host in hosts)+']']
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



def stop_original(python, config, directory, root):
    """Stop the original client through its own runtime.stop, then prove no writer remains by holding its OS lock."""
    try: run([str(python), '-I', '-c', "from ioalab_sdk.runtime import CATALOG; assert 'runtime.stop' in CATALOG"])
    except RuntimeError:
        raise InstallFault('explicit_upgrade_required', 'The original client lacks a safe stop entry; preserve it and stop it through its own lifecycle before an explicit upgrade') from None
    if not config.exists(): return None
    # Only the original SDK parses the configuration to locate its data directory.
    home = json.loads(run([str(python), '-I', '-c', 'import json,sys; from ioalab_sdk.runtime import RuntimeConfig; '
                           'print(json.dumps(str(RuntimeConfig.load(sys.argv[1]).home)))', str(config)]))
    with tempfile.TemporaryDirectory(prefix='ioalab-upgrade-') as temporary:
        arguments = Path(temporary)/'stop.json'; arguments.write_text('{}', encoding='utf-8')
        try: run([str(python), '-I', '-m', 'ioalab_sdk.access', 'call', 'runtime.stop', '--config', str(config),
                  '--args-file', str(arguments)], directory/'install.log')
        except RuntimeError: pass  # Not running is fine; only the lock below proves the stop.
    try: return source_lease(root)(Path(home)/'client.lock', timeout=STOP_WAIT)
    except RuntimeError:
        raise InstallFault('not_ready', 'The original client still holds its data directory; the program was not changed. Let its work finish, then retry the explicit upgrade') from None


def upgrade_environment(directory, environment, python, marker, record, root, resolved, expected, config, hosts, source_extra):
    """Replace only the stopped program environment; account, configuration, data and defaults stay untouched."""
    check_python(python)
    lease = stop_original(python, config, directory, root)
    try:
        before = marker.read_bytes()
        previous = directory/('env.prev-'+uuid.uuid4().hex)
        marker.write_text(json.dumps({**record, 'phase': 'upgrading', 'previousEnvironment': previous.name}), encoding='utf-8')
        try: environment.rename(previous)
        except OSError:
            marker.write_bytes(before)
            raise InstallFault('not_ready', 'Program files are still in use; nothing was changed. Close processes using the client and retry') from None
        try:
            run([str(base_python()), '-I', '-m', 'venv', str(environment)])
            check_python(python)
            installed = install_dependencies(root, resolved, python, directory, hosts)
            run([str(python), '-I', '-m', 'pip', 'check'])
            upgraded = {**expected, 'selectedHost': record['selectedHost'], 'installedHosts': record.get('installedHosts', [record['selectedHost']]),
                        'phase': 'completed', 'commit': resolved, **installed, **source_extra, 'configurationFile': str(config),
                        'previousEnvironment': previous.name, 'upgradedFrom': record['commit']}
            marker.write_text(json.dumps(upgraded), encoding='utf-8')
            return upgraded
        except BaseException:
            shutil.rmtree(environment, ignore_errors=True)
            try: previous.rename(environment)
            except OSError:
                raise InstallFault('installation_failed', 'Upgrade failed and the original program remains preserved at '+str(previous)) from None
            marker.write_bytes(before)
            raise
    finally:
        if lease is not None: lease.close()


def install(directory, ref, output, host, config=None, *, repository=REPOSITORY, channel=None, host_ref=None,
            reuse_installed=False, upgrade=False):
    if sys.version_info[:2] < (3, 11): raise RuntimeError('Python >=3.11 is required')
    if host_ref and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', host_ref):
        raise ValueError('Invalid stable host reference')
    if host not in HOST_EXTRAS: raise ValueError('Select an explicit supported host')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', ref) or '..' in ref:
        raise ValueError('Invalid Git revision')
    if reuse_installed and upgrade: raise ValueError('Choose either reusing the original source or an explicit upgrade')
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
    expected = {'format': 2, 'repository': repository, 'tag': ref, 'channel': channel}
    recovering = False
    if directory.exists():
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError('Existing directory has no client ownership; preserve it and use a new directory')
        record = json.loads(marker.read_text(encoding='utf-8'))
        same_source = all(record.get(key) == value for key, value in expected.items())
        if record.get('phase') != 'completed':
            # Only an interrupted install of this same source that never received an account is resumed in place.
            recovering = (record.get('phase') == 'installing' and same_source and not reuse_installed and not upgrade
                          and record.get('configurationFile', str(config)) == str(config)
                          and not config.exists() and not config.with_suffix('.data').exists())
            if not recovering:
                raise RuntimeError('Existing installation is incomplete; preserve it. Only an interrupted install of the same source without configuration or data resumes in place')
        elif reuse_installed or upgrade:
            if (record.get('format') != 2 or record.get('repository') not in (REPOSITORY, DEVELOP_REPOSITORY)
                    or not re.fullmatch('[0-9a-f]{40}', record.get('commit', ''))):
                raise InstallFault('identity_conflict', 'Original installation has no verified official fixed source; preserve it')
            if not supplied_config:
                config = Path(record['configurationFile']).expanduser().resolve()
            if config.is_relative_to(environment): raise ValueError('Keep the original configuration outside the environment')
        elif not same_source:
            raise InstallFault('explicit_upgrade_required', 'The existing client uses another fixed source and is only verified without -Upgrade; rerun with -Upgrade to safely upgrade it while keeping account and data')
        if not recovering and record.get('configurationFile') != str(config):
            raise RuntimeError('Reuse the original client configuration; do not rebind its account during installation')
    root, resolved = checkout(ref, repository, channel)
    source_extra = {}
    if channel and (tag := design_tag(repository, resolved)): source_extra['designTag'] = tag
    bootstrap_source = {'repository': repository, 'revision': ref, 'channel': channel, 'commit': resolved, **source_extra}
    if recovering and record.get('commit') not in (None, resolved):
        raise RuntimeError('The interrupted installation used another commit; preserve it')
    upgraded = None
    if reuse_installed:
        if not directory.exists(): raise InstallFault('not_ready', 'Original client is missing; do not create another account')
        check_python(python)
        prior = existing_capabilities(python, config, host, host_ref)
        origin = prior['data'].get('source', {})
        if (origin.get('url') != record.get('installSource') or
                (record.get('wheelSha256') and origin.get('archive_info', {}).get('hashes', {}).get('sha256') != record['wheelSha256'])):
            raise InstallFault('installation_changed', 'Original client source differs from its installation ownership; preserve it before adding dependencies')
        original = record['commit']
        # Fetch only the recorded immutable object into the throwaway onboarding clone (no branch/HEAD change).
        # Never change the installed client or its default host.
        run(['git', '-C', str(root), 'fetch', '--depth', '1', record['repository'], original])
        if run(['git', '-C', str(root), 'rev-parse', original+'^{commit}']).strip() != original:
            raise InstallFault('identity_conflict', 'Original source object does not match its recorded commit')
        resolved = original
    if upgrade and not directory.exists():
        raise InstallFault('not_ready', 'No installed client to upgrade; install it first without -Upgrade')
    if not directory.exists() or recovering:
        directory.mkdir(parents=True, exist_ok=recovering)
        if recovering and (environment.exists() or environment.is_symlink()):
            # The interrupted environment is kept beside the client for diagnosis, never reused.
            environment.rename(directory/('env.prev-'+uuid.uuid4().hex))
        marker.write_text(json.dumps({**expected, 'selectedHost': host, 'phase': 'installing', 'commit': resolved}), encoding='utf-8')
        base = base_python()
        run([str(base), '-I', '-m', 'venv', str(environment)])
        check_python(python)
        installed = install_dependencies(root, resolved, python, directory, [host])
        record = {**expected, 'selectedHost': host, 'installedHosts': [host],
                  'phase': 'installing', 'commit': resolved, **installed, **source_extra,
                  'configurationFile': str(config)}
        marker.write_text(json.dumps(record), encoding='utf-8')
    elif upgrade and record['commit'] != resolved:
        hosts = list(dict.fromkeys([*record.get('installedHosts', [record['selectedHost']]), host]))
        record = upgraded = upgrade_environment(directory, environment, python, marker, record, root, resolved,
                                                expected, config, hosts, source_extra)
    else:
        check_python(python)
        record = json.loads(marker.read_text(encoding='utf-8'))
        if host not in record.get('installedHosts', [record['selectedHost']]):
            # Refuse source drift before dependency mutation, keeping original identity.
            if record.get('commit') != resolved:
                raise RuntimeError('Keep the original installed source commit')
            installed = install_dependencies(root, resolved, python, directory, [host], config=config)
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
    # skills-install exports both roles and replaces only this client's own unmodified copies of the same binding.
    native = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'skills-install', '--host', host,
                             '--output', str(output), '--config', str(config), '--host-ref', host_ref or host]))
    if native.get('status') not in ('completed', 'waiting'): raise RuntimeError('Skill export or native preparation did not complete; reuse this client and inspect skills-install')
    marker.write_text(json.dumps({**record, 'phase': 'completed',
                                 'installedHosts': list(dict.fromkeys([*record.get('installedHosts', [record['selectedHost']]), host]))}), encoding='utf-8')
    data = {**result['data'], 'files': [item['package'] for item in native['data']['skills']],
            'clientDirectory': str(directory), 'programDirectory': str(environment), 'configurationFile': str(config),
            'nativeSkills': native['data'], 'checkout': {'repository': record['repository'], 'tag': record['tag'], 'channel': record.get('channel'),
            'selectedHost': record['selectedHost'], 'commit': resolved}, 'bootstrapSource': bootstrap_source, 'resolvedCommit': bootstrap_source['commit'],
            'sourceClone': {'path': str(root), 'removable': True,
                            'instruction': 'The installed client no longer uses this clone; it may be deleted. To upgrade later, clone again and rerun with -Upgrade.'},
            'nextAction': 'account_setup_then_verify_both_roles_and_new_session', 'connected': False}
    if upgraded:
        data['upgrade'] = {'from': upgraded['upgradedFrom'], 'to': resolved, 'previousEnvironment': str(directory/upgraded['previousEnvironment']),
                           'entryBinding': 'migration_pending',
                           'skillsPendingReinstall': [item for item in upgraded['installedHosts'] if item != host]}
    return {'status': 'completed', 'data': data, 'error': None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=Path.home()/'.ioalab/client')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--config', type=Path, help='existing client configuration to preserve and bind')
    revision = parser.add_mutually_exclusive_group(required=True)
    revision.add_argument('--tag', help='published GitCode release tag')
    revision.add_argument('--commit', help='actual fixed commit resolved from the selected channel')
    parser.add_argument('--channel', choices=CHANNELS, help='main: GitCode formal source; develop: GitHub development verification')
    parser.add_argument('--git', default='git', help='verified Git executable')
    parser.add_argument('--proxy', help='explicit HTTP(S) proxy for this installation only')
    lifecycle = parser.add_mutually_exclusive_group()
    lifecycle.add_argument('--reuse-installed', action='store_true', help='preserve the original official client source and supplement its host extra')
    lifecycle.add_argument('--upgrade', action='store_true', help='explicitly and safely upgrade the existing client program to this fixed source')
    parser.add_argument('--host-ref', help='stable host registration reference')
    parser.add_argument('--host', required=True, choices=HOST_EXTRAS, help='install only the selected host dependencies')
    args = parser.parse_args(argv)
    if bool(args.commit) != bool(args.channel): parser.error('--commit requires --channel; a published --tag takes no channel')
    global GIT_EXECUTABLE, EXPLICIT_PROXY
    GIT_EXECUTABLE, EXPLICIT_PROXY = args.git, args.proxy
    try: result = install(args.install_dir, args.tag or args.commit, args.output, args.host, args.config,
                         repository=CHANNELS[args.channel] if args.commit else REPOSITORY, channel=args.channel,
                         host_ref=args.host_ref, reuse_installed=args.reuse_installed, upgrade=args.upgrade)
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
