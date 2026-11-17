"""Public Codex resident lifecycle, reusing the shared SDK."""
import argparse
import json
from pathlib import Path
import sys
from ioalab_sdk.runtime import RuntimeFault, configure_stdio, public_error, start
from .assembly import assembled


def main(argv=None):
    configure_stdio()
    parser = argparse.ArgumentParser(prog='ioalab-codex')
    parser.add_argument('command', choices=('start', 'serve'))
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'start':
            from .configuration import validate_host
            from ioalab_sdk.runtime import RuntimeConfig
            validate_host(RuntimeConfig.load(args.config), base_dir=args.config.resolve().parent)
            result = start(args.config, command=[sys.executable, '-m', 'ioalab_codex'], assembly='ioalab-codex')
            print(json.dumps(result, ensure_ascii=True))
            return 0 if result['status'] in ('completed', 'waiting') else 1
        with assembled(args.config) as assembly:
            assembly.run()
    except RuntimeFault as exc:
        print(json.dumps({'status': 'rejected', 'data': None, 'error': public_error(exc)}, ensure_ascii=True))
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'data': None, 'error': {'code': 'not_ready',
              'type': type(exc).__name__, 'recovery': 'inspect_local_configuration'}}, ensure_ascii=True))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
