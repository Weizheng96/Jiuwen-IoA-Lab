"""Shared resident runtime entry; role handlers, hosts and business tools are injected separately.

Other modules import the shared runtime only from here; submodules stay importable for installed callers."""
from .catalog import AGENT, ASSISTANT, CATALOG, INTERNAL, OPERATIONS, PROVIDER, RUNTIME, SCRIPT, Operation, fields, handled_by
from .flows import FlowJournal, flow_key
from .config import RuntimeConfig, data_directory, read_configuration
from .connection import AgentConnection
from .network import Network
from .files import prepare_home, private_json, private_text, read_arguments, read_json, restrict, windows_sid
from .files_api import PrivateFiles
from .host import AgentDefinition, AgentHost, BusinessTools, HostResult, HostTurn, ToolBinding, validate_output
from .installation import SAFE_CALLS, current, require_matching
from .ipc import LocalClient, public_error
from .lease import Lease
from .lifecycle import require_configuration, run, setup_command, start
from .observations import model_port
from .protocol import RuntimeFault, configure_stdio, encode, instant, stamp
from .questions import native_answer
from .resident import ResidentRuntime
from .scheduling import JobHandler, JobOutcome
from .store import RuntimeStore
from .trusted_user import TrustedUserPort, TrustedUserServer
from ..templates import TEMPLATE_LIFETIME, TEMPLATE_RENEW_BEFORE, draft_from_terms, template_from_definition

__all__ = [
    'FlowJournal', 'flow_key',
    'AGENT', 'ASSISTANT', 'CATALOG', 'INTERNAL', 'OPERATIONS', 'PROVIDER', 'RUNTIME', 'SCRIPT', 'Operation', 'fields',
    'handled_by', 'RuntimeConfig', 'data_directory', 'read_configuration', 'AgentConnection', 'Network', 'prepare_home',
    'private_json', 'private_text', 'read_arguments', 'read_json', 'restrict', 'windows_sid', 'PrivateFiles', 'AgentDefinition',
    'AgentHost', 'BusinessTools', 'HostResult', 'HostTurn', 'ToolBinding', 'validate_output', 'SAFE_CALLS', 'current',
    'require_matching', 'LocalClient', 'public_error', 'Lease', 'require_configuration', 'run', 'setup_command', 'start',
    'model_port', 'RuntimeFault', 'configure_stdio', 'encode', 'instant', 'stamp', 'native_answer', 'ResidentRuntime',
    'JobHandler', 'JobOutcome', 'RuntimeStore', 'TrustedUserPort', 'TrustedUserServer', 'TEMPLATE_LIFETIME',
    'TEMPLATE_RENEW_BEFORE', 'draft_from_terms', 'template_from_definition',
]
