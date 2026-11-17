"""Shared resident runtime entry; role handlers, hosts and business tools are injected separately.

Other modules import the shared runtime only from here; submodules stay importable for installed callers."""
from .business_skills import FrozenSkills,documents
from .confirmations import ConfirmationDeadline
from .catalog import AGENT, ASSISTANT, CATALOG, INTERNAL, OPERATIONS, PROVIDER, RUNTIME, SCRIPT, Operation, fields, handled_by
from .flows import FlowJournal, flow_key
from .config import DEFAULT_PLATFORM_URL, RuntimeConfig, data_directory, read_configuration
from .connection import AgentConnection
from .network import Network
from .files import prepare_home, private_json, private_text, read_arguments, read_json, restrict, windows_sid
from .files_api import PrivateFiles
from .host import AgentDefinition, BusinessTools, ToolBinding, validate_output,output_schema_check
from .host_registry import HostRegistry, HostContext, native_source, source_scope, source_value, source_context
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
    'HostAdapter','HostRegistration','HostResource','HostDefinition','HostExecution',
    'HostInput','HostToolDeclaration','HostToolCall','HostToolResult','BindingAck','execution_snapshot','submission_succeeded','native_tools_restricted','proposal_tool_declared','native_source_descriptor','freeze_definition','read_definition_resource','ExecutionResources','ResourceGrant','SubmissionResourcePort','ProfessionalExecution','MaterialObservations','MaterialObservationPending','MaterialObservationBlocked',
    'SubmissionAttempt','SubmissionError','SubmissionResult','SubmissionEvent','MachineTerminal','NativeSubmissionPending',
    'ConfirmationDeadline','NATIVE_ENTRY_BIND_OPERATION',
    'NativeEventTarget','NativeEvents','flow_native_notice',
    'native_source', 'source_scope', 'source_value', 'source_context', 'HostRegistry', 'HostContext',
    'FrozenSkills', 'documents',
    'FlowJournal', 'flow_key',
    'AGENT', 'ASSISTANT', 'CATALOG', 'INTERNAL', 'OPERATIONS', 'PROVIDER', 'RUNTIME', 'SCRIPT', 'Operation', 'fields',
    'handled_by', 'DEFAULT_PLATFORM_URL', 'RuntimeConfig', 'data_directory', 'read_configuration', 'AgentConnection', 'Network', 'prepare_home',
    'private_json', 'private_text', 'read_arguments', 'read_json', 'restrict', 'windows_sid', 'PrivateFiles', 'AgentDefinition',
    'BusinessTools', 'ToolBinding', 'validate_output','output_schema_check', 'SAFE_CALLS', 'current',
    'require_matching', 'LocalClient', 'public_error', 'Lease', 'require_configuration', 'run', 'setup_command', 'start',
    'model_port', 'RuntimeFault', 'configure_stdio', 'encode', 'instant', 'stamp', 'native_answer', 'ResidentRuntime',
    'JobHandler', 'JobOutcome', 'RuntimeStore', 'TrustedUserPort', 'TrustedUserServer', 'TEMPLATE_LIFETIME',
    'TEMPLATE_RENEW_BEFORE', 'draft_from_terms', 'template_from_definition',
]

from .host_registry import NativeEventTarget
from .native_events import NativeEvents,flow_native_notice
from .native_sources import native_source_descriptor
from .native_entry_bindings import NATIVE_ENTRY_BIND_OPERATION
from .definition_snapshots import freeze_definition,read_definition_resource
from .execution_resources import ExecutionResources,ResourceGrant
from .submission_resources import SubmissionResourcePort
from .professional_execution import ProfessionalExecution
from .material_observations import MaterialObservations,MaterialObservationPending,MaterialObservationBlocked
from .host_submission import (HostAdapter, HostRegistration, HostResource, HostDefinition,
    HostExecution, HostInput, HostToolDeclaration, HostToolCall, HostToolResult,
    BindingAck, SubmissionAttempt, SubmissionError, SubmissionResult,
    SubmissionEvent, MachineTerminal, execution_snapshot, submission_succeeded, NativeSubmissionPending,
    native_tools_restricted, proposal_tool_declared)
