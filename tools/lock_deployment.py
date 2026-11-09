"""Maintainer-only command: resolve ordinary binary dependencies for one production target."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from deploy.release import TARGETS, UNITS, pins, sha
from deploy.dependencies import inputs


def mirror(value):
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use an HTTPS package mirror without embedded credentials')
    return value


def write_lock(path, versions):
    # Content-addressed text must survive Git's LF normalization on every host.
    Path(path).write_bytes(''.join(name + '==' + versions[name] + '\n' for name in sorted(versions)).encode('utf-8'))


def resolve(wheels, output, target, *, index, constraints=None, resolver='uv'):
    index = mirror(index)
    executable = shutil.which(resolver)
    if not executable:
        raise ValueError('Maintainer lock generation requires uv; deployment installation does not')
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(('UV_', 'PIP_', 'PYTHON'))}
    env.update(PYTHONUTF8='1', UV_PYTHON_DOWNLOADS='never')
    version = subprocess.check_output([executable, '--version'], env=env, text=True).strip()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    wheel_paths = sorted(Path(wheels).glob('*.whl'))
    for unit in UNITS:
        plan = inputs(unit, target, wheel_paths)
        with tempfile.TemporaryDirectory(prefix='ioa-lock-') as temporary:
            folder = Path(temporary)
            direct = folder / 'requirements.in'
            locked = folder / 'requirements.lock'
            direct.write_text('\n'.join(plan['requirements']) + '\n', encoding='utf-8')
            command = [executable, 'pip', 'compile', str(direct), '--python-platform',
                       'linux', '--python-version', '3.11',
                       '--python', sys.executable, '--only-binary', ':all:', '--default-index', index,
                       '--native-tls', '--no-header', '--no-annotate', '--output-file', str(locked)]
            if constraints:
                command.extend(['--constraints', str(Path(constraints) / (unit + '.txt'))])
            subprocess.run(command, env=env, check=True)
            versions = pins(locked.read_text(encoding='utf-8'))
            lock = output / (unit + '.lock')
            write_lock(lock, versions)
        record = {'format': 'ioalab.deployment-lock/1', 'unit': unit, 'target': target, 'python': '3.11',
                  'inputSha256': plan['inputSha256'], 'lockSha256': sha(lock), 'resolver': version}
        (output / (unit + '.json')).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
        print(unit + ': ' + str(len(versions)) + ' ordinary packages locked', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheels', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--target', required=True, choices=TARGETS)
    parser.add_argument('--constraints', type=Path, help='directory containing core.txt, participant.txt, swarm.txt')
    parser.add_argument('--index', default='https://pypi.tuna.tsinghua.edu.cn/simple')
    args = parser.parse_args()
    resolve(args.wheels, args.out, args.target, index=args.index, constraints=args.constraints)


if __name__ == '__main__':
    main()
