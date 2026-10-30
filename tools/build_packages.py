"""Build Python wheels from isolated source snapshots, without stale local build outputs."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


IGNORED = ('build','dist','*.egg-info','__pycache__','*.pyc','.git','.venv')


def build_wheel(project,output):
    project,output = Path(project).resolve(),Path(output).resolve()
    if not (project/'pyproject.toml').is_file():
        raise ValueError('A Python project directory is required')
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ioalab-wheel-') as temporary:
        source = Path(temporary)/'source'
        shutil.copytree(project,source,ignore=shutil.ignore_patterns(*IGNORED))
        built = Path(temporary)/'wheels'
        subprocess.run([sys.executable,'-m','pip','wheel','--no-index','--no-deps','--no-build-isolation',
                        '-w',str(built),str(source)],check=True)
        wheels = list(built.glob('*.whl'))
        if len(wheels)!=1:
            raise RuntimeError('Expected one wheel for a source project')
        wheel = wheels[0]
        # These projects use regular packages. Verify the complete Python module list,
        # rather than assuming pip/setuptools discarded earlier build directories.
        packages = [path for path in source.iterdir() if path.is_dir() and (path/'__init__.py').is_file()]
        expected = {path.relative_to(source).as_posix() for package in packages for path in package.rglob('*.py')}
        with zipfile.ZipFile(wheel) as archive:
            actual = {name for name in archive.namelist() if name.endswith('.py') and name.split('/')[0] in {p.name for p in packages}}
        if actual!=expected:
            raise RuntimeError('Built Python modules differ from the source snapshot')
        destination = output/wheel.name
        shutil.copy2(wheel,destination)
        return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('projects',nargs='+',type=Path)
    args = parser.parse_args()
    for project in args.projects:
        print(build_wheel(project,args.out),flush=True)


if __name__=='__main__':
    main()
