"""Test-only S01 external boundary; business assertions stay in their original tests."""
from dataclasses import dataclass,replace
from types import SimpleNamespace
import uuid

from ioalab_sdk.runtime import (BindingAck,HostRegistration,HostToolCall,MachineTerminal,
    RuntimeFault,SubmissionEvent,SubmissionResult,SubmissionError)


@dataclass(frozen=True)
class FixtureResult:
    status: str
    session_id: str|None
    usage: dict
    error: dict|None=None
    request_id: str|None=None


class ScriptedSubmission:
    registration=HostRegistration('scripted-s01-boundary','1',('cancel',),readonly_context='isolated_session')

    def check(self):return {'state':'unknown','reason':'Explicit test boundary; no model inventory claim'}

    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        if execution is None:raise AssertionError('Business scripts require the original execution snapshot')
        request='boundary-request-'+str(uuid.uuid4())
        known=[session_id]
        receipt=[SubmissionResult(operation_id,input.attempt.nonce,'running',session_id,delivery='accepted')]
        def bind(sid):
            if not isinstance(sid,str) or not sid or known[0] and sid!=known[0]:
                raise RuntimeFault('identity_conflict','The scripted native session changed')
            known[0]=sid
            receipt[0]=replace(receipt[0],session_id=sid,request_id=request)
            if on_event(SubmissionEvent('native-session','session',receipt[0]))!=BindingAck(sid,request):
                raise RuntimeFault('not_ready','Persist the original binding before the scripted SDK callback')
        def call(name,args,call_id):
            if not known[0] or not receipt[0].request_id:raise RuntimeFault('not_ready','The scripted native boundary has no acknowledged session')
            result=on_tool(HostToolCall(operation_id,input.attempt.nonce,known[0],request,call_id,name,args))
            if result.error:raise RuntimeFault(result.error.code,result.error.message)
            return result.value
        definition=SimpleNamespace(name=execution.definition.reference,instructions=execution.definition.instructions,
            tools=execution.tools,skills=execution.definition.resources,
            native_tools=tuple((execution.authorization.get('nativeTools') or {}).items()))
        frame=SimpleNamespace(operation_id=operation_id,job_id=execution.job_id,actor=execution.actor,interaction_id=execution.interaction_id,business_ref=execution.business_ref,
            definition=definition,input=input.text,workspace=execution.workspace,session_id=session_id,
            on_session=bind,on_tool=call,on_interaction=getattr(self,'question_handler',None),
            allow_native_tools=execution.authorization.get('nativeToolsEnabled',False),deadline=execution.deadline)
        try:value=self.execute(frame)
        except Exception as exc:
            self.failure={'type':type(exc).__name__,'message':str(exc)}
            raise
        if not isinstance(value,FixtureResult):raise AssertionError('Return a test-private scripted result')
        if value.session_id and known[0] and value.session_id!=known[0]:
            raise RuntimeFault('identity_conflict','The scripted final session differs from P28')
        terminal=None
        if value.status in ('completed','failed','cancelled') and known[0] and receipt[0].request_id:
            terminal=MachineTerminal(operation_id,input.attempt.nonce,known[0],request,value.status,'scripted-machine-end:'+request)
        error=SubmissionError(value.error.get('code','unknown_result'),'native','accepted','Scripted native failure') if value.error else None
        return replace(receipt[0],state=value.status,error=error,terminal=terminal,usage=value.usage)

    def cancel(self,request_id):return True


class RecordingSubmission:
    """Opt-in real adapter seen only through S01; records the original submitted references."""
    def __init__(self,host):self.host=host;self.turns=[];self.registration=host.registration
    def check(self):return self.host.check()
    def get_request(self,request_id):return self.host.get_request(request_id)
    def cancel(self,request_id):return self.host.cancel(request_id)
    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        self.turns.append(SimpleNamespace(operation_id=operation_id,session_id=session_id,input=input.text,
            interaction_id=execution.interaction_id if execution else None,job_id=execution.job_id if execution else None,
            definition=SimpleNamespace(name=execution.definition.reference) if execution else None))
        return self.host.submit(operation_id,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)


def bound_runtime(config,host,role_factory,**options):
    """A role fixture follows the same sole Registry/P20 route as SDK applications."""
    from ioalab_sdk.runtime import HostRegistry,ResidentRuntime
    registry=HostRegistry({'test-application':host},'test-application') if host is not None else None
    role=role_factory(registry)
    if registry is not None:
        def tools(profile):
            return dict(role.tools if hasattr(role,'tools') else role.tools_by_profile.get(profile,{}))
        registry.tools['test-application']=SimpleNamespace(for_profile=tools)
    runtime=ResidentRuntime(config,modules=(role,),host_registry=registry,
        host_status=registry.status_snapshot if registry else None,**options)
    if host is not None and getattr(host,'question',False):
        def answer(record):
            from ioalab_sdk.runtime import native_answer
            from ioalab_sdk.runtime.scheduling import CURRENT_JOB
            current=CURRENT_JOB.get()
            if not current or current[0] is not runtime.scheduler:raise AssertionError('A question needs its original SDK job')
            job=runtime.scheduler.get(current[1])
            return native_answer(runtime,job['agentId'],current[1],record,runtime.stop_requested)
        host.question_handler=answer
    return runtime
