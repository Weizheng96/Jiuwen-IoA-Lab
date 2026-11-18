from ioalab_sdk.runtime import SubmissionQuery
"""Explicit SDK S01 at the public external SDK boundary; no native CLI/model."""
import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
import hashlib
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import pytest
import claude_agent_sdk as sdk
from ioalab_claude_code import ClaudeCodeHost
from ioalab_sdk.runtime import RuntimeFault
from ioalab_sdk.runtime import (BindingAck,HostDefinition,HostExecution,HostInput,
    HostRegistration,HostResource,HostToolDeclaration,HostToolResult,SubmissionAttempt)

@pytest.fixture
def boundary(monkeypatch):
    state=SimpleNamespace(mode='normal',clients=[],options=[],calls=[],results=[],queries=[],version_calls=[],
        interrupt_mode='ack',interrupt_started=threading.Event(),interrupt_release=threading.Event(),
        interrupt_acked=threading.Event(),terminal_release=None,interrupt_calls=0,structured=None,
        text='I delivered it (content only)')
    class Client:
        def __init__(self,options):self.options=options;self.interrupted=asyncio.Event();state.options.append(options)
        async def __aenter__(self):state.clients.append(self);return self
        async def __aexit__(self,*args):pass
        async def get_server_info(self):return {'private':'SECRET_CONFIG','models':[{'value':'test-model'}]}
        async def query(self,prompt):
            self.prompt=[v async for v in prompt][0];state.queries.append(self.prompt)
            self.options.stderr('502 SECRET_KEY PRIVATE_USER_BODY')
        async def interrupt(self):
            state.interrupt_calls+=1;state.interrupt_started.set()
            if state.interrupt_mode=='error':raise OSError('control response lost')
            if state.interrupt_mode=='pending':
                while not state.interrupt_release.is_set():await asyncio.sleep(.005)
            state.interrupt_acked.set();self.interrupted.set()
        async def receive_response(self):
            sid=self.options.resume or 'original-session'
            yield sdk.SystemMessage(subtype='init',data={'session_id':sid})
            if state.mode!='no-echo':
                yield sdk.UserMessage(content=self.prompt['message']['content'],uuid=self.prompt['uuid'],
                    parent_tool_use_id='parent' if state.mode=='nested-echo' else None,
                    origin={'kind':'task-notification'} if state.mode=='injected-echo' else None)
            if state.mode=='drop':raise OSError('SECRET_PROVIDER_BODY')
            if state.mode=='wait':
                await self.interrupted.wait()
                while state.terminal_release is not None and not state.terminal_release.is_set():await asyncio.sleep(.005)
            else:
                tool=self.options.mcp_servers['ioa_sdk']['tools'][0]
                name='mcp__ioa_sdk__'+tool.name
                if state.mode=='foreign':name='Bash'
                attempts=[('call-original',{'text':'hello'})]
                if state.mode=='schema-retry':attempts=[('invalid',{'text':42}),('corrected',{'text':'hello'})]
                hook=self.options.hooks['PreToolUse'][0].hooks[0]
                for call,args in attempts:
                    decision=await hook({'session_id':sid,'tool_name':name,'tool_use_id':call,'tool_input':args},call,{})
                    state.calls.append(decision)
                    if decision.get('hookSpecificOutput',{}).get('permissionDecision')=='allow':
                        try:state.results.append(await tool.handler(args))
                        except Exception:
                            if state.mode!='swallow':raise
            if state.mode=='lost-terminal':return
            yield sdk.ResultMessage(subtype='error_during_execution' if state.mode=='wait' else 'success',
                duration_ms=1,duration_api_ms=1,is_error=state.mode=='wait',num_turns=1,
                session_id='foreign-session' if state.mode=='wrong-sid' else sid,usage={'input_tokens':3},
                result=state.text,structured_output=state.structured,uuid=None if state.mode=='no-result-id' else 'result-uuid',
                origin={'kind':'task-notification'} if state.mode=='injected-result' else None,
                terminal_reason=None if state.mode=='end-turn-only' else 'aborted_streaming' if state.mode=='wait' else 'completed',
                stop_reason='end_turn')
    monkeypatch.setattr(sdk,'ClaudeSDKClient',Client)
    monkeypatch.setattr(sdk,'create_sdk_mcp_server',lambda **kwargs:kwargs)
    original_run=__import__('subprocess').run
    def version(args,*rest,**kwargs):
        # Any CLI start (version command or handshake) from check is a regression.
        if isinstance(args,(list,tuple)) and args and args[0]==sys.executable:
            state.version_calls.append(list(args));return SimpleNamespace(returncode=0,stdout='2.test (Claude Code)')
        return original_run(args,*rest,**kwargs)
    monkeypatch.setattr('subprocess.run',version)
    return state

def host():return ClaudeCodeHost([sys.executable],model='test-model')
def execution(path):
    tool=HostToolDeclaration('local.notice','Return actual JSON',{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False})
    return HostExecution('actor','profile','business','interaction','job',HostDefinition('fixed','1','digest','Only local.notice'),tools=(tool,),workspace=str(path))
def input(attempt='attempt',text='Call local.notice'):return HostInput(text,attempt=SubmissionAttempt(attempt,'scope','actor','profile'))
def ack(event):return BindingAck(event.result.session_id,event.result.request_id) if event.kind=='session' else None

def test_submit_has_static_registration_no_probe_gate_and_no_legacy_methods(boundary,tmp_path):
    h=host();effects=[]
    result=h.submit('operation',input(),execution=execution(tmp_path),on_event=ack,on_tool=lambda c:effects.append(c) or HostToolResult({'receipt':1}))
    assert isinstance(h.registration,HostRegistration) and result.state=='completed' and result.terminal
    assert h.registration.readonly_context=='isolated_session'
    assert result.request_id==boundary.queries[0]['uuid'] and result.request_id!='result-uuid'
    assert result.terminal.request_id==result.request_id and len(effects)==1 and not boundary.version_calls
    assert not any(hasattr(h,name) for name in ('run','probe','capabilities','model_catalog'))
    assert result.output=='I delivered it (content only)' and result.proposal is None
    options=boundary.options[-1]
    assert options.tools is None and options.skills is None and options.setting_sources is None
    assert not options.strict_mcp_config and options.fallback_model is None and options.permission_mode is None
    assert options.can_use_tool is None
    assert options.hooks['PreToolUse'][0].matcher=='^mcp__ioa_sdk__'
    assert options.extra_args=={'replay-user-messages':None}

def test_binding_ack_before_callback_and_same_session_resume(boundary,tmp_path):
    h=host();events=[];calls=[]
    def bind(event):events.append(event);return ack(event)
    def tool(call):assert events[0].kind=='session';calls.append(call);return HostToolResult({'receipt':len(calls)})
    first=h.submit('one',input(),execution=execution(tmp_path),on_event=bind,on_tool=tool)
    second=h.submit('two',input('second'),first.session_id,execution=execution(tmp_path),on_event=ack,on_tool=lambda c:HostToolResult({'receipt':2}))
    assert first.state==second.state=='completed' and second.session_id==first.session_id
    assert boundary.options[-1].resume==first.session_id and len(boundary.queries)==2
    assert first.request_id!=second.request_id

@pytest.mark.parametrize('mode',['no-echo','wrong-sid','no-result-id','end-turn-only','lost-terminal','drop',
                                  'injected-result','injected-echo','nested-echo'])
def test_missing_real_request_or_terminal_stays_unknown(boundary,tmp_path,mode):
    boundary.mode=mode;calls=[]
    result=host().submit('op',input(),execution=execution(tmp_path),on_event=ack,on_tool=lambda c:calls.append(c) or HostToolResult(1))
    assert result.state=='unknown' and result.terminal is None
    if mode in ('no-echo','injected-echo','nested-echo'):assert result.request_id is None and not calls

@pytest.mark.parametrize('bad_ack',[None,BindingAck('other','other')])
def test_no_binding_ack_denies_effect(boundary,tmp_path,bad_ack):
    calls=[]
    result=host().submit('op',input(),execution=execution(tmp_path),on_event=lambda e:bad_ack,on_tool=lambda c:calls.append(c))
    assert result.state=='unknown' and not calls

def test_schema_rejection_before_effect_allows_new_call(boundary,tmp_path):
    boundary.mode='schema-retry';calls=[]
    result=host().submit('op',input(),execution=execution(tmp_path),on_event=ack,on_tool=lambda c:calls.append(c) or HostToolResult({'receipt':1}))
    assert result.state=='completed' and [c.call_id for c in calls]==['corrected']
    assert json.loads(boundary.results[0]['content'][0]['text'])['error']['code']=='invalid_input'

def test_callback_unknown_is_separate_from_real_query_terminal(boundary,tmp_path):
    boundary.mode='swallow'
    def callback(c):raise OSError('SECRET_EFFECT')
    result=host().submit('op',input(),execution=execution(tmp_path),on_event=ack,on_tool=callback)
    assert result.state=='completed' and result.terminal.state=='completed' and result.error.code=='tool_unknown'

def test_unknown_and_changed_operation_not_redispatched(boundary,tmp_path):
    boundary.mode='drop';h=host();ex=execution(tmp_path)
    one=h.submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    two=h.submit('op',input('other-attempt'),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert one==two and len(boundary.queries)==1
    with pytest.raises(RuntimeFault):h.submit('op',input(text='changed'),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))

def test_actual_interrupt_tracks_original_request(boundary,tmp_path):
    boundary.mode='wait';h=host();bound=threading.Event();reference=[];results=[]
    def event(e):
        if e.kind=='session':reference.append(e.result.request_id);bound.set()
        return ack(e)
    worker=threading.Thread(target=lambda:results.append(h.submit('op',input(),execution=execution(tmp_path),on_event=event,on_tool=lambda c:HostToolResult(1))))
    worker.start();assert bound.wait(3);assert h.cancel(reference[0]) in (None,True);worker.join(3)
    assert not worker.is_alive() and results[0].state=='cancelled' and results[0].terminal
    assert h.get_request(SubmissionQuery('op','attempt',None,reference[0]))==results[0] and not h.cancel('op')

def test_static_configuration_and_check_do_not_submit_model(boundary,tmp_path,monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path))
    h=host();assert not boundary.clients
    result=h.check()
    assert result['state']=='available' and result['configDirectory']=='readable'
    # D38 per-launch criterion: no CLI process, version command, handshake or model.
    assert not boundary.clients and not boundary.queries and not boundary.version_calls
    assert 'SECRET_CONFIG' not in json.dumps(result)
    assert ClaudeCodeHost.from_config({'command':[sys.executable],'model':'test-model'},base_dir=tmp_path).model=='test-model'

@pytest.mark.parametrize('bad',[[],False,'',0])
def test_bad_skill_sources_rejected_without_cli(boundary,bad):
    with pytest.raises(RuntimeFault):ClaudeCodeHost([sys.executable],model='test-model',skill_directories=bad)

def test_unsupported_resources_and_expired_deadline_are_honestly_not_submitted(boundary,tmp_path):
    h=host();ex=execution(tmp_path)
    resource=HostResource('r','x','text/plain',None,'scope','remote:unmapped')
    result=h.submit('op',replace(input(),resources=(resource,)),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.error.dispatch=='not_submitted' and result.error.phase=='resources'
    ex=replace(ex,deadline=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    result=h.submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.error.code=='expired' and not boundary.queries

def test_stderr_is_content_free_private_diagnostic(boundary,tmp_path):
    result=host().submit('op',input(),execution=execution(tmp_path),on_event=ack,on_tool=lambda c:HostToolResult(1))
    saved=json.loads((tmp_path/'runs'/(hashlib.sha256(b'op').hexdigest()+'.json')).read_text())
    assert saved['diagnostics']['stderrSignals']==['observed_numeric_502']
    assert 'SECRET_KEY' not in json.dumps(saved) and 'PRIVATE_USER_BODY' not in json.dumps(saved)
    assert saved['terminalEvidence']['requestId']==result.request_id

def test_deadline_does_not_stop_running_callback_or_invent_terminal(boundary,tmp_path):
    calls=[]
    def callback(c):time.sleep(1.25);calls.append(c);return HostToolResult({'receipt':1})
    ex=replace(execution(tmp_path),deadline=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
    started=time.monotonic()
    result=host().submit('op',input(),execution=ex,on_event=ack,on_tool=callback)
    assert result.state=='unknown' and result.terminal is None and len(calls)==1
    assert time.monotonic()-started>=1.25
    saved=json.loads((tmp_path/'runs'/(hashlib.sha256(b'op').hexdigest()+'.json')).read_text())
    events=saved['diagnostics']['events']
    assert next(e['elapsedMs'] for e in events if e['kind']=='timeout') < next(e['elapsedMs'] for e in events if e['kind']=='tool_return')

def test_restart_cannot_replay_original_unknown_operation(boundary,tmp_path):
    boundary.mode='drop';ex=execution(tmp_path)
    result=host().submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.state=='unknown'
    with pytest.raises(RuntimeFault):
        host().submit('op',input('new-attempt'),result.session_id,execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert len(boundary.queries)==1

def test_observation_uses_same_submit_and_retains_scope(boundary,tmp_path):
    calls=[];ex=replace(execution(tmp_path),kind='observation')
    result=host().submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:calls.append(c) or HostToolResult(1))
    assert result.state=='completed' and calls[0].operation_id=='op' and calls[0].attempt_id=='attempt'

def test_frozen_definition_change_and_missing_binding_do_not_resume(boundary,tmp_path):
    ex=execution(tmp_path)
    result=host().submit('one',input(),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    changed=replace(ex,definition=replace(ex.definition,instructions='changed'))
    with pytest.raises(RuntimeFault):host().submit('two',input('second'),result.session_id,execution=changed,on_event=ack,on_tool=lambda c:HostToolResult(1))
    (tmp_path/'claude-session.json').unlink()
    with pytest.raises(RuntimeFault):host().submit('two',input('second'),result.session_id,execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert len(boundary.queries)==1

def test_sdk_owned_attempt_rejects_cross_role_execution_before_dispatch(boundary,tmp_path):
    result=host().submit('op',input(),execution=replace(execution(tmp_path),profile='other-role'),on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.error.dispatch=='not_submitted' and result.error.code=='identity_conflict' and not boundary.queries

def test_running_query_and_callback_still_own_session_resources(boundary,tmp_path):
    h=host();entered=threading.Event();release=threading.Event();results=[];reference=[]
    def callback(c):reference.append(c.request_id);entered.set();assert release.wait(3);return HostToolResult(1)
    worker=threading.Thread(target=lambda:results.append(h.submit('one',input(),execution=execution(tmp_path),on_event=ack,on_tool=callback)))
    worker.start()
    try:
        assert entered.wait(3)
        assert h.check()['state']=='busy'
        pending=h.get_request(SubmissionQuery('one','attempt',None,reference[0]));assert pending.terminal is None and pending.state=='running'
        blocked=h.submit('two',input('second'),pending.session_id,execution=execution(tmp_path),on_event=ack,on_tool=lambda c:HostToolResult(1))
        assert blocked.error.code=='busy' and blocked.error.dispatch=='not_submitted' and len(boundary.queries)==1
    finally:release.set();worker.join(3)
    assert not worker.is_alive() and results[0].state=='completed'

def test_terminal_event_delivery_failure_does_not_erase_machine_fact(boundary,tmp_path):
    def event(e):
        if e.kind=='terminal':raise OSError('lost event acknowledgement')
        return ack(e)
    result=host().submit('op',input(),execution=execution(tmp_path),on_event=event,on_tool=lambda c:HostToolResult(1))
    assert result.state=='completed' and result.terminal and result.error.code=='event_reporting_unknown'

@pytest.mark.parametrize('interrupt_mode',['pending','error','ack'])
def test_cancel_ack_is_separate_from_local_request_and_machine_end(boundary,tmp_path,interrupt_mode):
    boundary.mode='wait';boundary.interrupt_mode=interrupt_mode;boundary.terminal_release=threading.Event()
    h=host();bound=threading.Event();reference=[];results=[]
    def event(e):
        if e.kind=='session':reference.append(e.result.request_id);bound.set()
        return ack(e)
    ex=replace(execution(tmp_path),deadline=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat())
    worker=threading.Thread(target=lambda:results.append(h.submit('op',input(),execution=ex,on_event=event,on_tool=lambda c:HostToolResult(1))))
    worker.start()
    try:
        assert bound.wait(3)
        assert h.cancel(reference[0]) in (None,True)
        assert boundary.interrupt_started.wait(1)
        if interrupt_mode=='pending':
            assert h.cancel(reference[0]) is None
            boundary.interrupt_release.set();assert boundary.interrupt_acked.wait(1)
        elif interrupt_mode=='error':
            assert h.cancel(reference[0]) is None
        else:assert boundary.interrupt_acked.wait(1)
        if interrupt_mode!='error':
            until=time.monotonic()+1
            while h.cancel(reference[0]) is None and time.monotonic()<until:time.sleep(.005)
            assert h.cancel(reference[0]) is True
        assert h.get_request(SubmissionQuery('op','attempt',None,reference[0])).terminal is None and h.check()['state']=='busy'
        assert boundary.interrupt_calls==1
    finally:
        boundary.interrupt_release.set();boundary.terminal_release.set();worker.join(3)
    assert not worker.is_alive()
    assert results[0].state==('unknown' if interrupt_mode=='error' else 'cancelled')

PROPOSAL={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}

def test_real_structured_output_uses_original_schema_and_is_separate_from_text(boundary,tmp_path):
    boundary.structured={'ok':True};boundary.text='{"ok":false}'
    ex=replace(execution(tmp_path),proposal_schema=PROPOSAL)
    result=host().submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert boundary.options[-1].output_format=={'type':'json_schema','schema':PROPOSAL}
    assert boundary.options[-1].output_format['schema'] is not PROPOSAL
    assert result.terminal and result.state=='completed' and result.error is None
    assert result.proposal=={'ok':True} and result.output=='{"ok":false}'

@pytest.mark.parametrize('structured,code',[(None,'proposal_missing'),({'ok':'yes'},'proposal_invalid'),
    ({'ok':True,'extra':1},'proposal_invalid'),([],'proposal_invalid')])
def test_missing_or_invalid_proposal_keeps_real_machine_terminal(boundary,tmp_path,structured,code):
    boundary.structured=structured
    result=host().submit('op',input(),execution=replace(execution(tmp_path),proposal_schema=PROPOSAL),
        on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.state=='completed' and result.terminal and result.proposal is None and result.error.code==code
    saved=json.loads((tmp_path/'runs'/(hashlib.sha256(b'op').hexdigest()+'.json')).read_text())
    assert saved['proposalError']==code and saved['structuredOutput']==structured

def test_unsolicited_structured_output_is_not_a_business_proposal(boundary,tmp_path):
    boundary.structured={'ok':True}
    result=host().submit('op',input(),execution=execution(tmp_path),on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.terminal and result.proposal is None and result.error.code=='proposal_unrequested'

@pytest.mark.parametrize('schema',[{'$ref':'https://invalid.example/schema'}, {'$ref':'#/missing'},
    {'type':'unknown'}, {'type':'string','format':'unknown-private-format'}, {'description':'x'*16385},False])
def test_invalid_unbounded_or_remote_proposal_schema_is_not_dispatched(boundary,tmp_path,schema):
    result=host().submit('op',input(),execution=replace(execution(tmp_path),proposal_schema=schema),
        on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.error.code=='invalid_proposal_schema' and result.error.dispatch=='not_submitted'
    assert not boundary.clients and not boundary.queries and not list(tmp_path.iterdir())

def test_changed_proposal_schema_cannot_replay_original_operation(boundary,tmp_path):
    boundary.structured={'ok':True};h=host();ex=replace(execution(tmp_path),proposal_schema=PROPOSAL)
    h.submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    with pytest.raises(RuntimeFault):
        h.submit('op',input(),execution=replace(ex,proposal_schema={'type':'object'}),on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert len(boundary.queries)==1

def test_proposal_and_event_failure_do_not_erase_terminal(boundary,tmp_path):
    def event(e):
        if e.kind=='terminal':raise OSError('event receipt lost')
        return ack(e)
    result=host().submit('op',input(),execution=replace(execution(tmp_path),proposal_schema=PROPOSAL),
        on_event=event,on_tool=lambda c:HostToolResult(1))
    assert result.terminal and result.state=='completed' and result.proposal is None
    saved=json.loads((tmp_path/'runs'/(hashlib.sha256(b'op').hexdigest()+'.json')).read_text())
    assert saved['proposalError']=='proposal_missing'

def test_tool_unknown_and_proposal_error_are_recorded_separately(boundary,tmp_path):
    boundary.mode='swallow'
    def tool(c):raise OSError('effect unknown')
    result=host().submit('op',input(),execution=replace(execution(tmp_path),proposal_schema=PROPOSAL),on_event=ack,on_tool=tool)
    assert result.terminal and result.error.code=='tool_unknown' and result.proposal is None
    saved=json.loads((tmp_path/'runs'/(hashlib.sha256(b'op').hexdigest()+'.json')).read_text())
    assert saved['proposalError']=='proposal_missing'

@pytest.mark.parametrize('level',['allow','ask','unknown'])
def test_native_requirements_leave_original_host_permissions(boundary,tmp_path,level):
    ex=replace(execution(tmp_path),authorization={'nativeTools':{'Bash':level}})
    result=host().submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:HostToolResult(1))
    assert result.state=='completed' and result.terminal and len(boundary.queries)==1

def test_non_sdk_tools_are_not_approved_or_denied_by_adapter(boundary,tmp_path):
    boundary.mode='foreign';calls=[]
    ex=replace(execution(tmp_path),authorization={'nativeTools':{'Bash':'deny'}})
    result=host().submit('op',input(),execution=ex,on_event=ack,on_tool=lambda c:calls.append(c) or HostToolResult(1))
    assert result.terminal and not calls and boundary.calls[-1]=={}


def test_check_reports_evidenced_missing_executable_or_config_without_starting_cli(boundary,tmp_path,monkeypatch):
    exe=tmp_path/'claude.exe';exe.write_bytes(b'not executed')
    h=ClaudeCodeHost([str(exe)],model='test-model')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path/'absent'))
    assert h.check()['state']=='available' and h.check()['configDirectory']=='absent'
    not_directory=tmp_path/'config-file';not_directory.write_text('x')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(not_directory))
    assert h.check()['state']=='unavailable' and h.check()['basis']
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path))
    exe.unlink()
    result=h.check()
    assert result['state']=='unavailable' and 'executable' in result['basis']
    assert not boundary.clients and not boundary.version_calls


def test_unfinished_launch_check_is_unknown_not_unavailable(boundary,tmp_path,monkeypatch):
    import ioalab_claude_code.launch_check as launch_check
    release=threading.Event()
    monkeypatch.setattr(launch_check,'CHECK_SECONDS',.05)
    monkeypatch.setattr('ioalab_claude_code.host.launch_facts',lambda **_:release.wait(5) or {'state':'available'})
    try:assert host().check()['state']=='unknown'
    finally:release.set()
