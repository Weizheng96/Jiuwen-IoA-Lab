"""Opt-in model driver: explicit SDK Registry/Scheduler/S01 only, with a scripted S01 boundary (no model)."""
from dataclasses import replace
import threading

import pytest

from conftest import wait_for
from ioalab_sdk.runtime import (AgentDefinition,BindingAck,HostRegistration,HostToolCall,MachineTerminal,RuntimeFault,
    SubmissionEvent,SubmissionResult,ToolBinding)
from tools.verification.s01_driver import ExplicitSubmission


class Boundary:
    registration=HostRegistration('driver-boundary','1',('get_request','cancel'))
    def __init__(self):self.calls=[];self.cancelled=[];self.gate=None;self.receipts={}
    def check(self):raise AssertionError('The driver never gates on S02')
    def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        sid=session_id or 'native-session';request='request-'+op
        accepted=SubmissionResult(op,input.attempt.nonce,'accepted',sid,request,'accepted')
        assert on_event(SubmissionEvent('session','session',accepted))==BindingAck(sid,request)
        self.calls.append((op,session_id,execution.job_id,input.text))
        if self.gate:
            assert self.gate.wait(5);state='cancelled'
        else:
            value=on_tool(HostToolCall(op,input.attempt.nonce,sid,request,'call-'+str(len(self.calls)),'local.receipt',{'value':input.text}))
            assert value.value=={'receipt':input.text} and value.error is None
            state='completed'
        self.receipts[request]=replace(accepted,state=state,terminal=MachineTerminal(op,input.attempt.nonce,sid,request,state,'machine:'+request))
        return self.receipts[request]
    def get_request(self,request):return self.receipts[request]
    def cancel(self,request):self.cancelled.append(request);self.gate.set();return True


DEFINITION=AgentDefinition('driver-check','Use local.receipt once.',(ToolBinding('local.receipt','Return a local receipt',
    {'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False},lambda *_:None),))


def test_tool_runs_after_p28_binding_and_later_round_keeps_original_session(tmp_path):
    host=Boundary();sessions=[];tools=[]
    def tool(name,arguments,call):
        assert sessions,'P28 must bind the actual session before the SDK tool'
        tools.append(name);return {'receipt':arguments['value']}
    driver=ExplicitSubmission(host,tmp_path/'driver')
    try:
        first=driver.submit('interaction',DEFINITION,'first',workspace=tmp_path/'work',timeout=10,
            on_tool=tool,on_event=lambda event:sessions.append(event.result.session_id))
        assert first.state=='completed' and first.terminal and first.session_id=='native-session'
        second=driver.submit('interaction',DEFINITION,'second',workspace=tmp_path/'work',timeout=10,on_tool=tool)
        assert second.state=='completed' and second.session_id=='native-session'
        assert [call[1] for call in host.calls]==[None,'native-session'] and tools==['local.receipt']*2
        assert len({call[2] for call in host.calls})==2
    finally:driver.close()


def test_cancel_uses_original_request_and_unreturned_submission_stays_unknown(tmp_path):
    host=Boundary();host.gate=threading.Event()
    driver=ExplicitSubmission(host,tmp_path/'driver')
    try:
        operation=driver.start('interaction',DEFINITION,'wait',workspace=tmp_path/'work',on_tool=lambda *_:pytest.fail('No tool'))
        wait_for(lambda:host.calls)
        with pytest.raises(RuntimeFault) as caught:driver.wait(operation,.1)
        assert caught.value.code=='unknown_result' and len(host.calls)==1
        assert driver.cancel(operation)=={'supported':True,'accepted':True,'ended':False}
        result=driver.wait(operation,10)
        assert result.state=='cancelled' and result.terminal and host.cancelled==[result.request_id]
        assert driver.query(operation).terminal==result.terminal and len(host.calls)==1
    finally:host.gate.set();driver.close()
