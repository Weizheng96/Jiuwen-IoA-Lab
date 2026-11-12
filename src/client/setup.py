"""Build one client wheel, including the two audited unpublished dependencies."""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import shutil
import tomllib
import zipfile

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PROJECTS = {'ioalab-sdk': ROOT/'sdk', 'ioalab-jiuwen': ROOT/'hosts/jiuwen',
            'ioalab-codex': ROOT/'hosts/codex'}
dependencies = set()
packages = ['ioalab_client']
directories = {'ioalab_client': 'ioalab_client'}
versions = {'ioalab-client': (ROOT/'VERSION').read_text().strip().removeprefix('v')}
for name, project in PROJECTS.items():
    versions[name] = (project/'VERSION').read_text().strip().removeprefix('v')
    config = tomllib.loads((project/'pyproject.toml').read_text())
    dependencies.update(value for value in config['project']['dependencies']
                        if not value.startswith(('ioalab-sdk', 'a2c', 'jiuwenswarm-process-sdk')))
    for package in find_packages(str(project)):
        packages.append(package)
        directories[package] = (project/Path(*package.split('.'))).relative_to(ROOT).as_posix()
vendor = json.loads((REPO/'vendor/SOURCES.json').read_text(encoding='utf-8'))
wheels = []
for item in vendor['components']:
    if item['id'] not in ('a2c', 'jiuwenswarm-process-sdk'):
        continue
    info = item['wheelInfo']; path = REPO/'vendor'/info['file']
    if hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
        raise ValueError('Vendored dependency checksum mismatch: '+item['id'])
    versions[info['package']] = info['version']; wheels.append(path)
    with zipfile.ZipFile(path) as archive:
        meta = BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
        dependencies.update(value for value in meta.get_all('Requires-Dist', []) if 'extra ==' not in value)
pins = {}
for line in (ROOT/'skills/_shared/requirements.lock').read_text(encoding='utf-8').splitlines():
    if line and not line.startswith('#'):
        name, version = line.split('==')
        pins[name.lower().replace('_', '-')] = (name, version)
extras = {}
for raw in dependencies:
    requirement = Requirement(raw)
    key = requirement.name.lower().replace('_', '-')
    if key not in pins or pins[key][1] not in requirement.specifier:
        raise ValueError('Client lock does not cover dependency: '+requirement.name)
    extras.setdefault(key, set()).update(requirement.extras)
dependencies = {name+('['+','.join(sorted(extras.get(key, ())))+']' if extras.get(key) else '')+'=='+version
                for key, (name, version) in pins.items() if key not in versions and key != 'ioalab-official'}


class BuildClient(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib)
        data = target/'ioalab_client/data'
        shutil.copytree(ROOT/'skills', data/'skills', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'build', '*.egg-info'))
        shutil.copy2(ROOT/'bootstrap.py', data/'bootstrap.py')
        (target/'ioalab_client/components.json').write_text(json.dumps(versions, sort_keys=True), encoding='utf-8')
        for path in wheels:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if '.dist-info/' not in name and not name.endswith('/'):
                        destination = target/name
                        if not destination.resolve().is_relative_to(target.resolve()):
                            raise ValueError('Unsafe vendored wheel path')
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(archive.read(name))
        shutil.copy2(REPO/'vendor/SOURCES.json', data/'SOURCES.json')
        shutil.copytree(REPO/'vendor/licenses', data/'licenses', dirs_exist_ok=True)


(ROOT/'build').mkdir(exist_ok=True)

setup(options={'egg_info': {'egg_base': 'build'}}, version=versions['ioalab-client'], install_requires=sorted(dependencies), packages=packages,
      package_dir=directories, cmdclass={'build_py': BuildClient},
      package_data={'ioalab_sdk': ['runtime/web/*.html'],
                    'ioalab_jiuwen': ['resources/ioa-notifications/*.py', 'resources/ioa-notifications/*.yaml']})
