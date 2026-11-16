"""Role-bound launcher; both skills use the same installed client."""
import subprocess
import sys
from pathlib import Path
import json

CLIENT_PYTHON = __IOALAB_PYTHON__
CLIENT_CONFIG = __IOALAB_CONFIG__
if __name__ == '__main__':
    if any(value == '--config' or value.startswith('--config=') for value in sys.argv[1:]):
        print(json.dumps({'status': 'rejected', 'data': None, 'error': {'code': 'identity_conflict',
            'message': 'This skill is bound to its client configuration; omit --config', 'recovery': 'use_bound_client'}}))
        raise SystemExit(1)
    if not Path(CLIENT_PYTHON).is_file():
        print(json.dumps({'status': 'rejected', 'data': None, 'error': {'code': 'not_ready',
            'message': 'The bound client interpreter is missing; repair the original client binding', 'recovery': 'repair_binding'}}))
        raise SystemExit(1)
    raise SystemExit(subprocess.call([CLIENT_PYTHON, '-I', '-X', 'utf8', '-m',
                                     'ioalab_client', '--role', '__IOALAB_ROLE__', '--config', CLIENT_CONFIG, *sys.argv[1:]]))
