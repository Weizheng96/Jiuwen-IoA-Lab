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
    state=SimpleNamespace(mode='normal',calls=[],clients=[],options=[],terminal=None,tool_results=[])
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
                name='mcp__ioa_sdk__'+tool.name
                if state.mode=='foreign':name='Bash'
                attempts=[('call-original',{'text':'hello'})]
                if state.mode=='schema-retry':attempts=[('call-invalid',{'text':42}),('call-corrected',{'text':'hello'})]
                hook=self.options.hooks['PreToolUse'][0].hooks[0]
                for call,arguments in attempts:
                    result=await hook({'tool_name':name,'tool_input':arguments,'tool_use_id':call,
                                       'session_id':sid},call,{})
                    state.calls.append(result)
                    if result['hookSpecificOutput']['permissionDecision']=='allow':
                        try:
                            state.terminal=await tool.handler(arguments)
                            state.tool_results.append(state.terminal)
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
    second=h.run(replace(request,session_id=first.session_id,allow_native_tools=True));assert second.status=='completed'
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


def test_unprobed_round_is_rejected_without_files(boundary,tmp_path):
    with pytest.raises(RuntimeFault):host().run(turn(tmp_path))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('resumed',[False,True])
@pytest.mark.parametrize('permission',['allow','ask'])
def test_actual_native_permissions_are_rejected_before_files_or_effects(boundary,tmp_path,enabled,resumed,permission):
    h=host();h.probe();effects=[]
    request=turn(tmp_path,on_tool=lambda *a:effects.append(a) or {'receipt':'actual'})
    if resumed:
        first=h.run(request);assert first.status=='completed'
        request=replace(request,session_id=first.session_id)
    before={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    prior_effects=list(effects);prior_calls=len(boundary.calls);prior_clients=len(boundary.clients)
    request=replace(request,allow_native_tools=enabled,definition=replace(request.definition,native_tools=(('Bash',permission),)))
    with pytest.raises(RuntimeFault,match='native production tools are not supported'):h.run(request)
    assert effects==prior_effects and len(boundary.calls)==prior_calls and len(boundary.clients)==prior_clients
    assert {p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==before


def test_sdk_cannot_bypass_frozen_skill_reader(boundary,tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('Business rules')
    h=ClaudeCodeHost([sys.executable],model='test-model',skill_directories={'business':str(source)});h.probe();calls=[]
    request=turn(tmp_path/'round',on_tool=lambda *a:calls.append(a));request=replace(request,definition=replace(request.definition,skills=('business',)))
    result=h.run(request)
    assert result.status=='completed' and not calls
    assert json.loads(boundary.terminal['content'][0]['text'])['error']['code']=='read_business_skill'


@pytest.mark.parametrize('failure',['io','validation'])
def test_callback_failure_stays_unknown_even_if_sdk_returns_success(boundary,tmp_path,failure):
    boundary.mode='swallow';h=host();h.probe();effects=[]
    def invoke(*args):
        effects.append(args)
        if failure=='validation':
            from jsonschema.exceptions import ValidationError
            raise ValidationError('callback may already have changed external state')
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


def test_probe_reports_valid_skill_sources_to_provider_readiness(boundary,tmp_path):
    from types import SimpleNamespace
    from ioalab_sdk.provider.execution import ProviderExecution
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('private-skill-content',encoding='utf-8')
    refs=source/'references';refs.mkdir();(refs/'flow.md').write_text('Reference rules',encoding='utf-8')
    h=ClaudeCodeHost([sys.executable],model='test-model',skill_directories={'selected':str(source),'missing':str(tmp_path/'missing')})
    caps=h.probe()
    assert caps['available'] and caps['skillSources']=={'selected':True,'missing':False}
    assert caps['skillSourceErrors']=={'missing':'Restore the selected business skill source'}
    execution=SimpleNamespace(host=h,configs={'service':{'skills':['selected','missing','unconfigured']}})
    assert ProviderExecution.skill_errors(execution,'service')==['missing','unconfigured']
    assert 'private-skill-content' not in json.dumps(caps)
    caps['skillSources']['selected']=False
    assert h.capabilities()['skillSources']['selected'] is True
    (source/'SKILL.md').unlink()
    assert h.probe()['skillSources']=={'selected':False,'missing':False}
    assert ProviderExecution.skill_errors(execution,'service')==['selected','missing','unconfigured']


@pytest.mark.parametrize('invalid',['missing_main','empty','non_utf8','linked_root','linked_references','linked_document'])
def test_probe_keeps_invalid_skill_sources_unavailable(boundary,tmp_path,invalid):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('Business rules',encoding='utf-8')
    if invalid=='missing_main':(source/'SKILL.md').unlink()
    elif invalid=='empty':(source/'SKILL.md').write_bytes(b'')
    elif invalid=='non_utf8':(source/'SKILL.md').write_bytes(b'\xff')
    else:
        import subprocess
        target=tmp_path/'target';target.mkdir();(target/'SKILL.md').write_text('Target rules',encoding='utf-8')
        if invalid=='linked_root':link=tmp_path/'linked';destination=source;source=link
        elif invalid=='linked_references':link=source/'references';destination=target
        else:
            link=source/'SKILL.md';link.unlink();destination=target/'SKILL.md'
        directory=invalid!='linked_document'
        try:link.symlink_to(destination,target_is_directory=directory)
        except OSError:
            if sys.platform!='win32' or not directory:pytest.skip('Local environment cannot create this link')
            result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(destination)],capture_output=True)
            if result.returncode:pytest.skip('Local environment cannot create directory links')
    h=ClaudeCodeHost([sys.executable],model='test-model',skill_directories={'selected':str(source)})
    caps=h.probe()
    assert caps['available'] and caps['skillSources']=={'selected':False}
    assert caps['skillSourceErrors']=={'selected':'Restore the selected business skill source'}


def test_failed_host_reprobe_clears_previous_skill_availability(boundary,tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('Business rules',encoding='utf-8')
    h=ClaudeCodeHost([sys.executable],model='test-model',skill_directories={'selected':str(source)})
    assert h.probe()['skillSources']=={'selected':True}
    def unavailable(*args,**kwargs):raise OSError('CLI unavailable')
    monkeypatch.setattr('ioalab_claude_code.host.subprocess.run',unavailable)
    caps=h.probe()
    assert not caps['available'] and caps['skillSources']=={} and caps['skillSourceErrors']=={}
    assert h.capabilities()['skillSources']=={}


def test_invalid_tool_arguments_can_be_corrected_in_the_same_bound_round(boundary,tmp_path):
    boundary.mode='schema-retry';h=host();h.probe();effects=[];sessions=[]
    def invoke(name,args,call):
        assert sessions==['original-session']
        effects.append((name,args,call));return {'receipt':'actual-valid-result'}
    request=turn(tmp_path,on_session=sessions.append,on_tool=invoke)
    checked=replace(request.definition.tools[0],output_schema={'type':'object','properties':{'receipt':{'type':'string'}},'required':['receipt'],'additionalProperties':False})
    result=h.run(replace(request,definition=replace(request.definition,tools=(checked,))))
    assert result.status=='completed' and result.session_id=='original-session'
    assert effects==[('local.notice',{'text':'hello'},'call-corrected')]
    rejected=json.loads(boundary.tool_results[0]['content'][0]['text'])
    assert rejected['status']=='rejected' and rejected['error']['code']=='invalid_input'
    assert 'callback was not invoked' in rejected['error']['message']
    assert json.loads(boundary.tool_results[1]['content'][0]['text'])=={'receipt':'actual-valid-result'}
