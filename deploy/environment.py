"""Install or verify one immutable runtime environment from an audited release."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
import uuid
import venv

from .release import UNITS, current_target, digest, linked, load, official_catalog, pins, read_json, regular
from .processes import child_environment


def atomic(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def writer(path):
    if path.exists() and linked(path):
        raise ValueError('Installation lock cannot be a link')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'0')
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another installer holds this environment writer lock; inspect that original attempt') from None
        try:
            yield
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == 'nt':
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def mirror(value):
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use an HTTPS package mirror without embedded credentials')
    return value


def execute(python, args, folder, log):
    with log.open('ab') as output:
        subprocess.run([str(python), '-I', *map(str, args)], cwd=folder, env=child_environment(),
                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, check=True)


def python_in(environment):
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def native_paths(environment):
    if os.name != 'nt':
        return
    for path in environment.rglob('*'):
        if path.suffix.lower() in ('.pyd', '.dll') and len(str(path.absolute()).encode('utf-16-le')) // 2 > 240:
            raise RuntimeError('A Windows native library path is too long; preserve this attempt and choose a shorter --root')


def validation_code(unit, expected, catalog):
    checks = '''
import importlib.metadata as metadata
import json,re,sys
from pathlib import Path
canonical=lambda value:re.sub(r'[-_.]+','-',value).lower()
expected=json.loads(sys.argv[1])
actual={canonical(item.metadata['Name']):item.version for item in metadata.distributions()}
assert all(actual.get(name)==version for name,version in expected.items()), 'Installed versions differ from the exact release'
assert not set(actual)-set(expected)-{'pip','setuptools','wheel'}, 'Unexpected packages in isolated runtime'
'''
    if unit == 'core':
        checks += "from ioalab.config import Settings\nfrom ioalab.web.app import create_app\n"
    elif unit == 'participant':
        checks += ("from ioalab_sdk.runtime.resident import ResidentRuntime\n"
                   "from ioalab_jiuwen.process import ProcessHost\n"
                   "from ioalab_services.catalog import definitions\n"
                   "services=[item['id'] for item in definitions()]\n"
                   "assert sorted(services)==" + repr(sorted(identifier for ids in catalog.values() for identifier in ids)) + ", 'Installed official catalog differs from the release declaration'\n"
                   "from ioalab_admin_assistant.presets import load\nassert len(load(meetings=False)[1])==2\n")
    else:
        checks += ("import jiuwenswarm,openjiuwen,sqlalchemy\n"
                   "distribution=metadata.distribution('workswarm')\n"
                   "assert distribution.locate_file('jiuwenswarm/channels/web/frontend/dist/index.html').is_file()\n"
                   "assert any(item.name=='jiuwenswarm-process' for item in distribution.entry_points)\n")
    forbidden = sorted(set().union(*UNITS.values()) - UNITS[unit])
    checks += 'assert not set(actual).intersection(' + repr(forbidden) + "), 'Runtime roles are mixed'\n"
    checks += '''
roots={'ioalab','ioalab_sdk','ioalab_jiuwen','ioalab_services','ioalab_tools','ioalab_admin_assistant','a2c','a2x_registry','jiuwenswarm','openjiuwen'}
for name,module in tuple(sys.modules.items()):
    source=getattr(module,'__file__',None)
    if name.split('.')[0] in roots and source:
        assert Path(source).resolve().is_relative_to(Path(sys.prefix).resolve()), 'Runtime module came from outside its installed environment'
'''
    checks += "print('Exact versions, runtime imports and package boundaries verified')\n"
    return ['-c', checks, json.dumps(expected, sort_keys=True)]


class Environment:
    def __init__(self, release, unit, root):
        self.release = Path(release).absolute()
        self.document = load(self.release, require_target=True)
        if unit not in UNITS:
            raise ValueError('Unknown deployment unit')
        self.unit, self.plan = unit, self.document['units'][unit]
        self.identity = {'format': 'ioalab.deployment-environment/1', 'layout': 'compact-1', 'unit': unit, 'target': current_target(),
                         'python': {'version': sys.version, 'implementation': sys.implementation.cache_tag}, 'plan': self.plan}
        self.id = digest(self.identity)
        self.root = Path(root).absolute()
        prefix = Path(sys.prefix).resolve()
        if self.root.resolve().is_relative_to(prefix) or prefix.is_relative_to(self.root.resolve()):
            raise ValueError('Choose a dedicated environment root separate from this running interpreter')
        self.folder = self.root / (unit + '-' + self.id[:16])
        if os.name == 'nt' and len(str(self.folder / ('a-' + '0' * 16)).encode('utf-16-le')) // 2 > 145:
            raise ValueError('Choose a shorter --root for Windows native dependencies; the environment prefix must fit 145 UTF-16 characters')
        self.versions = pins(regular(self.release, self.plan['lock']['path']).read_text(encoding='utf-8'))
        self.versions.update({wheel['name']: wheel['version'] for wheel in self.plan['wheels']})

    def prepare_folder(self):
        if self.root.exists() and linked(self.root):
            raise ValueError('Environment root cannot be a link')
        self.root.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            self.folder.mkdir()
            created = True
        except FileExistsError:
            if linked(self.folder) or not self.folder.is_dir():
                raise ValueError('Environment group must be an owned regular directory') from None
        with writer(self.folder / 'writer.lock'):
            owner = self.folder / 'owner.json'
            if created:
                atomic(owner, self.identity)
            elif not owner.is_file() or linked(owner) or read_json(owner) != self.identity:
                raise RuntimeError('Existing environment has unknown ownership; preserve and inspect it')

    def state(self):
        path = self.folder / 'state.json'
        if not path.exists():
            return None
        value = read_json(regular(self.folder, 'state.json'))
        if (not isinstance(value, dict) or set(value) != {'environmentId', 'phase', 'attempt'}
                or value['environmentId'] != self.id or value['phase'] not in ('installing', 'failed', 'ready')
                or not isinstance(value['attempt'], str) or not re.fullmatch(r'a-[a-f0-9]{16}', value['attempt'])):
            raise RuntimeError('Installation state is unknown; preserve and inspect this environment')
        return value

    def validate(self, attempt, log):
        environment = self.folder / attempt
        if linked(environment) or not environment.is_dir():
            raise RuntimeError('Installed environment path has changed')
        native_paths(environment)
        python = python_in(environment)
        catalog = official_catalog(self.release, self.document) if self.unit == 'participant' else None
        execute(python, validation_code(self.unit, self.versions, catalog), self.folder, log)
        execute(python, ['-m', 'pip', '--isolated', 'check'], self.folder, log)
        return environment

    def ready(self, state, log):
        environment = self.folder / state['attempt']
        if linked(environment) or not environment.is_dir():
            raise RuntimeError('Installed environment path has changed')
        marker = read_json(regular(environment, 'deployment.json'))
        if marker != {'environmentId': self.id, 'identity': self.identity, 'versions': self.versions}:
            raise RuntimeError('Installed deployment identity has changed')
        if self.unit == 'participant':
            expected = {'format': 'ioalab.access-installation/1', 'releaseId': self.id, 'versions': self.versions}
            if read_json(regular(environment, 'ioalab-release.json')) != expected or regular(environment, '.ready').read_text(encoding='utf-8') != self.id:
                raise RuntimeError('SDK installation identity is incomplete or changed')
        elif regular(environment, '.ready').read_text(encoding='utf-8') != self.id:
            raise RuntimeError('Environment readiness identity has changed')
        self.validate(state['attempt'], log)
        if self.unit == 'participant':
            execute(python_in(environment), ['-c', "from ioalab_sdk.runtime.installation import current; import sys; assert current('ioalab-official')['releaseId']==sys.argv[1]", self.id], self.folder, log)
        return environment

    def install(self, *, index='https://pypi.tuna.tsinghua.edu.cn/simple', retry_failed=False, verify=False):
        index = mirror(index)
        self.prepare_folder()
        with writer(self.folder / 'writer.lock'):
            state = self.state()
            if state and state['phase'] == 'ready':
                environment = self.ready(state, self.folder / (state['attempt'] + '.verify.log'))
                return self.result(environment, reused=True)
            if verify:
                raise RuntimeError('No verified completed environment exists for this release')
            if state and state['phase'] == 'installing':
                raise RuntimeError('Original installation has no verified completion; its result is unknown and must be inspected')
            if state and state['phase'] == 'failed' and not retry_failed:
                raise RuntimeError('The original installation failed; inspect its log and explicitly retry with --retry-failed')
            if state is None and any(path.name not in ('owner.json', 'writer.lock') for path in self.folder.iterdir()):
                raise RuntimeError('Unrecorded installation files exist; preserve and inspect them')
            attempt = 'a-' + uuid.uuid4().hex[:16]
            environment, log = self.folder / attempt, self.folder / (attempt + '.log')
            state = {'environmentId': self.id, 'phase': 'installing', 'attempt': attempt}
            atomic(self.folder / 'state.json', state)
            print(self.unit + ': installing a new isolated environment', flush=True)
            try:
                venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
                python = python_in(environment)
                execute(python, ['-m', 'pip', '--isolated', 'install', '--disable-pip-version-check', '--no-input',
                                 '--index-url', index, '--only-binary', ':all:', '--no-deps', '-r',
                                 regular(self.release, self.plan['lock']['path'])], self.folder, log)
                local_lock = self.folder / (attempt + '.wheels.lock')
                local_lock.write_text(''.join(regular(self.release, value['path']).as_uri() + ' --hash=sha256:' + value['sha256'] + '\n'
                                              for value in self.plan['wheels']), encoding='utf-8')
                execute(python, ['-m', 'pip', '--isolated', 'install', '--disable-pip-version-check', '--no-input',
                                 '--no-index', '--no-deps', '--require-hashes', '-r', local_lock], self.folder, log)
                self.validate(attempt, log)
                if load(self.release, require_target=True) != self.document:
                    raise RuntimeError('Release inputs changed during installation')
                atomic(environment / 'deployment.json', {'environmentId': self.id, 'identity': self.identity, 'versions': self.versions})
                if self.unit == 'participant':
                    atomic(environment / 'ioalab-release.json', {'format': 'ioalab.access-installation/1', 'releaseId': self.id, 'versions': self.versions})
                (environment / '.ready').write_text(self.id, encoding='utf-8')
                state['phase'] = 'ready'
                self.ready(state, log)
                atomic(self.folder / 'state.json', state)
            except Exception:
                state['phase'] = 'failed'
                atomic(self.folder / 'state.json', state)
                raise RuntimeError('Installation failed; the original environment and log were preserved: ' + str(log)) from None
            return self.result(environment, reused=False)

    def result(self, environment, *, reused):
        return {'status': 'completed', 'unit': self.unit, 'releaseId': self.document['releaseId'],
                'environmentId': self.id, 'environment': str(environment), 'python': str(python_in(environment)), 'reused': reused}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True, type=Path)
    parser.add_argument('--unit', required=True, choices=UNITS)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--index', default='https://pypi.tuna.tsinghua.edu.cn/simple')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    result = Environment(args.release, args.unit, args.root).install(index=args.index, retry_failed=args.retry_failed, verify=args.verify)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
