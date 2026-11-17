"""Native Rail API consumers; native SDK objects are external boundary substitutes."""
import asyncio
import importlib.util
from importlib.resources import files
import sys
from types import ModuleType, SimpleNamespace

import pytest

from test_jiuwen_native_turn import agents, module as turns_module, execution


@pytest.fixture
def rail(monkeypatch):
    route=[None]
    class Inputs:
        def __init__(self,**values):self.__dict__.update(values)
    modules={
        'openjiuwen.core.foundation.tool':dict(LocalFunction=Inputs,ToolCard=Inputs,ToolInfo=Inputs),
        'openjiuwen.core.single_agent.prompts.builder':dict(PromptSection=Inputs),
        'openjiuwen.core.single_agent.rail.base':dict(AgentRail=type('AgentRail',(),{}),ModelCallInputs=Inputs,ToolCallInputs=Inputs),
        'openjiuwen.core.sys_operation.cwd':dict(get_cwd=lambda:None),
        'jiuwenswarm.common.context_keys':dict(JIUWENSWARM_CHANNEL_CONTEXT_KEY='channel'),
        'jiuwenswarm.agents.harness.common.tools.session_messaging_toolkit':dict(
            SESSION_MESSAGING_ROUTE_EXTRA_KEY='route',current_session_messaging_route=lambda:route[0]),
        'jiuwenswarm.runtime.context':dict(get_current_runtime=lambda:None),
        'jiuwenswarm.runtime.session_catalog':dict(SessionGetInput=Inputs),
        'jiuwenswarm.common.schema.agent':dict(AgentRequest=Inputs),
        'jiuwenswarm.common.schema.message':dict(ReqMethod=SimpleNamespace(CHAT_SEND='chat.send')),
    }
    for name,values in modules.items():
        value=ModuleType(name);value.__dict__.update(values);monkeypatch.setitem(sys.modules,name,value)
    path=files('ioalab_jiuwen').joinpath('resources','native','rail.py')
    spec=importlib.util.spec_from_file_location('ioa_native_rail_fixture',str(path))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    turns=turns_module();monkeypatch.setattr(value,'_turns',lambda:turns)
    return value,turns,route,Inputs


def public_runtime(tmp_path,*,isolated=True):
    _,react=agents();_,source_react=agents();calls=[]
    class Deep:
        react_agent=react
        async def load_plugin_ability(self,*,rails):
            calls.append(('load',rails));return 'original-load-record'
        async def unload_extension(self,record):calls.append(('unload',record))
    deep=Deep();prepared=[False]
    class Wrapper:
        async def prepare_session(self,**kwargs):calls.append(('prepare',kwargs));prepared[0]=True
        def get_live_session_instance(self,sid):
            if sid=='source':return SimpleNamespace(react_agent=source_react)
            return deep if isolated else None
    wrapper=Wrapper()
    class Manager:
        def get_agent_for_session_nowait(self,channel,sid):return wrapper if prepared[0] else None
        async def get_agent_for_request(self,request):calls.append(('select',request));return wrapper
    class Runtime:
        agent_manager=Manager()
        async def describe_session(self,*,session_id):
            return SimpleNamespace(session_id=session_id,channel_id='web',user_id='native-owner',
                project_dir=str(tmp_path),project_id='project',mode='agent.code.normal',work_mode='code')
        async def send_session_message(self,**_):raise AssertionError('preparation must not dispatch')
    source={'sessionId':'source','userId':'native-owner','projectDir':str(tmp_path),'projectId':'project'}
    command={'plannedRequestId':'private-planned','execution':execution()}
    return Runtime(),source,command,deep,calls


def test_public_native_mode_selection_is_not_inferred_from_mode_shape(rail,tmp_path):
    value,turns,_,_=rail
    async def scenario():
        runtime,source,command,deep,calls=public_runtime(tmp_path)
        async def callback(*_):return {'value':'echo','error':None}
        handle=await value.prepare_turn(runtime,source,'business',command,callback)
        request=calls[0][1]
        assert request.params=={'mode':'agent.code.normal','work_mode':'code','project_dir':str(tmp_path),'project_id':'project'}
        assert (request.session_id,request.user_id,request.request_id)==('business','native-owner','private-planned')
        assert deep.react_agent.prompt_builder.sections.keys()=={'user'}
        assert not deep.react_agent.ability_manager.tools
        assert handle.turn.key==('business','private-planned') and not handle.turn.installed
        await handle.finish(True)
        assert calls[-1]==('unload','original-load-record') and not turns.PENDING
    asyncio.run(scenario())


def test_public_permission_isolated_agent_is_unavailable_before_dispatch(rail,tmp_path):
    value,turns,_,_=rail
    async def scenario():
        runtime,source,command,_,calls=public_runtime(tmp_path,isolated=False)
        assert await value.prepare_turn(runtime,source,'business',command,None) is None
        assert [c[0] for c in calls]==['select','prepare'] and not turns.PENDING
    asyncio.run(scenario())


def test_actual_native_owner_mismatch_precedes_agent_preparation(rail,tmp_path):
    value,turns,_,_=rail
    async def scenario():
        runtime,source,command,_,calls=public_runtime(tmp_path);source['userId']='other-owner'
        with pytest.raises(ValueError,match='native_session_owner_mismatch'):
            await value.prepare_turn(runtime,source,'business',command,None)
        assert not calls and not turns.PENDING
    asyncio.run(scenario())


def test_public_guard_ignores_earlier_busy_turn_then_installs_exact_request(rail,tmp_path):
    value,turns,route,Inputs=rail
    async def scenario():
        runtime,source,command,deep,calls=public_runtime(tmp_path)
        handle=await value.prepare_turn(runtime,source,'business',command,None)
        guard=calls[-1][1][0]
        route[0]=SimpleNamespace(session_id='business',request_id='earlier-user')
        await guard.before_invoke(SimpleNamespace(agent=deep))
        assert deep.react_agent.prompt_builder.sections.keys()=={'user'}
        route[0]=SimpleNamespace(session_id='business',request_id='private-planned')
        await guard.before_invoke(SimpleNamespace(agent=deep))
        assert deep.react_agent.prompt_builder.get_section(turns.SECTION).content=={'cn':'frozen system rules','en':'frozen system rules'}
        await handle.finish(True)
        assert deep.react_agent.prompt_builder.sections.keys()=={'user'}
    asyncio.run(scenario())


def test_entry_tool_context_tokens_are_per_call_even_with_shared_extra(rail):
    value,_,_,Inputs=rail
    async def scenario():
        entry=value.IoAEntryRail();entry.tools={'ioa_assistant_entry':object()}
        shared={};ready=asyncio.Event();count=[0]
        async def call(exception):
            ctx=SimpleNamespace(inputs=Inputs(tool_name='ioa_assistant_entry'),extra=shared)
            await entry.before_tool_call(ctx);count[0]+=1
            if count[0]==2:ready.set()
            await ready.wait()
            assert value._context.get() is ctx
            if exception:await entry.on_tool_exception(ctx)
            else:await entry.after_tool_call(ctx)
            assert value._context.get() is None
        await asyncio.gather(call(False),call(True))
        assert 'ioa_entry_token' not in shared
    asyncio.run(scenario())
