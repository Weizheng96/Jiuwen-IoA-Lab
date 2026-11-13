"""Host public contract over an explicit external stdio process boundary."""
import sys
from pathlib import Path
from dataclasses import replace
import json
import threading
import time

import pytest

from ioalab_codex import CodexHost
from ioalab_sdk.runtime import AgentDefinition,HostTurn,ToolBinding,RuntimeFault


def configured(mode='normal'):
    command=[sys.executable,'-u',str(Path(__file__).with_name('_codex_process.py')),'--mode',mode]
    return CodexHost(command,model='test-model')


def round_at(tmp_path,**kwargs):
    definition=AgentDefinition('assistant','Use only the bound SDK tool.',(
        ToolBinding('local.notice','Return a receipt',{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False},lambda *_:None),))
    return HostTurn('operation','actor','interaction',definition,'Call local.notice',str(tmp_path),**kwargs)


def test_unprobed_host_is_unavailable_and_configuration_is_explicit(tmp_path):
    host=CodexHost.from_config({'command':[sys.executable], 'model':'test-model'},base_dir=tmp_path)
    assert host.capabilities()['available'] is False


def test_bound_tool_round_and_new_host_resume_keep_the_original_session(tmp_path):
    host=configured();assert host.probe()['available'] is True
    sessions=[];calls=[]
    def invoke(name,args,call):
        assert sessions==['test-real-thread']
        assert json.loads((tmp_path/'codex-session.json').read_text())['sessionId']==sessions[0]
        calls.append((name,args,call));return {'receipt':args['text']}
    turn=round_at(tmp_path,on_session=sessions.append,on_tool=invoke)
    first=host.run(turn)
    assert first.status=='completed' and first.session_id=='test-real-thread'
    assert first.usage['total']['totalTokens']==17
    sessions.clear();host=configured();host.probe()
    second=host.run(replace(turn,session_id=first.session_id))
    assert second.status=='completed' and second.session_id==first.session_id
    assert calls==[('local.notice',{'text':'hello'},'original-call')]*2


def test_lost_result_keeps_session_and_never_retries(tmp_path):
    host=configured('exit');assert host.probe()['available']
    result=host.run(round_at(tmp_path))
    assert result.status=='unknown' and result.session_id=='test-real-thread'
    assert len(list((tmp_path/'runs').glob('*.json')))==1
    with pytest.raises(RuntimeFault,match='Resume the original'):
        host.run(round_at(tmp_path))


def test_changed_definition_and_missing_original_binding_are_rejected(tmp_path):
    host=configured();host.probe()
    turn=round_at(tmp_path,on_tool=lambda *_:{'receipt':'hello'})
    first=host.run(turn);assert first.status=='completed'
    changed=replace(turn.definition,instructions='A different tool policy')
    with pytest.raises(RuntimeFault,match='definition changed'):
        host.run(replace(turn,session_id=first.session_id,definition=changed))
    (tmp_path/'codex-session.json').unlink()
    with pytest.raises(RuntimeFault,match='Restore the original'):
        host.run(replace(turn,session_id=first.session_id))


def test_cancel_returns_actual_terminal_and_cannot_claim_no_effect(tmp_path):
    host=configured('wait');host.probe()
    started=threading.Event();results=[]
    turn=round_at(tmp_path,on_event=lambda e:started.set() if e['method']=='turn/started' else None)
    worker=threading.Thread(target=lambda:results.append(host.run(turn)))
    worker.start()
    try:
        assert started.wait(10)
        assert host.cancel(turn.operation_id) is True
    finally:
        worker.join(10)
    assert not worker.is_alive()
    assert results[0].status=='cancelled' and results[0].session_id=='test-real-thread'
    assert host.cancel(turn.operation_id) is False


def test_native_tools_and_unprobed_rounds_fail_before_start(tmp_path):
    host=configured();turn=round_at(tmp_path)
    with pytest.raises(RuntimeFault) as error:host.run(turn)
    assert error.value.code=='not_ready'
    host.probe()
    with pytest.raises(RuntimeFault) as error:
        host.run(replace(turn,definition=replace(turn.definition,native_tools=(('Bash','allow'),))))
    assert error.value.code=='not_ready' and not (tmp_path/'runs').exists()


def test_selected_skills_are_frozen_and_cannot_be_replaced_on_resume(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('# Original skill',encoding='utf-8')
    host=CodexHost(configured().command,model='test-model',skill_directories={'selected':str(source)})
    host.probe()
    turn=round_at(tmp_path/'work',on_tool=lambda *_:{'receipt':'hello'})
    turn=replace(turn,definition=replace(turn.definition,skills=('selected',)))
    first=host.run(turn)
    assert first.status=='completed'
    (source/'SKILL.md').write_text('# Changed source',encoding='utf-8')
    second=host.run(replace(turn,session_id=first.session_id))
    assert second.status=='completed'
    assert (tmp_path/'work/skill-documents/selected/SKILL.md').read_text()=='# Original skill'


def test_unbound_namespace_cannot_reach_the_role_callback(tmp_path):
    host=configured('bad-tool');host.probe();calls=[]
    result=host.run(round_at(tmp_path,on_tool=lambda *args:calls.append(args)))
    assert result.status=='unknown' and calls==[]
    assert result.error['message']=='Unbound SDK tool call'


def test_unresponsive_child_cannot_block_the_turn_deadline_while_writing(tmp_path):
    host=configured('stall-input');host.probe()
    turn=replace(round_at(tmp_path),input='x'*(1024*1024),timeout_seconds=.5)
    start=time.monotonic();result=host.run(turn)
    assert result.status=='unknown' and result.session_id=='test-real-thread'
    assert time.monotonic()-start<2
