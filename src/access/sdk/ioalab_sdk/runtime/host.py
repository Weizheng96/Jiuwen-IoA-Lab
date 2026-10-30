"""Small host contracts. Role code owns business rules, not the host adapter."""
from dataclasses import dataclass,field
from typing import Callable,Protocol


@dataclass(frozen=True)
class ToolBinding:
    name: str
    description: str
    input_schema: dict
    invoke: Callable=field(repr=False)
    effect: str='none'
    revision: str='1'

    def declaration(self):
        return {'name':self.name,'description':self.description,'input_schema':self.input_schema}

    def policy(self):
        return {**self.declaration(),'effect':self.effect,'revision':self.revision}


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    instructions: str
    tools: tuple[ToolBinding,...]
    skills: tuple=()


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


@dataclass(frozen=True)
class HostResult:
    status: str
    session_id: str|None
    usage: dict
    error: dict|None=None


class AgentHost(Protocol):
    def capabilities(self)->dict:...
    def run(self,turn:HostTurn)->HostResult:...
    def cancel(self,operation_id:str)->bool:...
    def notify(self,message:dict)->dict:...
