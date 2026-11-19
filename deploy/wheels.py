"""Build reproducible Python wheels from isolated, explicitly declared source roots."""
import fnmatch
import email.parser
import hashlib
from packaging.version import Version
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile


IGNORED = ('build', 'dist', '*.egg-info', '__pycache__', '*.pyc', '.git', '.venv')


def source_modules(source):
    source = Path(source).resolve()
    settings = tomllib.loads((source / 'pyproject.toml').read_text(encoding='utf-8'))
    config = settings.get('tool', {}).get('setuptools', {})
    find = config.get('packages', {}).get('find', {})
    roots = find.get('where', [config.get('package-dir', {}).get('', '.')])
    include, exclude = find.get('include', ['*']), find.get('exclude', [])
    expected, packages = set(), set()
    for name in roots:
        root = (source / name).resolve()
        if not root.is_relative_to(source) or not root.is_dir():
            raise ValueError('Package source roots must stay inside the project snapshot')
        for package in root.iterdir():
            if not package.is_dir() or not (package / '__init__.py').is_file():
                continue
            if not any(fnmatch.fnmatchcase(package.name, pattern) for pattern in include):
                continue
            if any(fnmatch.fnmatchcase(package.name, pattern) for pattern in exclude):
                continue
            if package.name in packages:
                raise ValueError('Package names must have exactly one source root')
            packages.add(package.name)
            expected.update(path.relative_to(root).as_posix() for path in package.rglob('*.py'))
    if not packages:
        raise ValueError('No declared regular Python packages')
    return packages, expected


def verify_wheel(wheel, source):
    """Verify every published package byte and declared resource against one source snapshot."""
    source = Path(source).resolve()
    settings = tomllib.loads((source / 'pyproject.toml').read_text(encoding='utf-8'))
    config = settings.get('tool', {}).get('setuptools', {})
    roots = config.get('packages', {}).get('find', {}).get('where',
                    [config.get('package-dir', {}).get('', '.')])
    packages, expected_modules = source_modules(source)
    source_files = {}
    for name in roots:
        for package in packages:
            directory = source / name / package
            if directory.is_dir():
                for path in directory.rglob('*'):
                    if path.is_symlink():
                        raise ValueError('Package source links are not allowed')
                    if path.is_file() and not any(fnmatch.fnmatchcase(part, pattern)
                            for part in path.relative_to(directory).parts for pattern in IGNORED):
                        source_files[package + '/' + path.relative_to(directory).as_posix()] = path.read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(name.startswith('/') or '..' in Path(name).parts for name in names):
            raise RuntimeError('Unsafe or duplicate wheel member')
        payload = {name for name in names if name.split('/')[0] in packages and not name.endswith('/')}
        actual_modules = {name for name in payload if name.endswith('.py')}
        if actual_modules != expected_modules:
            raise RuntimeError('Built Python modules differ from the source snapshot')
        expected = set(expected_modules)
        package_data = config.get('package-data', {})
        for key, patterns in package_data.items():
            if key != '*' and key.split('.')[0] not in packages:
                raise ValueError('Package resource declaration names an unknown package')
            for root_name in roots:
                for package in packages:
                    directory = source / root_name / package
                    if not directory.is_dir():
                        continue
                    if key == '*':
                        locations = [directory, *(path.parent for path in directory.rglob('__init__.py')
                                                  if path.parent != directory)]
                    else:
                        locations = [source / root_name / Path(*key.split('.'))]
                    for location in locations:
                        for pattern in patterns:
                            if Path(pattern).is_absolute() or '..' in Path(pattern).parts:
                                raise ValueError('Unsafe package resource pattern')
                            for path in location.glob(pattern):
                                if path.is_file():
                                    expected.add(path.relative_to(source / root_name).as_posix())
        if not expected <= payload:
            raise RuntimeError('A declared package resource is missing from the wheel')
        for name in payload:
            if name not in source_files or archive.read(name) != source_files[name]:
                raise RuntimeError('Built package bytes differ from the source snapshot: ' + name)
        metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise RuntimeError('Expected one wheel metadata file')
        info = email.parser.BytesParser().parsebytes(archive.read(metadata[0]))
        if info['Name'] != settings['project']['name']:
            raise RuntimeError('Wheel project identity differs from its source')
        version = source / 'VERSION'
        if version.is_file() and Version(info['Version']) != Version(version.read_text().strip()):
            raise RuntimeError('Wheel version differs from its source VERSION')
        for pattern in config.get('license-files', []):
            for path in source.glob(pattern):
                candidates = [name for name in names if '.dist-info/' in name and
                              name.endswith('/' + path.name)]
                if len(candidates) != 1 or archive.read(candidates[0]) != path.read_bytes():
                    raise RuntimeError('Wheel attribution differs from its source: ' + path.name)
    return {'package': info['Name'], 'version': info['Version'],
            'sourceFiles': {name: hashlib.sha256(blob).hexdigest() for name, blob in sorted(source_files.items())}}


def build_wheel(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    if not (project / 'pyproject.toml').is_file():
        raise ValueError('A Python project directory is required')
    if any(path.is_symlink() for path in project.rglob('*') if not any(part in ('build', 'dist', '.venv', '.git', '__pycache__') for part in path.relative_to(project).parts)):
        raise ValueError('Package source links are not allowed')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ioalab-wheel-') as temporary:
        source = Path(temporary) / 'source'
        shutil.copytree(project, source, ignore=shutil.ignore_patterns(*IGNORED))
        built = Path(temporary) / 'wheels'
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation',
                        '-w', str(built), str(source)], check=True,
                       env={**os.environ, 'SOURCE_DATE_EPOCH': '315532800'})
        wheels = list(built.glob('*.whl'))
        if len(wheels) != 1:
            raise RuntimeError('Expected one wheel for a source project')
        wheel = wheels[0]
        verify_wheel(wheel, source)
        destination = output / wheel.name
        shutil.copy2(wheel, destination)
        return destination
