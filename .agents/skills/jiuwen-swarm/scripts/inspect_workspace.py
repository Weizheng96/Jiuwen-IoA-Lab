"""Read-only Jiuwen workspace/installation inventory without credential access."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--python', type=Path)
    args = parser.parse_args()
    root = args.workspace.expanduser().resolve()
    if not root.is_dir() or not (root / 'config/config.yaml').is_file():
        print(json.dumps({'status': 'rejected', 'code': 'not_existing_workspace'}))
        return 2
    value = {'status': 'inspected', 'workspace': str(root), 'configPresent': True,
             'environmentFilePresent': (root / 'config/.env').is_file(),
             'userApplicationDirectoryPresent': (root / 'application_plugins').is_dir(),
             'ioaNotificationFilesPresent': all((root / 'application_plugins/ioa-notifications' / name).is_file()
                                                for name in ('extension.py', 'extension.yaml')),
             'businessReady': 'not_checked'}
    if args.python:
        code = "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ('workswarm',)}))"
        try:
            result = subprocess.run([str(args.python), '-I', '-B', '-c', code], capture_output=True,
                                    text=True, encoding='utf-8', timeout=15)
            value['distribution'] = json.loads(result.stdout) if result.returncode == 0 else {'status': 'not_identified'}
        except (OSError, ValueError, subprocess.TimeoutExpired): value['distribution'] = {'status': 'not_identified'}
    print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == '__main__': raise SystemExit(main())
