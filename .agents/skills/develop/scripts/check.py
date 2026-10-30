"""Run the repository gates, or focused tests during implementation."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tests', nargs='+', help='focused pytest targets; does not replace final full gates')
    args = parser.parse_args()
    helper = ROOT / '.agents/skills/design/scripts/check.py'
    if not helper.is_file() or not (ROOT / 'AGENTS.md').is_file():
        parser.error('run this helper from its repository skill location')
    commands = [[str(helper)]]
    commands.append(['-m', 'pytest', '-q', *args.tests] if args.tests else [str(helper), '--full'])
    env = {**os.environ, 'PYTHONUTF8': '1', 'IOALAB_STUB': '1'}
    for command in commands:
        print('Running: ' + ' '.join(command), flush=True)
        result = subprocess.run([sys.executable, *command], cwd=ROOT, env=env)
        if result.returncode:
            return result.returncode if result.returncode > 0 else 1
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
