"""Public AgentHost contract at the official SDK external boundary."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import pytest
import claude_agent_sdk as sdk
from ioalab_claude_code import ClaudeCodeHost
from ioalab_sdk.runtime import AgentDefinition, HostTurn, ToolBinding, RuntimeFault


@pytest.fixture
def boundary(monkeypatch):
    state=SimpleNamespace(mode='normal',calls=[],clients=[],options=[],terminal=None)
    class Client:
        def __init__(self, options):self.options=options;self.interrupted=asyncio.Event();state.options.append(options)
        async def __aenter__(self):state.clients.append(self);return self
        async def __aexit__(self,*args):pass
        async def get_server_info(self):return {'privateConfiguration':'private-sentinel','models':[{'value':'test-model','resolvedModel':'test-model','displayName':'Test'}, {'value':'default','resolvedModel':'test-model','displayName':'Default (recommended)'}]}
        async def query(self,prompt):self.prompt=prompt
        async def interrupt(self):self.interrupted.set()
        async def receive_response(self):
            sid=self.options.resume or 'original-session'
            yield sdk.SystemMessage(subtype='init',data={'session_id':sid})
            if state.mode=='wait':await self.interrupted.wait()
            elif state.mode=='drop':raise OSError('lost stream')
            else:
                tool=self.options.mcp_servers['ioa_sdk']['tools'][0]
                name='mcp__ioa_sdk__'+tool.name;arguments={'text':'hello'}
                if state.mode=='foreign':name='Bash'
                hook=self.options.hooks['PreToolUse'][0].hooks[0]
                result=await hook({'tool_name':name,'tool_input':arguments,'tool_use_id':'call-original',
                                   'session_id':sid},'call-original',{})
                state.calls.append(result)
                if result['hookSpecificOutput']['permissionDecision']=='allow':
                    try:state.terminal=await tool.handler(arguments)
                    except Exception:
                        if state.mode!='swallow':raise
            yield sdk.ResultMessage(subtype='error_during_execution' if state.mode=='wait' else 'success',
                duration_ms=1,duration_api_ms=1,is_error=state.mode=='wait',num_turns=1,session_id=sid,
                usage={'input_tokens':3},stop_reason=None if state.mode=='wait' else 'end_turn',
                terminal_reason='aborted_streaming' if state.mode=='wait' else None)
    monkeypatch.setattr(sdk,'ClaudeSDKClient',Client)
    monkeypatch.setattr(sdk,'create_sdk_mcp_server',lambda **kwargs:kwargs)
    original_run = __import__('subprocess').run
    monkeypatch.setattr('ioalab_claude_code.host.subprocess.run',lambda args,**kwargs: SimpleNamespace(returncode=0,stdout='2.test (Claude Code)') if args[-1]=='--version' else original_run(args,**kwargs))
    return state


def host():return ClaudeCodeHost([sys.executable],model='test-model')
def turn(tmp_path,**kwargs):
    tool=ToolBinding('local.notice','Return actual JSON',{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False},lambda *_:None)
    return HostTurn('operation','actor','interaction',AgentDefinition('test','Only local.notice',(tool,)),'Call local.notice',str(tmp_path),**kwargs)


def test_original_session_is_bound_before_tool_and_resumed_with_fixed_permissions(boundary,tmp_path):
    h=host();assert h.probe()['available'];sessions=[];calls=[]
    def invoke(name,args,call):
        assert sessions==['original-session'];assert json.loads((tmp_path/'claude-session.json').read_text())['sessionId']==sessions[0]
        calls.append((name,args,call));return {'receipt':'actual-json','status':'completed'}
    request=turn(tmp_path,on_session=sessions.append,on_tool=invoke)
    first=h.run(request);assert first.status=='completed'
    sessions.clear();h=host();h.probe()
    second=h.run(replace(request,session_id=first.session_id));assert second.status=='completed'
    assert calls==[('local.notice',{'text':'hello'},'call-original')]*2
    assert json.loads(boundary.terminal['content'][0]['text'])=={'receipt':'actual-json','status':'completed'}
    options=boundary.options[-1]
    assert options.tools==[] and options.skills==[] and options.setting_sources==[] and options.strict_mcp_config
    assert options.permission_mode=='dontAsk' and options.plugins==[] and options.fallback_model is None
    assert options.resume=='original-session' and options.continue_conversation is False


def test_native_tool_hook_denies_even_without_permission_callback(boundary,tmp_path):
    boundary.mode='foreign';h=host();h.probe();calls=[]
    result=h.run(turn(tmp_path,on_tool=lambda *args:calls.append(args)))
    assert result.status=='completed' and not calls
    assert boundary.calls[-1]['hookSpecificOutput']['permissionDecision']=='deny'


def test_missing_binding_definition_change_and_unknown_round_are_not_retried(boundary,tmp_path):
    h=host();h.probe();request=turn(tmp_path,on_tool=lambda *_:{'receipt':1})
    result=h.run(request)
    with pytest.raises(RuntimeFault,match='original Claude Session'):
        h.run(replace(request,session_id=result.session_id,definition=replace(request.definition,instructions='changed')))
    (tmp_path/'claude-session.json').unlink()
    with pytest.raises(RuntimeFault,match='Restore'):
        h.run(replace(request,session_id=result.session_id))
    other=tmp_path/'dropped';boundary.mode='drop';lost=h.run(replace(request,workspace=str(other)))
    assert lost.status=='unknown' and lost.session_id=='original-session'
    with pytest.raises(RuntimeFault,match='original operation'):h.run(replace(request,workspace=str(other)))


def test_real_interrupt_waits_for_original_terminal(boundary,tmp_path):
    boundary.mode='wait';h=host();h.probe();bound=threading.Event();results=[]
    worker=threading.Thread(target=lambda:results.append(h.run(turn(tmp_path,on_session=lambda _:bound.set()))))
    worker.start();assert bound.wait(5)
    assert h.cancel('operation') is True
    worker.join(5);assert not worker.is_alive()
    assert results[0].status=='cancelled' and results[0].session_id=='original-session'
    assert h.cancel('operation') is False


def test_unprobed_or_native_enabled_round_is_rejected_without_files(boundary,tmp_path):
    h=host()
    with pytest.raises(RuntimeFault):h.run(turn(tmp_path))
    h.probe()
    with pytest.raises(RuntimeFault):h.run(turn(tmp_path,allow_native_tools=True))
    assert not list(tmp_path.iterdir())


def test_sdk_cannot_bypass_frozen_skill_reader(boundary,tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('Business rules')
    h=ClaudeCodeHost([sys.executable],model='test-model',skill_directories={'business':str(source)});h.probe();calls=[]
    request=turn(tmp_path/'round',on_tool=lambda *a:calls.append(a));request=replace(request,definition=replace(request.definition,skills=('business',)))
    result=h.run(request)
    assert result.status=='completed' and not calls
    assert json.loads(boundary.terminal['content'][0]['text'])['error']['code']=='read_business_skill'


def test_callback_failure_stays_unknown_even_if_sdk_returns_success(boundary,tmp_path):
    boundary.mode='swallow';h=host();h.probe();effects=[]
    def invoke(*args):
        effects.append(args)
        raise OSError('effect result was lost')
    result=h.run(turn(tmp_path,on_tool=invoke))
    assert result.status=='unknown' and result.error['code']=='host_unknown' and len(effects)==1
    with pytest.raises(RuntimeFault):h.run(turn(tmp_path,on_tool=invoke))
    assert len(effects)==1


def test_selected_output_contract_is_enforced_after_actual_tool_effect(boundary,tmp_path):
    boundary.mode='swallow';h=host();h.probe();request=turn(tmp_path,on_tool=lambda *_:{'unexpected':'value'})
    original=request.definition.tools[0]
    checked=replace(original,output_schema={'type':'object','properties':{'receipt':{'type':'string'}},'required':['receipt'],'additionalProperties':False})
    result=h.run(replace(request,definition=replace(request.definition,tools=(checked,))))
    assert result.status=='unknown' and result.error['code']=='host_unknown'


@pytest.mark.parametrize('invalid',[[], '', 0, False])
def test_invalid_empty_skill_sources_are_not_coerced_to_mapping(invalid):
    with pytest.raises(RuntimeFault):ClaudeCodeHost([sys.executable],model='test-model',skill_directories=invalid)


def test_model_catalog_exposes_public_default_alias_without_private_initialization(boundary):
    h=host();assert h.model_catalog()=={'models':[],'default':None}
    h.probe();catalog=h.model_catalog()
    assert catalog['default']=='default' and catalog['models'][0]['resolvedModel']=='test-model'
    assert 'private-sentinel' not in json.dumps(catalog)
    catalog['models'].clear();assert h.model_catalog()['models']
