"""A fixed isolated build environment; never a service runtime or editable install."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid
import venv

from .environment import mirror, python_in, writer
from .release import digest, pins, read_json, sha
from .workspace import path_without_links, save
from .processes import child_environment
from .layout import write_initial


def run(command, *, cwd, log):
    with Path(log).open('ab') as output:
        subprocess.run(list(map(str, command)), cwd=cwd, env=child_environment(), stdin=subprocess.DEVNULL,
                       stdout=output, stderr=subprocess.STDOUT, check=True)


class Builder:
    def __init__(self, root, lock):
        self.lock = Path(lock).absolute()
        path_without_links(self.lock)
        self.lock_bytes = self.lock.read_bytes()
        self.lock_text = self.lock_bytes.decode('utf-8')
        self.versions = pins(self.lock_text)
        if set(self.versions) != {'packaging', 'setuptools'}:
            raise ValueError('Use the fixed packaging/setuptools deployment build lock')
        self.identity = {'format': 'ioalab.build-environment/1', 'python': sys.version,
                         'lockSha256': sha(self.lock), 'versions': self.versions}
        self.id = digest(self.identity)
        self.folder = path_without_links(root) / self.id

    def validate(self, state, *, completed=True):
        attempt = state.get('attempt')
        if not isinstance(attempt, str) or not re.fullmatch(r'a-[a-f0-9]{16}', attempt):
            raise ValueError('Original builder attempt is invalid')
        prefix = self.folder / attempt
        path_without_links(prefix)
        if completed and (prefix / '.ready').read_text(encoding='utf-8') != self.id:
            raise ValueError('Builder completion identity is damaged; do not repair in place')
        if sha(self.folder / 'requirements.lock') != self.identity['lockSha256']:
            raise ValueError('Owned build lock changed; preserve it')
        python = python_in(prefix)
        code = '''import importlib.metadata as m,json,sys,sysconfig
from pathlib import Path
expected=json.loads(sys.argv[1])
site=Path(sysconfig.get_path('purelib')).resolve()
assert site.is_relative_to(Path(sys.prefix).resolve()), 'Build site escapes its environment'
actual={d.metadata['Name'].lower():d.version for d in m.distributions(path=[str(site)])}
assert all(actual.get(name)==version for name,version in expected.items()), 'Build tool versions differ'
assert set(actual)<=set(expected)|{'pip'}, 'Unexpected separately installed build tools'
import packaging,setuptools
assert all(Path(mod.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()) for mod in (packaging,setuptools)), 'Build imports escape their environment'
'''
        run([python, '-I', '-c', code, json.dumps(self.versions)], cwd=self.folder, log=self.folder / 'verify.log')
        run([python, '-I', '-m', 'pip', 'check'], cwd=self.folder, log=self.folder / 'verify.log')
        return python

    def ensure(self, index, *, retry_failed=False):
        index = mirror(index)
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        if self.folder.exists():
            path_without_links(self.folder)
            if read_json(self.folder / 'owner.json') != self.identity:
                raise ValueError('Build directory has different ownership')
        else:
            self.folder.mkdir()
            save(self.folder / 'owner.json', self.identity)
        with writer(self.folder / 'writer.lock'):
            write_initial(self.folder / 'requirements.lock', self.lock_bytes)
            state_path = self.folder / 'state.json'
            state = read_json(state_path) if state_path.exists() else None
            if state:
                if set(state) != {'phase', 'attempt'} or state['phase'] not in ('installing', 'failed', 'ready'):
                    raise ValueError('Unknown builder state; preserve it')
                if state['phase'] == 'ready': return self.validate(state)
                if state['phase'] == 'installing':
                    raise RuntimeError('Builder installation result is unknown; inspect the original attempt')
                if not retry_failed:
                    raise RuntimeError('Original builder failed; inspect its log and explicitly retry')
            elif {p.name for p in self.folder.iterdir()} - {'owner.json', 'writer.lock', 'requirements.lock'}:
                raise ValueError('Unrecorded builder files exist')
            state = {'phase': 'installing', 'attempt': 'a-' + uuid.uuid4().hex[:16]}
            prefix = self.folder / state['attempt']; log = self.folder / (state['attempt'] + '.log')
            save(state_path, state)
            try:
                venv.EnvBuilder(with_pip=True, system_site_packages=False).create(prefix)
                python = python_in(prefix)
                run([python, '-I', '-m', 'pip', '--isolated', 'install', '--no-input', '--disable-pip-version-check',
                     '--index-url', index, '--only-binary', ':all:', '--no-deps', '-r', self.folder / 'requirements.lock'], cwd=self.folder, log=log)
                self.validate(state, completed=False)
                (prefix / '.ready').write_text(self.id, encoding='utf-8')
                state['phase'] = 'ready'; save(state_path, state)
            except Exception:
                state['phase'] = 'failed'; save(state_path, state)
                raise RuntimeError('Build environment failed; preserve its attempt and inspect ' + str(log)) from None
            return python
