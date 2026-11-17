"""Private values for the one Process invocation engine; not a public host API."""
from dataclasses import dataclass, field

@dataclass
class _Round:
    operation_id: str
    actor: str
    interaction_id: str
    definition: object
    input: str
    workspace: str
    planned_request_id: str
    timeout_seconds: float|None=None
    session_id: str|None=None
    on_session: object=None
    on_interaction: object=None
    on_tool: object=None
    on_event: object=None
    # Frozen SDK phase fact, recorded only: this adapter declares no native
    # tool restriction, so permissions keep their configured levels (D37).
    native_tools_enabled: bool=False
    definition_digest: str|None=None

@dataclass
class _Result:
    status: str
    session_id: str|None
    usage: dict
    error: dict|None=None
    planned_request_id: str|None=None
    native_record: dict=field(default_factory=dict)
