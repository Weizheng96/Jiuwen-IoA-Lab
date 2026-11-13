"""Public client CLI: inspect installation, export skills, or call the selected role."""
import argparse
import json
from pathlib import Path

from . import export_skills
from . import installation


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--role':
        from .commands import main as call
        return call(argv)
    parser = argparse.ArgumentParser(prog='ioalab-client')
    parser.add_argument('command', choices=('status', 'skills'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    result = installation.prepare()
    if result['status'] == 'completed' and args.command == 'skills':
        if args.output is None: parser.error('skills requires --output')
        try: result['data']['files'] = [str(path) for path in export_skills(args.output)]
        except (OSError, ValueError, RuntimeError) as error:
            result = {'status': 'rejected', 'data': None,
                      'error': {'code': 'export_failed', 'message': str(error)}}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] == 'completed' else 1
