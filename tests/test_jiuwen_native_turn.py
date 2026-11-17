"""Request-owned definition/tool gate at the native Rail seam.

The native Agent, prompt builder, ability manager and callback ctx are external
boundary substitutes; installed jiuwenswarm/openjiuwen bodies are not run here.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from importlib.resources import files
import importlib.util
from types import SimpleNamespace

import pytest


def module():
    path=files('ioalab_jiuwen').joinpath('resources','native','turn.py')
    spec=importlib.util.spec_from_file_location('native_turn_fixture',str(path))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class Builder:
    def __init__(self):self.sections={'user':SimpleNamespace(name='user',content='original user settings')}
    def get_section(self,name):return self.sections.get(name)
    def add_section(self,section):self.sections[section.name]=section
    def remove_section(self,name):self.sections.pop(name,None)


class Manager:
    def __init__(self):self.tools={}
    def get(self,name):return self.tools[name][0] if name in self.tools else None
    def add_ability(self,card,tool):self.tools[card.name]=(card,tool)
    def remove_ability(self,name):self.tools.pop(name,None)
    async def call(self,inputs):
        # External ability manager: consumes the public rewritten ToolCallInputs.
        return await self.tools[inputs.tool_name][1](**inputs.tool_args)


class Ctx:
    def __init__(self,agent,inputs):self.agent=agent;self.inputs=inputs;self.finished=None
    def request_force_finish(self,result):self.finished=result


def agents():
    react=SimpleNamespace(prompt_builder=Builder(),ability_manager=Manager())
    return SimpleNamespace(react_agent=react),react


def section(name,text):return SimpleNamespace(name=name,content=text)
def tool(declaration,func):
    card=SimpleNamespace(name=declaration['name'])
    return SimpleNamespace(card=card,ability=func,info=SimpleNamespace(name=card.name))


ROUTE=('actual-sid','original-request')
ECHO={'name':'sdk_echo','description':'echo','input_schema':{'type':'object'},'output_schema':None}


def execution(enabled=False,**changes):
    value={'kind':'business','definition':{'instructions':'frozen system rules'},'tools':[ECHO],
           'authorization':{'nativeTools':{},'nativeToolsEnabled':enabled},'deadline':None}
    return {**value,**changes}


def prepared(value,callback=None,**changes):
    owner,react=agents()
    async def default(*_):return {'value':'unused','error':None}
    turn=value.PreparedTurn(*ROUTE,execution(**changes),section,tool,callback or default)
    turn.reserve(owner,react);return turn,owner,react


def model(react,*names):return Ctx(react,SimpleNamespace(tools=[SimpleNamespace(name=n) for n in names]))
def call(react,name,call_id='native-call'):
    return Ctx(react,SimpleNamespace(tool_name=name,tool_args={'text':'ok'},tool_call=SimpleNamespace(id=call_id)))


def test_reserve_leaves_busy_user_turn_unchanged_and_only_exact_invocation_installs():
    async def scenario():
        value=module();turn,owner,react=prepared(value)
        assert set(react.prompt_builder.sections)=={'user'} and not react.ability_manager.tools
        turn.on_invoke(owner,('actual-sid','earlier-user-request'))
        user=model(react,'native_write');turn.on_model(user,('actual-sid','earlier-user-request'))
        assert set(react.prompt_builder.sections)=={'user'} and user.finished is None
        assert [t.name for t in user.inputs.tools]==['native_write']
        turn.on_invoke(owner,ROUTE)
        assert react.prompt_builder.get_section(value.SECTION).content=='frozen system rules'
        turn.after_invoke(ROUTE)
        assert set(react.prompt_builder.sections)=={'user'} and not react.ability_manager.tools
        assert react.prompt_builder.sections['user'].content=='original user settings'
    asyncio.run(scenario())


def test_collision_is_rejected_before_send_and_late_collision_ends_before_model():
    async def scenario():
        value=module();turn,owner,react=prepared(value)
        with pytest.raises(ValueError):value.PreparedTurn(*ROUTE,execution(),section,tool,None).reserve(owner,react)
        other=value.PreparedTurn('actual-sid','another',execution(),section,tool,None)
        with pytest.raises(ValueError):
            react.ability_manager.add_ability(SimpleNamespace(name='sdk_echo'),None)
            other.reserve(SimpleNamespace(),react)
        # The original request was accepted; a reserve-to-install race is not zero dispatch.
        turn.on_invoke(owner,ROUTE)
        first=model(react,'sdk_echo');turn.on_model(first,ROUTE)
        assert first.finished['error']['code']=='native_definition_or_tool_collision'
        assert value.SECTION not in react.prompt_builder.sections
    asyncio.run(scenario())


def test_early_sdk_callback_waits_for_exact_ack_and_false_never_calls_sdk():
    async def scenario():
        value=module();calls=[]
        async def callback(*args):calls.append(args);return {'value':{'echo':'ok'},'error':None}
        turn,owner,react=prepared(value,callback);turn.on_invoke(owner,ROUTE)
        ctx=call(react,'sdk_echo');turn.on_tool(ctx,ROUTE)
        pending=asyncio.create_task(react.ability_manager.call(ctx.inputs))
        await asyncio.sleep(0)
        assert not pending.done() and not calls
        turn.accept(False)
        assert (await pending)['error']['code']=='native_binding_unacknowledged' and not calls
        turn.after_tool(ctx)
        turn.accept(True);ctx=call(react,'sdk_echo','second-call');turn.on_tool(ctx,ROUTE)
        assert await react.ability_manager.call(ctx.inputs)=={'echo':'ok'}
        assert calls==[('second-call','sdk_echo',{'text':'ok'})]
        turn.after_tool(ctx);turn.close()
    asyncio.run(scenario())


def test_cancelled_invocation_cleans_before_next_owner():
    async def scenario():
        value=module();turn,owner,react=prepared(value);ready=asyncio.Event()
        async def invocation():
            turn.on_invoke(owner,ROUTE);ready.set();await asyncio.Event().wait()
        task=asyncio.create_task(invocation());await ready.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await asyncio.sleep(0)
        assert set(react.prompt_builder.sections)=={'user'} and not react.ability_manager.tools
        follow=value.PreparedTurn('actual-sid','next',execution(),section,tool,None)
        follow.reserve(owner,react);follow.close()
    asyncio.run(scenario())



def test_parallel_tool_tasks_ending_keep_the_running_invocation_definition():
    async def scenario():
        value=module();turn,owner,react=prepared(value);ready=asyncio.Event();stop=asyncio.Event()
        async def invocation():
            turn.on_invoke(owner,ROUTE);ready.set();await stop.wait()
        owner_task=asyncio.create_task(invocation());await ready.wait()
        async def parallel(call_id):
            ctx=call(react,'native_write',call_id);turn.on_tool(ctx,ROUTE);turn.after_tool(ctx)
        await asyncio.gather(parallel('one'),parallel('two'));await asyncio.sleep(0)
        assert turn.installed and react.prompt_builder.get_section(value.SECTION) is not None
        stop.set();await owner_task;await asyncio.sleep(0)
        assert not turn.installed and set(react.prompt_builder.sections)=={'user'}
    asyncio.run(scenario())

@pytest.mark.parametrize('enabled,expected',[(False,['sdk_echo']),(True,['native_write','sdk_echo'])])
def test_native_tool_phase_gates_model_window_and_rewrites_one_call(enabled,expected):
    async def scenario():
        value=module();effects=[];turn,owner,react=prepared(value,enabled=enabled);turn.on_invoke(owner,ROUTE)
        async def native_body(**_):effects.append('native effect');return 'effect'
        react.ability_manager.add_ability(SimpleNamespace(name='native_write'),native_body)
        window=model(react,'native_write','sdk_echo',value.DENY);turn.on_model(window,ROUTE)
        assert [t.name for t in window.inputs.tools]==expected and window.finished is None
        ctx=call(react,'native_write');turn.on_tool(ctx,ROUTE)
        result=await react.ability_manager.call(ctx.inputs)
        if enabled:assert result=='effect' and effects==['native effect']
        else:assert result['error']['code']=='native_tool_not_authorized' and not effects
        # One ordinary denial never ends the Agent; later tools continue.
        assert ctx.finished is None and turn.installed
        turn.close()
    asyncio.run(scenario())


def test_native_tool_phase_requires_trusted_boolean():
    value=module()
    with pytest.raises(ValueError):value.PreparedTurn(*ROUTE,execution(enabled=1),section,tool,None)
    turn=value.PreparedTurn(*ROUTE,execution(authorization={'nativeTools':{}}),section,tool,None)
    assert turn.native_tools_enabled is False


def test_original_deadline_ends_invocation_at_next_model_hook():
    async def scenario():
        value=module();now=[datetime(2026,1,1,tzinfo=timezone.utc)]
        owner,react=agents()
        turn=value.PreparedTurn(*ROUTE,execution(deadline='2026-01-01T00:10:00Z'),section,tool,None,clock=lambda:now[0])
        turn.reserve(owner,react);turn.on_invoke(owner,ROUTE)
        first=model(react,'sdk_echo');turn.on_model(first,ROUTE);assert first.finished is None
        now[0]+=timedelta(minutes=10)
        late=model(react,'sdk_echo');turn.on_model(late,ROUTE)
        assert late.finished['error']['code']=='native_deadline_expired'
        turn.close()
    asyncio.run(scenario())


def test_prepared_request_invoked_on_another_agent_never_reaches_model():
    async def scenario():
        value=module();turn,owner,react=prepared(value)
        _,other=agents();ctx=model(other,'native_write')
        value.tripwire(ctx,ROUTE)
        assert ctx.finished['error']['code']=='native_agent_mismatch'
        unrelated=model(other,'native_write');value.tripwire(unrelated,('actual-sid','user-request'))
        assert unrelated.finished is None
        turn.close()
    asyncio.run(scenario())


def test_unknown_dispatch_keeps_inert_guard_until_original_invocation_ends():
    async def scenario():
        value=module();turn,owner,react=prepared(value);unloaded=[]
        async def unload():unloaded.append(True)
        handle=value.Preparation(turn,unload)
        await handle.finish(False)
        assert not unloaded and value.PENDING[ROUTE] is turn
        async def invocation():turn.on_invoke(owner,ROUTE)
        await asyncio.create_task(invocation());await asyncio.sleep(0);await asyncio.sleep(0)
        assert unloaded==[True] and ROUTE not in value.PENDING
        second,owner2,_=prepared(value);other=value.Preparation(second,unload)
        await other.finish(True)
        assert unloaded==[True,True]
    asyncio.run(scenario())


def test_preparation_reports_guard_failure_without_rewriting_machine_facts():
    async def scenario():
        value=module();turn,owner,react=prepared(value)
        async def unload():pass
        handle=value.Preparation(turn,unload)
        assert handle.failure is None
        react.ability_manager.add_ability(SimpleNamespace(name='sdk_echo'),None)
        turn.on_invoke(owner,ROUTE)
        ctx=model(react,'sdk_echo');turn.on_model(ctx,ROUTE)
        assert handle.failure=='native_definition_or_tool_collision'
        assert ctx.finished['error']['code']==handle.failure
        await handle.finish(True)
        assert handle.failure=='native_definition_or_tool_collision'
    asyncio.run(scenario())


def test_proof_evidence_records_only_phases_ids_timestamps_and_booleans(tmp_path):
    source=files('ioalab_jiuwen').joinpath('resources','native','turn.py')
    assert module().EVIDENCE is None  # the packaged resource never records
    (tmp_path/'turn.py').write_bytes(source.read_bytes());(tmp_path/'ioa-evidence.enabled').write_text('')
    spec=importlib.util.spec_from_file_location('native_turn_evidence',str(tmp_path/'turn.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    secrets=('SECRET-INSTRUCTIONS','SECRET-ARGUMENT','SECRET-OUTPUT','SECRET-INPUT')
    async def scenario():
        async def callback(*_):return {'value':{'echo':'SECRET-OUTPUT'},'error':None}
        owner,react=agents()
        turn=value.PreparedTurn(*ROUTE,execution(definition={'instructions':'SECRET-INSTRUCTIONS'}),section,tool,callback)
        turn.reserve(owner,react);turn.on_invoke(owner,ROUTE)
        turn.on_model(model(react,'native_write','sdk_echo'),ROUTE)
        native=Ctx(react,SimpleNamespace(tool_name='native_write',tool_args={'path':'SECRET-ARGUMENT'},tool_call=SimpleNamespace(id='c0')))
        turn.on_tool(native,ROUTE)
        ctx=Ctx(react,SimpleNamespace(tool_name='sdk_echo',tool_args={'text':'SECRET-ARGUMENT'},tool_call=SimpleNamespace(id='c1')))
        turn.on_tool(ctx,ROUTE);turn.accept(True)
        assert await react.ability_manager.call(ctx.inputs)=={'echo':'SECRET-OUTPUT'}
        turn.after_tool(ctx)
        async def unload():pass
        await value.Preparation(turn,unload).finish(True)
        _,other=agents();value.PENDING[('s','r')]=value.PreparedTurn('s','r',execution(),section,tool,callback)
        value.PENDING[('s','r')].agent=react;value.tripwire(model(other,'x'),('s','r'))
    asyncio.run(scenario())
    text=(tmp_path/'ioa-evidence.jsonl').read_text(encoding='utf-8')
    assert not any(secret in text for secret in secrets)
    records=[__import__('json').loads(line) for line in text.splitlines()]
    phases=[r['phase'] for r in records]
    for expected in ('reserve','installed','model_window','tool_native','tool_sdk','binding_ack',
                     'binding_wait_start','binding_wait_end','callback_sent','callback_done','finish','cleanup','unload','tripwire'):
        assert expected in phases
    identity={'phase','session','request'}
    for record in records:
        for key,item in record.items():
            if key in identity:assert isinstance(item,str)
            elif key in ('wall','mono'):assert type(item) is float
            else:assert type(item) in (bool,int)
    window=next(r for r in records if r['phase']=='model_window')
    assert window['sdk_tool'] is True and window['native_tools'] is False and window['deny_tool'] is False
    assert next(r for r in records if r['phase']=='tool_native')['rewritten'] is True
