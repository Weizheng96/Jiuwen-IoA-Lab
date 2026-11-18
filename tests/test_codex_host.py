from ioalab_sdk.runtime import SubmissionQuery
"""S01 Codex tests at the real adapter / explicit public-wire process boundary.

Checks SDK protocol, source binding, schema and unknown recovery; no claim of
native desktop visibility or live Codex model acceptance.
"""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json,sys,threading,time
from pathlib import Path
import pytest
from ioalab_codex import CodexHost
from ioalab_sdk.runtime import RuntimeFault
from ioalab_sdk.runtime.host_submission import (HostInput,HostExecution,HostDefinition,HostResource,HostToolDeclaration,
 SubmissionAttempt,BindingAck,HostToolResult)

def configured(mode='normal',**kw):
    command=[sys.executable,'-u',str(Path(__file__).with_name('_codex_submission_process.py')),'--mode',mode]
    return CodexHost(command,model='test-model',**kw)

def execution(tmp_path,**kw):
    tool=HostToolDeclaration('local.notice','Return original receipt',{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False})
    return HostExecution('actor','profile','business','interaction','job',HostDefinition('assistant','1','fixed-digest','Use only the bound SDK tool.'),tools=(tool,),workspace=str(tmp_path),**kw)

def submit(host,tmp_path,*,op='operation',session=None,text='Call local.notice',events=None,tool=None,definition=None):
    def event(value):
        if events is not None:events.append(value)
        if value.kind=='session':return BindingAck(value.result.session_id,value.result.request_id)
    return host.submit(op,HostInput(text,attempt=SubmissionAttempt('attempt-'+op,'scope','actor','profile')),session,
        execution=definition or execution(tmp_path),on_event=event,on_tool=tool or(lambda call:HostToolResult(value={'receipt':call.arguments['text']})))

def test_static_registration_and_check_are_not_submit_gate(tmp_path):
    host=configured()
    assert host.registration.adapter_id=='codex'
    assert set(host.registration.optional_methods)=={'get_request','cancel'}
    assert not any(hasattr(host,n)for n in ('run','probe','capabilities','prepare_delivery','collect_context','resolve_context'))
    assert submit(host,tmp_path).state=='completed'

def test_bound_tool_round_has_actual_turn_and_prior_p28_ack(tmp_path):
    host=configured();events=[];calls=[]
    def invoke(call):
        assert any(e.kind=='session'and e.result.session_id==call.session_id and e.result.request_id==call.request_id for e in events)
        assert call.request_id=='test-turn'and call.call_id=='original-call'
        calls.append(call);return HostToolResult(value={'receipt':call.arguments['text']})
    first=submit(host,tmp_path,events=events,tool=invoke)
    assert first.state=='completed'and first.request_id=='test-turn'and first.terminal.request_id=='test-turn'
    assert first.usage['total']['totalTokens']==17 and len(calls)==1
    second=submit(configured(),tmp_path,op='next',session=first.session_id)
    assert second.session_id==first.session_id and second.state=='completed'

@pytest.mark.parametrize('fails',[False,True])
def test_bad_arguments_correct_in_same_turn_callback_failure_stays_unknown(tmp_path,fails):
    calls=[]
    def invoke(call):
        calls.append(call)
        if fails:raise RuntimeError('Original effect unknown')
        return HostToolResult(value={'receipt':'hello'})
    result=submit(configured('bad-args'),tmp_path,tool=invoke)
    assert [c.call_id for c in calls]==['corrected-call']
    assert result.state==('unknown'if fails else 'completed')

def test_lost_result_keeps_machine_id_and_same_operation_does_not_replay(tmp_path):
    host=configured('exit');first=submit(host,tmp_path)
    assert first.state=='unknown'and first.session_id=='test-real-thread'and first.request_id=='test-turn'
    assert submit(configured('exit'),tmp_path)==first

def test_original_scope_cannot_change_or_bind_another_session(tmp_path):
    first=submit(configured(),tmp_path)
    with pytest.raises(RuntimeFault):submit(configured(),tmp_path,text='changed')
    with pytest.raises(RuntimeFault):submit(configured(),tmp_path,session='different-thread')
    assert first.session_id=='test-real-thread'

def test_cancel_ack_does_not_replace_actual_terminal(tmp_path):
    host=configured('wait');started=threading.Event();result=[]
    def event(e):
        if e.kind=='running':started.set()
        if e.kind=='session':return BindingAck(e.result.session_id,e.result.request_id)
    worker=threading.Thread(target=lambda:result.append(host.submit('op',HostInput('wait',attempt=SubmissionAttempt('attempt','scope','actor','profile')),execution=execution(tmp_path),on_event=event)))
    worker.start()
    try:
        assert started.wait(10)
        assert host.cancel('test-turn')['state']=='accepted'
    finally:worker.join(10)
    assert not worker.is_alive()and result[0].state=='cancelled'and result[0].terminal.state=='cancelled'
    assert host.cancel('missing')['state']=='unknown'

def test_sdk_native_requirements_do_not_override_host_permissions(tmp_path):
    result=submit(configured(),tmp_path,definition=execution(tmp_path,authorization={'nativeTools':{'Bash':'allow'}}))
    assert result.state=='completed' and result.terminal


def test_native_approval_is_not_answered_and_original_request_is_retained(tmp_path):
    calls=[]
    result=submit(configured('native-approval'),tmp_path,tool=lambda call:calls.append(call))
    assert result.state=='unknown' and result.session_id=='test-real-thread' and result.request_id=='test-turn'
    assert result.error.phase=='native_approval' and result.error.action=='continue_original_native_entry'
    assert not calls and not (tmp_path/'unexpected-native-approval-response').exists()
    assert submit(configured('native-approval'),tmp_path)==result

def test_unbound_namespace_never_enters_sdk_callback(tmp_path):
    calls=[];result=submit(configured('bad-tool'),tmp_path,tool=lambda c:calls.append(c))
    assert result.state=='unknown'and calls==[]

def test_missing_p28_ack_cannot_activate_sdk_tools(tmp_path):
    calls=[];host=configured()
    result=host.submit('op',HostInput('call',attempt=SubmissionAttempt('attempt','scope','actor','profile')),execution=execution(tmp_path),on_event=lambda e:None,on_tool=lambda c:calls.append(c))
    assert result.state=='unknown'and calls==[]

def test_resources_preserve_original_reference_and_fail_before_dispatch_until_p26_glue(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('# Original skill')
    host=configured(skill_directories={'selected':str(source)})
    frozen=HostResource('frozen-skill','SKILL.md','text/markdown','original-digest','original-scope','sdk-definition:frozen-skill')
    selected=replace(execution(tmp_path/'work'),definition=replace(execution(tmp_path/'work').definition,resources=(frozen,)))
    first=submit(host,tmp_path/'work',definition=selected)
    (source/'SKILL.md').write_text('# Changed source')
    second=submit(host,tmp_path/'work',definition=selected)
    assert first.error.phase==second.error.phase=='resource_mapping'
    assert first.error.dispatch==second.error.dispatch=='not_submitted'
    assert selected.definition.resources==(frozen,)and not(tmp_path/'work/codex-submissions').exists()

def test_bounded_turn_deadline_keeps_unknown_and_original_session(tmp_path):
    deadline=(datetime.now(timezone.utc)+timedelta(seconds=.5)).isoformat()
    start=time.monotonic();result=submit(configured('stall-input'),tmp_path,text='x'*(1024*1024),definition=execution(tmp_path,deadline=deadline))
    assert result.state=='unknown'and result.session_id=='test-real-thread'
    assert time.monotonic()-start<3

def test_runtime_check_is_bounded_read_only_inventory():
    assert configured().check()['state']=='available'


@pytest.mark.parametrize('mode',['early-good','early-wrong','early-mixed','wrong-start'])
def test_only_rpc_owned_turn_can_activate_early_tool_packets(tmp_path,mode):
    events=[];calls=[]
    def invoke(call):
        assert any(e.kind=='session'and(e.result.session_id,e.result.request_id)==(call.session_id,call.request_id)for e in events)
        calls.append(call);return HostToolResult(value={'receipt':'hello'})
    result=submit(configured(mode),tmp_path,events=events,tool=invoke)
    assert [c.request_id for c in calls]==(['test-turn']if mode=='early-good'else [])
    assert result.state==('completed'if mode=='early-good'else 'unknown')
    assert result.request_id=='test-turn'

def test_request_binding_ack_must_include_exact_actual_request(tmp_path):
    calls=[];host=configured('early-good')
    result=host.submit('op',HostInput('call',attempt=SubmissionAttempt('attempt','scope','actor','profile')),execution=execution(tmp_path),
        on_event=lambda e:BindingAck(e.result.session_id,None)if e.kind=='session'else None,
        on_tool=lambda c:calls.append(c))
    assert result.state=='unknown'and result.request_id=='test-turn'and calls==[]

def test_terminal_event_callback_failure_preserves_machine_fact_and_replay(tmp_path):
    host=configured()
    def event(value):
        if value.kind=='terminal':raise RuntimeError('SDK event receipt failed')
        if value.kind=='session':return BindingAck(value.result.session_id,value.result.request_id)
    result=host.submit('op',HostInput('call',attempt=SubmissionAttempt('attempt','scope','actor','profile')),execution=execution(tmp_path),
        on_event=event,on_tool=lambda c:HostToolResult(value={'receipt':'hello'}))
    assert result.state=='completed'and result.terminal.state=='completed'
    assert result.error.phase=='event_callback'and host.get_request(SubmissionQuery(result.operation_id,result.attempt_id,result.session_id,'test-turn'))==result
    replay=host.submit('op',HostInput('call',attempt=SubmissionAttempt('attempt','scope','actor','profile')),execution=execution(tmp_path),on_event=event)
    assert replay==result

@pytest.mark.parametrize('field,value',[('actor','other-actor'),('profile','other-profile')])
def test_execution_cannot_override_sdk_attempt_identity(tmp_path,field,value):
    with pytest.raises(RuntimeFault):submit(configured(),tmp_path,definition=replace(execution(tmp_path),**{field:value}))
    assert not(tmp_path/'codex-submissions').exists()

def test_relative_workspace_is_not_made_absolute_by_resolve(tmp_path):
    with pytest.raises(RuntimeFault):submit(configured(),tmp_path,definition=replace(execution(tmp_path),workspace='relative-untrusted'))
    assert not(tmp_path/'codex-submissions').exists()

def test_proposal_schema_is_preserved_and_rejected_before_dispatch(tmp_path):
    schema={'type':'object','properties':{'actual':{'type':'string'}},'required':['actual'],'additionalProperties':False}
    original=execution(tmp_path,proposal_schema=schema)
    result=submit(configured(),tmp_path,definition=original)
    assert original.proposal_schema==schema
    assert result.error.phase=='proposal_schema'and result.error.dispatch=='not_submitted'
    assert not(tmp_path/'codex-submissions').exists()

@pytest.mark.parametrize('has_deadline',[False,True])
def test_model_wait_uses_original_deadline_without_extra_five_minute_cap(tmp_path,monkeypatch,has_deadline):
    # Fake only the external clock at the wire boundary: a completed tool step
    # consumes 700s while native terminal bytes remain the same real wire data.
    # This catches an extra 300s cap without waiting 12 minutes in the suite.
    from types import SimpleNamespace
    import ioalab_codex.transport as transport
    monotonic=time.monotonic;elapsed=[0]
    monkeypatch.setattr(transport,'time',SimpleNamespace(monotonic=lambda:monotonic()+elapsed[0]))
    definition=execution(tmp_path,deadline=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()if has_deadline else None)
    def invoke(call):
        elapsed[0]=700
        return HostToolResult(value={'receipt':'hello'})
    result=submit(configured(),tmp_path,definition=definition,tool=invoke)
    assert result.state=='completed'and result.terminal.state=='completed'
    assert configured().registration.readonly_context=='isolated_session'

@pytest.mark.parametrize('kind',['business','observation'])
def test_native_phase_uses_original_host_settings(tmp_path,kind):
    result=submit(configured(),tmp_path,definition=execution(tmp_path,kind=kind,
        authorization={'nativeToolsEnabled':True,'nativeTools':{}}))
    assert result.state=='completed' and result.terminal

@pytest.mark.parametrize('flag',[1,0,None,'false',{},[]])
def test_mutated_non_boolean_native_phase_is_rejected_before_dispatch(tmp_path,flag):
    original=execution(tmp_path,authorization={'nativeToolsEnabled':False})
    original.authorization['nativeToolsEnabled']=flag
    result=submit(configured(),tmp_path,definition=original)
    assert result.error.code=='invalid_input'and result.error.dispatch=='not_submitted'
    assert not(tmp_path/'codex-submissions').exists()

def test_false_native_phase_does_not_restrict_this_adapter(tmp_path):
    result=submit(configured(),tmp_path,definition=execution(tmp_path,authorization={'nativeToolsEnabled':False,'nativeTools':{}}))
    assert result.state=='completed'and result.terminal.state=='completed'
