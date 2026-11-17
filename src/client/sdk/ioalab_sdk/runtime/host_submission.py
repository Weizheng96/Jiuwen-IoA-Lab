"""Neutral S01–S05 values. Native protocols and business effects have other owners."""
from dataclasses import dataclass, field
import hashlib
from typing import Callable, Protocol

from .protocol import RuntimeFault, encode


class NativeSubmissionPending(RuntimeFault):
    """SDK control signal, issued only after P20 verifies no native dispatch."""
    def __init__(self,issuer,reference,*,error):
        super().__init__('native_not_submitted','The original native submission is waiting without dispatch','wait_original',operation_id=reference['operation'])
        self.issuer=issuer
        self.reference=dict(reference)
        self.details={'code':error.code,'phase':error.phase,'dispatch':error.dispatch,'message':error.message,'action':error.action}


def _reference(value, name, *, optional=False):
    if optional and value is None:return
    if not isinstance(value,str) or not 1<=len(value)<=8192:
        raise RuntimeFault('invalid_input','A bounded '+name+' is required')


@dataclass(frozen=True)
class HostResource:
    resource_id: str
    name: str
    media_type: str
    digest: str|None
    access_scope: str
    locator: str

    def __post_init__(self):
        for name in ('resource_id','name','media_type','access_scope','locator'):
            _reference(getattr(self,name),name)
        _reference(self.digest,'digest',optional=True)


@dataclass(frozen=True)
class HostRegistration:
    adapter_id: str
    version: str
    optional_methods: tuple[str,...]=()
    resources: tuple[HostResource,...]=()
    readonly_context: str='unavailable'
    # Static adapter promise to restrict native tools in nativeToolsEnabled=false
    # rounds. The adapter alone guarantees it; the public layer only records it.
    native_tool_restriction: bool=False

    def __post_init__(self):
        _reference(self.adapter_id,'adapter ID');_reference(self.version,'adapter version')
        if len(set(self.optional_methods))!=len(self.optional_methods) or set(self.optional_methods)-{'install_skills','get_request','cancel'}:
            raise RuntimeFault('invalid_input','Declare only optional S03–S05 methods')
        if not all(isinstance(item,HostResource) for item in self.resources):
            raise RuntimeFault('invalid_input','Use static packaged resource references')
        if self.readonly_context not in ('unavailable','isolated_session','business_session'):
            raise RuntimeFault('invalid_input','Declare a supported static read-only context mode')
        if type(self.native_tool_restriction) is not bool:
            raise RuntimeFault('invalid_input','Declare native tool restriction as a static boolean')


@dataclass(frozen=True)
class HostToolDeclaration:
    name: str
    description: str
    input_schema: dict
    output_schema: dict|None=None

    def __post_init__(self):
        _reference(self.name,'tool name')
        # No callable or native object crosses the S01 declaration boundary.
        encode({'name':self.name,'description':self.description,'input':self.input_schema,'output':self.output_schema})


@dataclass(frozen=True)
class HostDefinition:
    reference: str
    version: str
    digest: str
    instructions: str
    resources: tuple[HostResource,...]=()

    def __post_init__(self):
        for name in ('reference','version','digest'):_reference(getattr(self,name),name)
        if not isinstance(self.instructions,str):raise RuntimeFault('invalid_input','Frozen instructions must be text')
        if not all(isinstance(item,HostResource) for item in self.resources):raise RuntimeFault('invalid_input','Use frozen definition resource references')


@dataclass(frozen=True)
class HostExecution:
    actor: str
    profile: str
    business_ref: str
    interaction_id: str
    job_id: str
    definition: HostDefinition
    kind: str='business'
    tools: tuple[HostToolDeclaration,...]=()
    authorization: dict=field(default_factory=dict)
    deadline: str|None=None
    workspace: str|None=None
    proposal_schema: dict|None=None

    def __post_init__(self):
        for name in ('actor','profile','business_ref','interaction_id','job_id'):_reference(getattr(self,name),name)
        if self.kind not in ('business','observation'):raise RuntimeFault('invalid_input','Use a business or observation execution scope')
        if not isinstance(self.definition,HostDefinition) or not all(isinstance(item,HostToolDeclaration) for item in self.tools):
            raise RuntimeFault('invalid_input','Use a frozen definition and tool declarations')
        if not isinstance(self.authorization,dict) or ('nativeToolsEnabled' in self.authorization and type(self.authorization['nativeToolsEnabled']) is not bool):
            raise RuntimeFault('invalid_input','The SDK native-tool phase authorization must be a boolean')
        encode({'authorization':self.authorization,'proposal_schema':self.proposal_schema})


def native_tools_restricted(registration,execution):
    """Recorded fact only: a declared adapter received nativeToolsEnabled=false.

    Never a gate: the public layer neither verifies the restriction nor refuses,
    reroutes or marks not_submitted because of it.
    """
    return bool(isinstance(registration,HostRegistration) and registration.native_tool_restriction
                and isinstance(execution,HostExecution) and execution.authorization.get('nativeToolsEnabled',False) is False)


def proposal_tool_declared(execution):
    """True when an SDK tool of this round accepts the exact proposal schema.

    An adapter without a structured output channel still submits such a round:
    the proposal stays empty and the same-key SDK tool alone carries the effect.
    """
    return bool(isinstance(execution,HostExecution) and execution.proposal_schema is not None
                and any(tool.input_schema==execution.proposal_schema for tool in execution.tools))


def execution_snapshot(identity,interaction,job,definition,*,definition_digest,version,deadline=None,workspace=None,kind='business',business_ref=None,host_definition=None,native_tools_enabled=True):
    """Encode the role-selected original snapshot, without host discovery or effects."""
    if host_definition is not None and (not isinstance(host_definition,HostDefinition) or host_definition.instructions!=definition.instructions):
        raise RuntimeFault('identity_conflict','The frozen host definition changed the original role instructions')
    return HostExecution(identity.agent_id,identity.profile,business_ref or interaction,interaction,job,
        host_definition or HostDefinition(definition.name,str(version),definition_digest,definition.instructions),
        kind=kind,tools=tuple(HostToolDeclaration(item.name,item.description,item.input_schema,item.output_schema) for item in definition.tools),
        authorization={'nativeTools':{},'nativeToolsEnabled':native_tools_enabled},deadline=deadline,workspace=workspace)


@dataclass(frozen=True)
class SubmissionAttempt:
    nonce: str
    scope_digest: str
    actor: str
    profile: str

    def __post_init__(self):
        _reference(self.nonce,'submission attempt');_reference(self.scope_digest,'submission scope digest')
        _reference(self.actor,'SDK actor');_reference(self.profile,'SDK profile')


@dataclass(frozen=True)
class HostInput:
    text: str
    resources: tuple[HostResource,...]=()
    # SDK transport metadata: never accepted from model arguments or professional content.
    attempt: SubmissionAttempt|None=field(default=None,repr=False)

    def __post_init__(self):
        if not isinstance(self.text,str) or not all(isinstance(item,HostResource) for item in self.resources):
            raise RuntimeFault('invalid_input','Use text and stable input resource references')


@dataclass(frozen=True)
class SubmissionError:
    code: str
    phase: str
    dispatch: str
    message: str
    action: str|None=None

    def __post_init__(self):
        if self.dispatch not in ('not_submitted','accepted','unknown'):
            raise RuntimeFault('invalid_input','Report the actual submission fact')


@dataclass(frozen=True)
class MachineTerminal:
    operation_id: str
    attempt_id: str
    session_id: str
    request_id: str
    state: str
    evidence_ref: str

    def __post_init__(self):
        for name in ('operation_id','attempt_id','session_id','request_id','evidence_ref'):_reference(getattr(self,name),name)
        if self.state not in ('completed','failed','cancelled'):
            raise RuntimeFault('invalid_input','Use a real machine terminal, never an acknowledgement')


@dataclass(frozen=True)
class SubmissionResult:
    operation_id: str
    attempt_id: str
    state: str
    session_id: str|None=None
    request_id: str|None=None
    delivery: str='unknown'
    output: object=None
    resources: tuple[HostResource,...]=()
    error: SubmissionError|None=None
    terminal: MachineTerminal|None=None
    usage: dict=field(default_factory=dict)
    proposal: dict|None=None

    def __post_init__(self):
        _reference(self.operation_id,'operation ID');_reference(self.attempt_id,'attempt ID')
        _reference(self.session_id,'session ID',optional=True)
        _reference(self.request_id,'request ID',optional=True)
        if self.state not in ('accepted','running','waiting','completed','failed','cancelled','unknown'):
            raise RuntimeFault('invalid_input','Report the actual execution state')
        if self.delivery not in ('unknown','accepted','delivered','handled'):
            raise RuntimeFault('invalid_input','Report the actual delivery fact')
        if self.terminal is not None and not isinstance(self.terminal,MachineTerminal):
            raise RuntimeFault('invalid_input','Use real machine terminal evidence')


def submission_succeeded(result):
    """Pure completion after P20 validation; not business-effect or drain proof."""
    if not isinstance(result,SubmissionResult) or result.error is not None or result.state!='completed':return False
    terminal=result.terminal
    return bool(terminal and terminal.state=='completed' and
        (terminal.operation_id,terminal.attempt_id,terminal.session_id,terminal.request_id)==
        (result.operation_id,result.attempt_id,result.session_id,result.request_id))


@dataclass(frozen=True)
class SubmissionEvent:
    event_id: str
    kind: str
    result: SubmissionResult

    def __post_init__(self):
        _reference(self.event_id,'event ID')
        if self.kind not in ('session','accepted','running','waiting','output','terminal','error'):
            raise RuntimeFault('invalid_input','Use a neutral execution event')


@dataclass(frozen=True)
class BindingAck:
    session_id: str
    request_id: str|None=None

    def __post_init__(self):
        _reference(self.session_id,'session ID');_reference(self.request_id,'request ID',optional=True)


@dataclass(frozen=True)
class HostToolCall:
    operation_id: str
    attempt_id: str
    session_id: str
    request_id: str
    call_id: str
    name: str
    arguments: dict

    def __post_init__(self):
        for name in ('operation_id','attempt_id','session_id','request_id','call_id','name'):_reference(getattr(self,name),name)
        if not isinstance(self.arguments,dict):raise RuntimeFault('invalid_input','SDK tool arguments must be an object')
        encode(self.arguments)

    @property
    def transport_reference(self):
        """Original native call scope, not a business action/effect identity."""
        return hashlib.sha256(encode([self.operation_id,self.request_id,self.call_id]).encode()).hexdigest()


@dataclass(frozen=True)
class HostToolResult:
    value: object=None
    resources: tuple[HostResource,...]=()
    error: SubmissionError|None=None


class HostAdapter(Protocol):
    def submit(self,operation_id:str,input:HostInput,session_id:str|None=None,*,
               execution:HostExecution|None=None,on_event:Callable|None=None,
               on_tool:Callable|None=None)->SubmissionResult:...
    def check(self)->dict:...
