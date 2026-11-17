"""Jiuwen native role entry; all business still uses the one public caller."""
from contextvars import ContextVar
import asyncio
from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace

from openjiuwen.core.foundation.tool import LocalFunction, ToolCard, ToolInfo
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentRail, ModelCallInputs, ToolCallInputs
from openjiuwen.core.sys_operation.cwd import get_cwd
from jiuwenswarm.common.context_keys import JIUWENSWARM_CHANNEL_CONTEXT_KEY
from jiuwenswarm.agents.harness.common.tools.session_messaging_toolkit import (
    SESSION_MESSAGING_ROUTE_EXTRA_KEY, current_session_messaging_route)
from jiuwenswarm.runtime.context import get_current_runtime
from jiuwenswarm.runtime.session_catalog import SessionGetInput
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod

_context=ContextVar('ioa_jiuwen_entry_context',default=None)


def _turns():
    # One process-wide request registry shared by every loaded Rail/Agent.
    name='_ioa_jiuwen_native_turn'
    module=sys.modules.get(name)
    if module is None:
        spec=importlib.util.spec_from_file_location(name,Path(__file__).parent/'turn.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module=sys.modules.setdefault(name,module)
    return module


def _route():
    # Bound by the native adapter for this invocation from the actual request.
    route=current_session_messaging_route()
    return (route.session_id,route.request_id) if route is not None else None


def _section(name,text):return PromptSection(name=name,content={'cn':text,'en':text},priority=100)


def _tool(declaration,func):
    card=ToolCard(id=declaration['name'],name=declaration['name'],description=declaration.get('description') or '',
        input_params=declaration.get('input_schema') or {'type':'object'},stateless=False,parallel_safe=False,idempotent=False)
    return SimpleNamespace(card=card,ability=LocalFunction(card=card,func=func),
        info=ToolInfo(name=card.name,description=card.description,parameters=card.input_params))


async def _capture(ctx):
    if ctx is None:raise ValueError('native_context_missing')
    run=ctx.extra.get('run_context');extra=run.get('extra') if isinstance(run,dict) else getattr(run,'extra',None)
    if not isinstance(extra,dict) or extra.get(JIUWENSWARM_CHANNEL_CONTEXT_KEY)!='web':raise ValueError('native_channel_unavailable')
    route=ctx.extra.get('session_input_message_route') or extra.get(SESSION_MESSAGING_ROUTE_EXTRA_KEY)
    if not isinstance(route,dict) or not all(isinstance(route.get(k),str) and route[k] for k in ('session_id','request_id','user_id')):
        raise ValueError('native_route_unavailable')
    runtime=get_current_runtime()
    if runtime is None:raise ValueError('native_runtime_unavailable')
    actual=runtime.get_session(SessionGetInput(channel_id='web',session_id=route['session_id']))
    descriptor=await runtime.describe_session(session_id=route['session_id'])
    cwd=Path(get_cwd())
    if (actual is None or descriptor is None or actual.session_id!=route['session_id'] or actual.channel_id!='web' or
        descriptor.session_id!=actual.session_id or descriptor.channel_id!='web' or descriptor.user_id!=route['user_id'] or
        not descriptor.user_id or descriptor.project_dir!=actual.project_dir or
        not cwd.is_absolute() or not actual.project_dir or not Path(actual.project_dir).is_absolute() or
        not cwd.resolve().is_relative_to(Path(actual.project_dir).resolve())):
        raise ValueError('native_source_mismatch')
    return runtime,{'sessionId':actual.session_id,'requestId':route['request_id'],'userId':descriptor.user_id,
        'channelId':descriptor.channel_id,'workspace':str(cwd),'projectId':descriptor.project_id,
        'projectDir':descriptor.project_dir,'mode':descriptor.mode,'workMode':descriptor.work_mode}


class IoAEntryRail(AgentRail):
    priority=50

    def __init__(self):self.tools={};self.workers={}

    def init(self,agent):
        names={'ioa_assistant_entry':'assistant','ioa_provider_entry':'provider'}
        if any(agent.ability_manager.get(name) is not None for name in names):raise ValueError('IoA entry tool name conflict')
        root=Path(__file__).parent
        spec=importlib.util.spec_from_file_location('_ioa_jiuwen_native_entry',root/'entry.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        entry=module.Entry(root)
        worker_spec=importlib.util.spec_from_file_location('_ioa_jiuwen_native_worker',root/'worker.py')
        worker_module=importlib.util.module_from_spec(worker_spec);worker_spec.loader.exec_module(worker_module)
        for name,role in names.items():
            def make(selected):
                async def invoke(operation,operationId,arguments):
                    ctx=_context.get()
                    captured=None
                    if entry.requires_source(operation):captured=await _capture(ctx)
                    loop=asyncio.get_running_loop()
                    def connect(descriptor,reference,source):
                        def attach():
                            key=(descriptor['bootId'],descriptor['profile'],reference)
                            if key not in self.workers:
                                worker=worker_module.NativeWorker(captured[0],source,reference,
                                    lambda body:module._post(descriptor,body,control=True),prepare_turn)
                                self.workers[key]=worker;worker.start()
                        loop.call_soon_threadsafe(attach)
                    return await asyncio.to_thread(entry.invoke,selected,
                        {'operation':operation,'operationId':operationId,'arguments':arguments},
                        capture=lambda:captured[1],connect=connect)
                return invoke
            card=ToolCard(id=name,name=name,description='Invoke the bound '+role+' public IoA client; routing is captured by the native host.',
                input_params={'type':'object','properties':{'operation':{'type':'string'},'operationId':{'type':'string','maxLength':128},
                    'arguments':{'type':'object'}},'required':['operation','operationId','arguments'],'additionalProperties':False},
                stateless=False,parallel_safe=False,idempotent=False)
            tool=LocalFunction(card=card,func=make(role));agent.ability_manager.add_ability(card,tool);self.tools[name]=tool

    def uninit(self,agent):
        for worker in self.workers.values():worker.close()
        self.workers={}
        for name,tool in self.tools.items():
            if agent.ability_manager.get(name) is tool.card:agent.ability_manager.remove_ability(name)
        self.tools={}

    async def before_model_call(self,ctx):
        route=_route()
        if route is not None:_turns().tripwire(ctx,route)

    async def before_tool_call(self,ctx):
        route=_route()
        if route is not None:_turns().tripwire(ctx,route)
        if isinstance(ctx.inputs,ToolCallInputs) and ctx.inputs.tool_name in self.tools:
            setattr(ctx,'ioa_entry_token',_context.set(ctx))

    async def after_tool_call(self,ctx):
        token=getattr(ctx,'ioa_entry_token',None)
        if token is not None:
            setattr(ctx,'ioa_entry_token',None);_context.reset(token)

    async def on_tool_exception(self,ctx):await self.after_tool_call(ctx)


class IoAExecutionRail(AgentRail):
    """Per-request guard attached by public load_plugin_ability; inert for other requests.

    Defined after IoAEntryRail: the native extension loader instantiates the
    first `class *Rail(AgentRail)` in this file."""
    priority=1000

    def __init__(self,turn):self.turn=turn

    async def before_invoke(self,ctx):self.turn.on_invoke(ctx.agent,_route())

    async def before_model_call(self,ctx):
        if isinstance(ctx.inputs,ModelCallInputs):self.turn.on_model(ctx,_route())

    async def before_tool_call(self,ctx):
        if isinstance(ctx.inputs,ToolCallInputs):self.turn.on_tool(ctx,_route())

    async def after_tool_call(self,ctx):self.turn.after_tool(ctx)

    async def on_tool_exception(self,ctx):self.turn.after_tool(ctx)

    async def after_invoke(self,ctx):self.turn.after_invoke(_route())


async def prepare_turn(runtime,source,sid,command,callback):
    """Public prepare -> actual per-SID DeepAgent -> reserve; never mutates prompt/tools before send."""
    if sid==source['sessionId']:raise ValueError('native_business_session_required')
    actual=await runtime.describe_session(session_id=sid)
    if (actual is None or actual.session_id!=sid or actual.channel_id!='web' or
            actual.user_id!=source['userId'] or actual.project_dir!=source['projectDir'] or
            actual.project_id!=source['projectId']):raise ValueError('native_session_owner_mismatch')
    manager=runtime.agent_manager
    wrapper=manager.get_agent_for_session_nowait('web',sid)
    if wrapper is None:
        # Public selection/preparation only: this does not invoke/queue a turn.
        # The caller request ID remains private until send's actual snapshot.
        request=AgentRequest(request_id=command['plannedRequestId'],channel_id='web',session_id=sid,
            req_method=ReqMethod.CHAT_SEND,user_id=actual.user_id,
            params={'mode':actual.mode,'work_mode':actual.work_mode,
                    'project_dir':actual.project_dir,'project_id':actual.project_id})
        wrapper=await manager.get_agent_for_request(request)
    if wrapper is None:return None
    await wrapper.prepare_session(session_id=sid,channel_id='web',mode=actual.mode,project_dir=actual.project_dir)
    if manager.get_agent_for_session_nowait('web',sid) is not wrapper:raise ValueError('native_session_owner_ambiguous')
    deep=wrapper.get_live_session_instance(sid)
    # None includes permission isolation: accurately unavailable, no private access.
    if deep is None or deep.react_agent is None:return None
    if deep is wrapper.get_live_session_instance(source['sessionId']):raise ValueError('native_session_not_isolated')
    turns=_turns()
    turn=turns.PreparedTurn(sid,command['plannedRequestId'],command['execution'],_section,_tool,callback)
    turn.reserve(deep,deep.react_agent)
    try:record=await deep.load_plugin_ability(rails=[IoAExecutionRail(turn)])
    except BaseException:turn.close();raise
    return turns.Preparation(turn,lambda:deep.unload_extension(record))
