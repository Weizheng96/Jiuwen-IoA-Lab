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
    parser.add_argument('command', choices=('status', 'skills', 'skills-install', 'onboarding'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--host', choices=('jiuwen', 'codex', 'claude_code'))
    parser.add_argument('--format', choices=('json', 'markdown'), default='json')
    args = parser.parse_args(argv)
    result = installation.prepare()
    if result['status'] == 'completed' and args.command == 'skills':
        if args.output is None: parser.error('skills requires --output')
        try: result['data']['files'] = [str(path) for path in export_skills(args.output, config=args.config)]
        except (OSError, ValueError, RuntimeError) as error:
            result = {'status': 'rejected', 'data': None,
                      'error': {'code': 'export_failed', 'message': str(error)}}
    if result['status'] == 'completed' and args.command in ('skills-install', 'onboarding'):
        if not args.host: parser.error(args.command+' requires --host')
        try:
            if args.command == 'skills-install':
                from .native_skills import install
                data = install(args.host, config=args.config, output=args.output)
                result = {'status': 'completed' if data['installed'] else 'waiting', 'data': data, 'error': None}
            else:
                from .onboarding import summary
                result = summary(args.host, config=args.config, python=result['data']['python'])
        except (OSError, ValueError, RuntimeError) as error:
            result = {'status': 'rejected', 'data': None, 'error': {'code': 'onboarding_failed', 'message': str(error)}}
    if args.format == 'markdown' and args.command == 'onboarding' and (result.get('data') or {}).get('format') == 'ioalab.onboarding/1':
        from .onboarding import markdown
        print(markdown(result))
    else: print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] in ('completed', 'waiting') else 1
