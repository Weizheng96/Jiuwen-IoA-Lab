"""Build reproducible Python wheels from isolated, explicitly declared source roots."""
import argparse
import fnmatch
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


def build_wheel(project, output):
    project, output = Path(project).resolve(), Path(output).resolve()
    if not (project / 'pyproject.toml').is_file():
        raise ValueError('A Python project directory is required')
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
        packages, expected = source_modules(source)
        with zipfile.ZipFile(wheel) as archive:
            actual = {name for name in archive.namelist() if name.endswith('.py') and name.split('/')[0] in packages}
        if actual != expected:
            raise RuntimeError('Built Python modules differ from the source snapshot')
        destination = output / wheel.name
        shutil.copy2(wheel, destination)
        return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('projects', nargs='+', type=Path)
    args = parser.parse_args()
    for project in args.projects:
        print(build_wheel(project, args.out), flush=True)


if __name__ == '__main__':
    main()
