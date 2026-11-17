"""Build one client wheel, including the two audited unpublished dependencies."""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import zipfile

from setuptools import find_packages, find_namespace_packages, setup
from setuptools.command.build_py import build_py
from packaging.requirements import Requirement
from packaging.markers import Marker
import re

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PROJECTS = {'a2c': REPO/'src/a2c', 'ioalab-sdk': ROOT/'sdk', 'ioalab-jiuwen': ROOT/'hosts/jiuwen',
            'ioalab-codex': ROOT/'hosts/codex', 'ioalab-claude-code': ROOT/'hosts/claude_code'}
# A2C has one maintained source root. Copy an isolated build input inside this
# project so setuptools never publishes an absolute or out-of-project path.
(ROOT/'build').mkdir(exist_ok=True)
a2c_snapshot = tempfile.TemporaryDirectory(prefix='a2c-source-', dir=ROOT/'build')
dependencies = set()
packages = ['ioalab_client']
directories = {'ioalab_client': 'ioalab_client'}
package_data = {}
versions = {'ioalab-client': (ROOT/'VERSION').read_text().strip().removeprefix('v')}
for name, project in PROJECTS.items():
    versions[name] = (project/'VERSION').read_text().strip().removeprefix('v')
    config = tomllib.loads((project/'pyproject.toml').read_text())
    dependencies.update(value for value in config['project']['dependencies']
                        if not value.startswith(('ioalab-sdk', 'a2c', 'jiuwenswarm-process-sdk')))
    component = config.get('tool', {}).get('setuptools', {})
    declared_data = component.get('package-data', {})
    discovery = component.get('packages', {}).get('find', {})
    finder = find_namespace_packages if discovery.get('namespaces', True) else find_packages
    component_packages = {}
    for where in discovery.get('where', ['.']):
        source = (project/where).resolve()
        if not source.is_relative_to(project.resolve()):
            raise ValueError('Component package source escapes its project: '+name)
        for package in finder(str(source), include=discovery.get('include', ['*']),
                              exclude=discovery.get('exclude', [])):
            if package in directories or package in component_packages:
                raise ValueError('Duplicate component package: '+package)
            component_packages[package] = source/Path(*package.split('.'))
    for package in sorted(component_packages, key=lambda item: (item.count('.'), item)):
        source = component_packages[package]
        if name == 'a2c':
            target = Path(a2c_snapshot.name)/source.relative_to(project.resolve())
            if not target.exists():
                shutil.copytree(source, target,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'build', '*.egg-info'))
            source = target
        packages.append(package)
        directories[package] = source.relative_to(ROOT).as_posix()
        # TOML's '*' (or setup's '') belongs only to this component's packages.
        patterns = [*declared_data.get('*', []), *declared_data.get('', []),
                    *declared_data.get(package, [])]
        if patterns:
            package_data[package] = list(dict.fromkeys(patterns))
vendor = json.loads((REPO/'vendor/SOURCES.json').read_text(encoding='utf-8'))
wheels = []
for item in vendor['components']:
    if item['id'] != 'jiuwenswarm-process-sdk':
        continue
    info = item['wheelInfo']; path = REPO/'vendor'/info['file']
    if hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
        raise ValueError('Vendored dependency checksum mismatch: '+item['id'])
    versions[info['package']] = info['version']; wheels.append(path)
    with zipfile.ZipFile(path) as archive:
        meta = BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
        dependencies.update(value for value in meta.get_all('Requires-Dist', []) if 'extra ==' not in value)
# The committed lock is universal. Do not evaluate platform markers at build time.
pins = {}
base_requirements = set()
host_requirements = {name: set() for name in ('jiuwen', 'codex', 'claude_code')}
extra_guard = re.compile(r'(?:(.*) and )?extra == "(jiuwen|codex|claude-code)"$')
for line in (ROOT/'skills/_shared/requirements.lock').read_text(encoding='utf-8').splitlines():
    if not line.strip() or line.lstrip().startswith('#'): continue
    requirement = Requirement(line)
    versions_in_line = list(requirement.specifier)
    if requirement.url or len(versions_in_line) != 1 or versions_in_line[0].operator != '==' or '*' in versions_in_line[0].version:
        raise ValueError('Client lock requires exact versions: '+requirement.name)
    key = requirement.name.lower().replace('_', '-')
    pins.setdefault(key, []).append(requirement)
    if key in versions: continue
    marker = str(requirement.marker) if requirement.marker else ''
    match = extra_guard.fullmatch(marker)
    if match:
        host = match[2].replace('-', '_')
        requirement.marker = Marker(match[1]) if match[1] else None
        host_requirements[host].add(str(requirement))
    else:
        if 'extra' in marker: raise ValueError('Unexpected host guard in client lock')
        base_requirements.add(str(requirement))
for raw in dependencies:
    requirement = Requirement(raw)
    key = requirement.name.lower().replace('_', '-')
    if key not in pins or not any(next(iter(p.specifier)).version in requirement.specifier for p in pins[key]):
        raise ValueError('Client lock does not cover dependency: '+requirement.name)


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
        attribution = data/'licenses/a2c/NOTICE.md'
        attribution.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO/'src/a2c/NOTICE.md', attribution)
        shutil.copy2(REPO/'vendor/SOURCES.json', data/'SOURCES.json')
        shutil.copytree(REPO/'vendor/licenses', data/'licenses', dirs_exist_ok=True)


(ROOT/'build').mkdir(exist_ok=True)

setup(options={'egg_info': {'egg_base': 'build'}}, version=versions['ioalab-client'], install_requires=sorted(base_requirements), extras_require={host: sorted(values) for host,values in host_requirements.items()}, packages=packages,
      package_dir=directories, cmdclass={'build_py': BuildClient},
      package_data=package_data)

a2c_snapshot.cleanup()
