"""Public local operations shared by CLI, MCP and other non-owner frontends."""
from pathlib import Path
import uuid

from .config import RuntimeConfig, data_directory, read_configuration
from .configuration import inspect_config
from .installation import SAFE_CALLS, require_matching
from .ipc import LocalClient, public_error
from .lifecycle import require_configuration
from .protocol import RuntimeFault


class ClientOperations:
    def __init__(self, config_path):
        self.path = Path(config_path).resolve()

    def configuration(self):
        return {'status': 'completed', 'data': inspect_config(self.path), 'error': None}

    def call(self, operation, arguments=None, *, operation_id=None):
        identifier = operation_id or str(uuid.uuid4())
        try:
            if arguments is not None and not isinstance(arguments, dict):
                raise RuntimeFault('invalid_input', 'Operation arguments must be an object')
            document = read_configuration(self.path)
            home = data_directory(self.path, document)
            client = LocalClient(home)
            if operation not in SAFE_CALLS:
                config = RuntimeConfig.from_mapping(self.path, document)
                state = client.call('runtime.status')['data']
                installation = state.get('installation')
                require_configuration(state, config,
                    assembly=installation.get('assembly') if isinstance(installation, dict) else None)
                client = LocalClient(home, boot_id=state['bootId'])
            result = client.call(operation, arguments or {}, operation_id=identifier)
            if operation == 'runtime.status' and result['status'] == 'completed':
                actual = result['data'].get('installation')
                try:
                    require_matching(actual, assembly=actual.get('assembly') if isinstance(actual, dict) else None)
                except RuntimeFault as exc:
                    result = {**result, 'status': 'rejected', 'error': public_error(exc)}
            return result
        except RuntimeFault as exc:
            return {'operationId': identifier, 'status': 'unknown' if exc.code in ('unknown_result', 'result_unknown') else 'rejected',
                    'data': None, 'error': public_error(exc)}
