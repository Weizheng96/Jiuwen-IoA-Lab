"""One native request's frozen definition, tool gate and P28 callback gate.

Pure module: rail.py injects native PromptSection/LocalFunction factories
(tool_factory returns card/ability/info for one declaration). It is
cached once per native process so every loaded Rail sees the same PENDING map.
"""
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import threading
import time


current=ContextVar('ioa_native_current_turn',default=None)
# (actual SID, original request) -> PreparedTurn. Private worker preparation,
# never an accepted identity; accepted identity is the original snapshot.
PENDING={}
SECTION='ioa_original_definition'
TOKEN='_ioa_turn_token'

# Proof evidence is off unless an operator placed the marker beside this
# installed resource (a test instance's extension directory). Records hold
# only phase names, IDs, timestamps and booleans: never text, arguments,
# model output, tool values or credentials.
_HERE=Path(__file__).resolve().parent
EVIDENCE=_HERE/'ioa-evidence.jsonl' if (_HERE/'ioa-evidence.enabled').is_file() else None
_sequence=itertools.count(1);_written=threading.Lock()


def evidence(phase,turn=None,**fields):
    if EVIDENCE is None:return
    record={'phase':phase,'seq':next(_sequence),'wall':time.time(),'mono':time.monotonic()}
    if turn is not None:record.update(session=turn.session_id,request=turn.request_id)
    for key,value in fields.items():
        # bool/int (object identities) only; IDs arrive through `turn`.
        if type(value) not in (bool,int):raise TypeError('evidence carries only IDs, timestamps and booleans')
        record[key]=value
    try:
        with _written,open(EVIDENCE,'a',encoding='utf-8') as stream:stream.write(json.dumps(record)+'\n')
    except OSError:pass


def deadline_of(execution):
    value=execution.get('deadline')
    if value is None:return None
    try:moment=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (TypeError,ValueError,AttributeError):raise ValueError('native_deadline_invalid') from None
    if moment.tzinfo is None:raise ValueError('native_deadline_invalid')
    return moment


def _name(tool):return tool.get('name') if isinstance(tool,dict) else getattr(tool,'name',None)


class PreparedTurn:
    def __init__(self,session_id,request_id,execution,section_factory,tool_factory,callback,*,clock=None):
        tools=execution.get('tools') or []
        if execution.get('kind')!='business' or len(tools)!=1 or not isinstance(tools[0].get('name'),str):
            raise ValueError('native_definition_unsupported')
        authorization=execution.get('authorization') or {}
        if 'nativeToolsEnabled' in authorization and type(authorization['nativeToolsEnabled']) is not bool:
            raise ValueError('native_tool_phase_invalid')
        self.session_id=session_id;self.request_id=request_id;self.execution=execution
        self.section_factory=section_factory;self.tool_factory=tool_factory;self.callback=callback
        self.tool_name=tools[0]['name'];self.declaration=tools[0]
        # Frozen trusted boolean, recorded only. This adapter declares no native
        # tool restriction (D37): native tools stay as the user configured them.
        self.native_tools_enabled=authorization.get('nativeToolsEnabled') is True
        self.deadline=deadline_of(execution);self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.owner=None;self.agent=None;self.section=None;self.tool=None
        self.installed=False;self.closed=False;self.failed=None;self.release=None
        self.binding=asyncio.Event();self.ack=False;self._owners=set()

    @property
    def key(self):return (self.session_id,self.request_id)

    def _collision(self,agent):
        manager=agent.ability_manager
        if agent.prompt_builder.get_section(SECTION) is not None or manager.get(self.tool_name) is not None:
            raise ValueError('native_definition_or_tool_collision')

    def reserve(self,owner,agent):
        """Before send: check only. No prompt/tool change reaches a busy user turn."""
        if self.closed or self.agent is not None:raise ValueError('native_agent_preparation_ambiguous')
        if PENDING.get(self.key) not in (None,self):raise ValueError('native_request_already_prepared')
        self._collision(agent)
        self.owner=owner;self.agent=agent
        self.section=self.section_factory(SECTION,self.execution['definition']['instructions'])
        PENDING[self.key]=self
        evidence('reserve',self,owner=id(owner),agent=id(agent),native_tools_enabled=self.native_tools_enabled)

    def install(self):
        if self.closed or self.agent is None or self.installed:raise ValueError('native_agent_preparation_ambiguous')
        if not self.ack and self.binding.is_set():raise ValueError('native_binding_unacknowledged')
        agent=self.agent;self._collision(agent)
        agent.prompt_builder.add_section(self.section)
        try:
            self.tool=self.tool_factory(self.declaration,self.invoke)
            agent.ability_manager.add_ability(self.tool.card,self.tool.ability)
            self.installed=True
        except BaseException:self._remove();raise

    def check(self,agent,route,*,owner_task=False):
        valid=(self.installed and not self.closed and self.failed is None and self.agent is agent and route==self.key and
            agent.prompt_builder.get_section(SECTION) is self.section and
            agent.ability_manager.get(self.tool_name) is self.tool.card)
        if not valid:raise ValueError(self.failed or 'native_definition_ownership_lost')
        # Only the invocation task owns cleanup; parallel tool tasks end early.
        task=asyncio.current_task() if owner_task else None
        if task is not None and task not in self._owners:
            self._owners.add(task);task.add_done_callback(lambda _:self.close())

    def expired(self):return self.deadline is not None and self.clock()>=self.deadline

    # Native hook bodies. rail.py supplies the actual route of the invocation.
    def on_invoke(self,owner,route):
        if route!=self.key:
            # Next-owner check: a cancelled/looped invocation can skip after hooks.
            if self.installed:
                evidence('stale_cleanup',self,owner=id(owner));self.close()
            return
        try:
            if owner is not self.owner:raise ValueError('native_agent_mismatch')
            if self.expired():raise ValueError('native_deadline_expired')
            self.install();self.check(self.agent,route,owner_task=True)
            evidence('installed',self,owner=id(owner),agent=id(self.agent),section=True,sdk_tool=True)
        except ValueError as exc:
            # The original request is accepted; it ends at its first model hook.
            self.failed=str(exc);self._remove()
            evidence('install_failed:'+self.failed,self,owner=id(owner),same_owner=owner is self.owner)

    def on_model(self,ctx,route):
        if route!=self.key:
            if self.installed:
                evidence('stale_cleanup',self,agent=id(ctx.agent));self.close()
            return
        try:
            if self.expired():raise ValueError('native_deadline_expired')
            self.check(ctx.agent,route)
        except ValueError as exc:
            self.failed=self.failed or str(exc);self._remove()
            ctx.request_force_finish({'output':'','error':{'code':self.failed}})
            evidence('model_blocked:'+self.failed,self,agent=id(ctx.agent),force_finish=True)
            return
        # The user's native tools stay in the window in every phase; only the
        # one SDK tool is ensured.
        tools=list(ctx.inputs.tools or [])
        if not any(_name(t)==self.tool_name for t in tools):tools.append(self.tool.info)
        ctx.inputs.tools=tools
        evidence('model_window',self,agent=id(ctx.agent),section=self.agent.prompt_builder.get_section(SECTION) is self.section,
            sdk_tool=any(_name(t)==self.tool_name for t in tools),
            native_tools=any(_name(t)!=self.tool_name for t in tools),native_tools_enabled=self.native_tools_enabled)

    def on_tool(self,ctx,route):
        if route!=self.key:return
        inputs=ctx.inputs
        try:
            if self.expired():raise ValueError('native_deadline_expired')
            self.check(ctx.agent,route)
        except ValueError as exc:
            self.failed=self.failed or str(exc);self._remove()
            ctx.request_force_finish({'output':'','error':{'code':self.failed}})
            evidence('tool_blocked:'+self.failed,self,agent=id(ctx.agent),force_finish=True)
            return
        if inputs.tool_name==self.tool_name:
            call=getattr(inputs.tool_call,'id',None)
            setattr(ctx,TOKEN,current.set((self,str(call) if call else None)))
            evidence('tool_sdk',self,agent=id(ctx.agent),call_id=bool(call))
            return
        # Ordinary native tool: never rerouted or denied by IoA (D37).
        evidence('tool_native',self,agent=id(ctx.agent),native_tools_enabled=self.native_tools_enabled)

    def after_tool(self,ctx):
        token=getattr(ctx,TOKEN,None)
        if token is not None:
            setattr(ctx,TOKEN,None);current.reset(token)

    def after_invoke(self,route):
        if route==self.key:self.close()

    def accept(self,value):
        # Only the original snapshot/P28 response reaches this method. A later
        # exact ACK may follow a transient False; True is never withdrawn.
        self.ack=self.ack or value is True
        self.binding.set()
        evidence('binding_ack',self,ack=self.ack)

    async def invoke(self,**arguments):
        route=current.get()
        if route is None or route[0] is not self or not route[1]:
            evidence('callback_unbound',self);return {'error':{'code':'native_request_unbound'}}
        evidence('binding_wait_start',self,ack=self.ack,waiting=not self.binding.is_set())
        try:
            if self.deadline is None:await self.binding.wait()
            else:await asyncio.wait_for(self.binding.wait(),max(0,(self.deadline-self.clock()).total_seconds()))
        except asyncio.TimeoutError:
            evidence('binding_wait_end:native_deadline_expired',self,ack=self.ack)
            return {'error':{'code':'native_deadline_expired'}}
        evidence('binding_wait_end',self,ack=self.ack,closed=self.closed)
        if not self.ack or self.closed:return {'error':{'code':'native_binding_unacknowledged'}}
        evidence('callback_sent',self)
        result=await self.callback(route[1],self.tool_name,arguments)
        valid=isinstance(result,dict)
        evidence('callback_done',self,receipt=valid,error=valid and result.get('error') is not None)
        if not valid:return {'error':{'code':'native_callback_receipt_invalid'}}
        return result['value'] if result.get('error') is None else {'value':result.get('value'),'error':result['error']}

    def _remove(self):
        if self.agent is None:return
        builder=self.agent.prompt_builder;manager=self.agent.ability_manager
        if self.section is not None and builder.get_section(SECTION) is self.section:builder.remove_section(SECTION)
        if self.tool is not None and manager.get(self.tool.card.name) is self.tool.card:manager.remove_ability(self.tool.card.name)
        self.installed=False

    def close(self):
        if self.closed:return
        self.closed=True;self.binding.set();self._remove()
        if PENDING.get(self.key) is self:PENDING.pop(self.key,None)
        evidence('cleanup',self,section=self.agent is not None and self.agent.prompt_builder.get_section(SECTION) is not None,
                 pending=self.key in PENDING)
        if self.release is not None:
            release,self.release=self.release,None
            try:asyncio.get_running_loop().create_task(release())
            except RuntimeError:pass


def tripwire(ctx,route):
    """Any IoA Rail: a prepared request invoked where its guard did not install never calls a model.

    Once installed on the reserved Agent, nested native work of that same
    invocation inherits the route and is governed by the installed tool gate."""
    turn=PENDING.get(route)
    if turn is not None and not turn.installed and ctx.agent is not turn.agent:
        turn.failed=turn.failed or 'native_agent_mismatch'
        ctx.request_force_finish({'output':'','error':{'code':turn.failed}})
        evidence('tripwire',turn,agent=id(ctx.agent),force_finish=True)


class Preparation:
    """Worker handle: P28 ACK delivery and owned guard-rail cleanup."""
    def __init__(self,turn,unload):
        self.turn=turn;self._unload=unload;self._done=False

    def accept(self,value):self.turn.accept(value)

    @property
    def failure(self):return self.turn.failed

    async def _release(self):
        if self._done:return
        self._done=True
        await self._unload()
        evidence('unload',self.turn)

    async def finish(self,final):
        # final: not dispatched, or the original native request is terminal.
        # Otherwise keep the inert guard so a late original invocation still
        # meets its frozen definition; its own invocation end releases it.
        evidence('finish',self.turn,final=bool(final))
        if final:
            self.turn.close()
            await self._release()
        elif self.turn.closed:await self._release()
        else:self.turn.release=self._release
