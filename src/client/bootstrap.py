"""Install and verify the single Git-hosted client in its own Python environment."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPOSITORY = 'https://github.com/ZhangRan-Zora/Jiuwen-IoA-Lab.git'
PYTHON_PROBE = 'import json,sys; print(json.dumps([sys.implementation.name,list(sys.version_info[:2])]))'


def run(command, log=None):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                            env={key: value for key, value in os.environ.items()
                                 if key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE')})
    if log is not None:
        with Path(log).open('a',encoding='utf-8') as stream:
            stream.write(result.stdout+getattr(result,'stderr',''))
    if result.returncode:
        # Keep detailed pip output local; URLs and environment may contain credentials.
        raise RuntimeError('Command failed: '+Path(command[0]).name+'; exit '+str(result.returncode))
    return result.stdout


def check_python(python):
    if json.loads(run([str(python), '-I', '-c', PYTHON_PROBE])) != ['cpython', [3, 11]]:
        raise RuntimeError('Use a verified CPython 3.11 base interpreter; preserve the installation')


def install(directory, ref, output):
    if sys.version_info[:2] != (3, 11): raise RuntimeError('CPython 3.11 is required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', ref) or '..' in ref:
        raise ValueError('Invalid Git revision')
    directory = Path(directory).expanduser().absolute()
    if directory.is_symlink(): raise RuntimeError('Linked install directory is not allowed')
    directory = directory.resolve()
    if directory == Path(sys.prefix).resolve(): raise RuntimeError('Use a separate client environment')
    python = directory/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    marker = directory/'ioalab-bootstrap.json'
    expected = {'format': 1, 'repository': REPOSITORY, 'tag': ref}
    if directory.exists():
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError('Existing directory has no client ownership; preserve it and use a new directory')
        record = json.loads(marker.read_text(encoding='utf-8'))
        if any(record.get(key) != value for key, value in expected.items()) or record.get('phase') != 'completed':
            raise RuntimeError('Existing installation differs or is incomplete; preserve it and use a new directory')
    else:
        tags = run(['git', 'ls-remote', '--tags', REPOSITORY, 'refs/tags/'+ref, 'refs/tags/'+ref+'^{}'])
        references = dict(line.split()[::-1] for line in tags.splitlines() if line.strip())
        resolved = references.get('refs/tags/'+ref+'^{}', references.get('refs/tags/'+ref, ''))
        if not re.fullmatch('[0-9a-f]{40}', resolved): raise RuntimeError('Requested GitHub tag was not found')
        directory.mkdir(parents=True)
        marker.write_text(json.dumps({**expected, 'phase': 'installing', 'commit': resolved}), encoding='utf-8')
        # A copied host venv can report 3.11 while its unversioned base executable
        # points at another system Python. Create from the versioned base itself.
        base = Path(sys.base_prefix)/('python.exe' if os.name == 'nt' else 'bin/python3.11')
        check_python(base)
        run([str(base), '-I', '-m', 'venv', str(directory)])
        check_python(python)
        requirement = 'ioalab-client @ git+'+REPOSITORY+'@'+resolved+'#subdirectory=src/client'
        run([str(python), '-I', '-m', 'pip', 'install', '--disable-pip-version-check', requirement], directory/'install.log')
    run([str(python), '-I', '-m', 'pip', 'check'])
    result = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'status']))
    if result.get('status') != 'completed': raise RuntimeError('Client verification did not complete')
    origin = result['data']['source']; vcs = origin.get('vcs_info', {})
    if origin.get('url') != REPOSITORY or origin.get('subdirectory') != 'src/client' or vcs.get('vcs') != 'git':
        raise RuntimeError('Installed client origin differs from the requested repository')
    commit = vcs.get('commit_id', '')
    if not re.fullmatch('[0-9a-f]{40}', commit): raise RuntimeError('Missing resolved Git commit')
    if vcs.get('requested_revision') != commit: raise RuntimeError('Installed Git revision was not pinned')
    if marker.exists() and json.loads(marker.read_text()).get('commit', commit) != commit:
        raise RuntimeError('Installed commit changed; preserve the original environment')
    exported = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'skills', '--output', str(Path(output).expanduser().resolve())]))
    if exported.get('status') != 'completed': raise RuntimeError('Skill export did not complete')
    marker.write_text(json.dumps({**expected, 'phase': 'completed', 'commit': commit}), encoding='utf-8')
    return {'status': 'completed', 'data': {**result['data'], 'files': exported['data']['files'],
            'nextAction': 'native_import_and_select_role_skill', 'connected': False}, 'error': None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=Path.home()/'.ioalab/client')
    parser.add_argument('--output', type=Path, default=Path.home()/'.ioalab/role-skills')
    parser.add_argument('--tag', required=True, help='published GitHub release tag')
    args = parser.parse_args(argv)
    try: result = install(args.install_dir, args.tag, args.output)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        result = {'status': 'rejected', 'data': {'connected': False},
                  'error': {'code': 'installation_failed', 'message': str(error), 'recovery': 'preserve_installation'}}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__': raise SystemExit(main())
