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

REPOSITORY = 'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git'
HOST_EXTRAS = {'jiuwen': 'jiuwen', 'codex': 'codex', 'claude_code': 'claude-code'}
PYTHON_PROBE = 'import json,sys; print(json.dumps([sys.implementation.name,list(sys.version_info[:2])]))'


def run(command, log=None, *, binary=False):
    result = subprocess.run(command, capture_output=True, text=not binary, encoding=None if binary else 'utf-8',
                            env={key: value for key, value in os.environ.items()
                                 if key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE')})
    if log is not None:
        with Path(log).open('a',encoding='utf-8') as stream:
            stream.write(result.stdout+getattr(result,'stderr',''))
    if result.returncode:
        # Keep detailed pip output local; URLs and environment may contain credentials.
        raise RuntimeError('Command failed: '+Path(command[0]).name+'; exit '+str(result.returncode))
    return result.stdout


def checkout(ref):
    root = Path(__file__).resolve().parents[2]
    origin = run(['git', '-C', str(root), 'remote', 'get-url', 'origin']).strip()
    if origin != REPOSITORY:
        raise RuntimeError('Use a checkout cloned from the official GitCode repository')
    tags = run(['git', 'ls-remote', '--tags', REPOSITORY, 'refs/tags/'+ref, 'refs/tags/'+ref+'^{}'])
    references = dict(line.split()[::-1] for line in tags.splitlines() if line.strip())
    commit = references.get('refs/tags/'+ref+'^{}', references.get('refs/tags/'+ref, ''))
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise RuntimeError('Requested GitCode tag was not found')
    for revision in ('HEAD', 'refs/tags/'+ref+'^{commit}'):
        if run(['git', '-C', str(root), 'rev-parse', revision]).strip() != commit:
            raise RuntimeError('The checkout does not match the requested published tag')
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
    if json.loads(run([str(python), '-I', '-c', PYTHON_PROBE])) != ['cpython', [3, 11]]:
        raise RuntimeError('Use a verified CPython 3.11 base interpreter; preserve the installation')


def install(directory, ref, output, host):
    if sys.version_info[:2] != (3, 11): raise RuntimeError('CPython 3.11 is required')
    if host not in HOST_EXTRAS: raise ValueError('Select an explicit supported host')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', ref) or '..' in ref:
        raise ValueError('Invalid Git revision')
    directory = Path(directory).expanduser().absolute()
    if directory.is_symlink(): raise RuntimeError('Linked install directory is not allowed')
    directory = directory.resolve()
    if directory == Path(sys.prefix).resolve(): raise RuntimeError('Use a separate client environment')
    python = directory/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    marker = directory/'ioalab-bootstrap.json'
    expected = {'format': 1, 'repository': REPOSITORY, 'tag': ref, 'selectedHost': host}
    if directory.exists():
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError('Existing directory has no client ownership; preserve it and use a new directory')
        record = json.loads(marker.read_text(encoding='utf-8'))
        if any(record.get(key) != value for key, value in expected.items()) or record.get('phase') != 'completed':
            raise RuntimeError('Existing installation differs or is incomplete; preserve it and use a new directory')
    root, resolved = checkout(ref)
    if not directory.exists():
        directory.mkdir(parents=True)
        marker.write_text(json.dumps({**expected, 'phase': 'installing', 'commit': resolved}), encoding='utf-8')
        # A copied host venv can report 3.11 while its unversioned base executable
        # points at another system Python. Create from the versioned base itself.
        base = Path(sys.base_prefix)/('python.exe' if os.name == 'nt' else 'bin/python3.11')
        check_python(base)
        run([str(base), '-I', '-m', 'venv', str(directory)])
        check_python(python)
        with tempfile.TemporaryDirectory(prefix='ioalab-client-source-') as temporary:
            source = snapshot(root, resolved, Path(temporary))
            install_source = source.as_uri()
            run([str(python), '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                 str(source)+'['+HOST_EXTRAS[host]+']'], directory/'install.log')
        record = {**expected, 'phase': 'installing', 'commit': resolved, 'installSource': install_source}
        marker.write_text(json.dumps(record), encoding='utf-8')
    check_python(python)
    run([str(python), '-I', '-m', 'pip', 'check'])
    result = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'status']))
    if result.get('status') != 'completed': raise RuntimeError('Client verification did not complete')
    origin = result['data']['source']
    record = json.loads(marker.read_text(encoding='utf-8'))
    if origin.get('url') != record.get('installSource') or origin.get('dir_info') != {}:
        raise RuntimeError('Installed client origin differs from the requested repository')
    if record.get('commit') != resolved:
        raise RuntimeError('Installed commit changed; preserve the original environment')
    exported = json.loads(run([str(python), '-I', '-m', 'ioalab_client', 'skills', '--output', str(Path(output).expanduser().resolve())]))
    if exported.get('status') != 'completed': raise RuntimeError('Skill export did not complete')
    marker.write_text(json.dumps({**record, 'phase': 'completed'}), encoding='utf-8')
    return {'status': 'completed', 'data': {**result['data'], 'files': exported['data']['files'],
            'checkout': {**expected, 'commit': resolved},
            'nextAction': 'native_import_and_select_role_skill', 'connected': False}, 'error': None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=Path.home()/'.ioalab/client')
    parser.add_argument('--output', type=Path, default=Path.home()/'.ioalab/role-skills')
    parser.add_argument('--tag', required=True, help='published GitCode release tag')
    parser.add_argument('--host', required=True, choices=HOST_EXTRAS, help='install only the selected host dependencies')
    args = parser.parse_args(argv)
    try: result = install(args.install_dir, args.tag, args.output, args.host)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, tarfile.TarError) as error:
        result = {'status': 'rejected', 'data': {'connected': False},
                  'error': {'code': 'installation_failed', 'message': str(error), 'recovery': 'preserve_installation'}}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__': raise SystemExit(main())
