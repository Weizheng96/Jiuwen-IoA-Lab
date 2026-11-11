"""Small host contracts. Role code owns business rules, not the host adapter."""
import copy
from dataclasses import dataclass,field
from typing import Callable,Protocol

from jsonschema import Draft202012Validator,FormatChecker
from referencing import Registry,Resource
from referencing.jsonschema import DRAFT202012

from .protocol import RuntimeFault,encode


def output_schema_check(schema):
    """Validate a bounded schema without allowing remote reference retrieval."""
    if not isinstance(schema,dict):
        raise RuntimeFault('invalid_input','Tool output_schema must be a JSON Schema object')
    try:
        if len(encode(schema).encode('utf-8'))>16384:
            raise ValueError('Output schema exceeds 16 KiB')
        Draft202012Validator.check_schema(schema)
        resource=Resource.from_contents(schema,default_specification=DRAFT202012)
        resolver=Registry().resolver_with_root(resource)
        def check(current,scope):
            if isinstance(current.contents,dict):
                if 'format' in current.contents and current.contents['format'] not in FormatChecker.checkers:
                    raise RuntimeFault('invalid_input','Tool output_schema format has no installed validator: '+current.contents['format'],
                                       'configure_tools')
                for keyword in ('$ref','$dynamicRef','$recursiveRef'):
                    if keyword in current.contents:
                        reference=current.contents[keyword]
                        if not isinstance(reference,str) or not reference.startswith('#'):
                            raise ValueError('External output schema references are not supported')
                        scope.lookup(reference)
            for child in current.subresources():check(child,scope.in_subresource(child))
        check(resource,resolver)
    except RuntimeFault:raise
    except Exception:
        raise RuntimeFault('invalid_input','Tool output_schema must be valid, self-contained JSON Schema within 16 KiB') from None


def validate_output(tool,result):
    """Check actual JSON output without rewriting the tool's original receipt."""
    try:
        encode(result).encode('utf-8')
        if tool.output_schema is not None:
            Draft202012Validator(tool.output_schema,format_checker=FormatChecker(),registry=Registry()).validate(result)
    except Exception:
        raise RuntimeFault('invalid_tool_output','Tool output does not match its declared JSON contract; preserve and inspect the original effect',
                           'inspect_tool_effect') from None
    return result


@dataclass(frozen=True)
class ToolBinding:
    name: str
    description: str
    input_schema: dict
    invoke: Callable=field(repr=False)
    effect: str='none'
    revision: str='1'
    requires_steps: tuple[str,...]=()
    output_schema: dict|None=None

    def __post_init__(self):
        if self.output_schema is not None:
            output_schema_check(self.output_schema)
            object.__setattr__(self,'output_schema',copy.deepcopy(self.output_schema))

    def declaration(self):
        return {'name':self.name,'description':self.description,'input_schema':self.input_schema,
                **({'output_schema':copy.deepcopy(self.output_schema)} if self.output_schema is not None else {})}

    def policy(self):
        return {**self.declaration(),'effect':self.effect,'revision':self.revision,
                **({'requiresSteps':list(self.requires_steps)} if self.requires_steps else {})}


@dataclass(frozen=True)
class BusinessTools:
    bindings: tuple[ToolBinding,...]
    close: Callable|None=field(default=None,repr=False)
    start: Callable|None=field(default=None,repr=False)
    status: Callable|None=field(default=None,repr=False)


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    instructions: str
    tools: tuple[ToolBinding,...]
    skills: tuple=()
    native_tools: tuple[tuple[str,str],...]=()


@dataclass(frozen=True)
class HostTurn:
    operation_id: str
    actor: str
    interaction_id: str
    definition: AgentDefinition
    input: str
    workspace: str
    timeout_seconds: float=300
    session_id: str|None=None
    on_session: Callable|None=field(default=None,repr=False)
    on_interaction: Callable|None=field(default=None,repr=False)
    on_tool: Callable|None=field(default=None,repr=False)
    on_event: Callable|None=field(default=None,repr=False)
    allow_native_tools: bool=False


@dataclass(frozen=True)
class HostResult:
    status: str
    session_id: str|None
    usage: dict
    error: dict|None=None
    request_id: str|None=None


class AgentHost(Protocol):
    def capabilities(self)->dict:...
    def run(self,turn:HostTurn)->HostResult:...
    def cancel(self,operation_id:str)->bool:...
