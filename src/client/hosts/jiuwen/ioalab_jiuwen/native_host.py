"""Native product entry. Never select the SDK Process driver as a fallback."""
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from ioalab_sdk.runtime import (HostExecution,HostRegistration,HostResource,HostInput,RuntimeFault,
                               SubmissionResult,SubmissionError)
from .native_skills import NATIVE_FILES
from .native_transport import NativeTransport


def _registration():
    try:version=metadata.version('ioalab-jiuwen')
    except metadata.PackageNotFoundError:version='uninstalled'
    return HostRegistration('jiuwen',version,('get_request',),resources=tuple(HostResource(
        'jiuwen-native:'+name,name,'application/octet-stream',None,'installation',
        'package:ioalab_jiuwen/resources/native/'+name) for name in NATIVE_FILES))


class NativeJiuwenHost:
    registration=_registration()

    def __init__(self,entry,*,resource_factory=None):
        self.entry=entry;self.resource_factory=resource_factory

    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        if not isinstance(input,HostInput) or input.attempt is None:
            raise RuntimeFault('invalid_input','Use the original SDK native submission attempt')
        if not isinstance(operation_id,str) or not operation_id or not input.text.strip():
            return SubmissionResult(operation_id,input.attempt.nonce,'unknown',session_id,
                error=SubmissionError('invalid_input','native_input','not_submitted','Use the original nonempty native message'))
        if session_id is not None and (not isinstance(session_id,str) or not session_id):
            raise RuntimeFault('invalid_input','Use the actual original native Session or None')
        supported=(isinstance(execution,HostExecution) and execution.kind=='business' and
            (execution.actor,execution.profile)==(input.attempt.actor,input.attempt.profile) and
            len(execution.tools)==1 and not execution.definition.resources and execution.proposal_schema is None and
            callable(on_tool))
        if input.resources or execution is not None and not supported:
            # send_session_message inherits the native Agent; the per-request
            # guard installs only one frozen definition and SDK tool. Never
            # dispatch and then discover an unsupported execution contract.
            return SubmissionResult(operation_id,input.attempt.nonce,'unknown',session_id,
                error=SubmissionError('not_ready','native_definition','not_submitted',
                    'Native slice requires one SDK tool, original instructions and no resources/proposal'))
        if execution is not None and execution.deadline is not None:
            # The original deadline is enforced at native hook boundaries; no
            # fixed cap and no silent ignoring.
            try:
                deadline=datetime.fromisoformat(execution.deadline.replace('Z','+00:00'))
                if deadline.tzinfo is None:raise ValueError()
            except (TypeError,ValueError,AttributeError):
                return SubmissionResult(operation_id,input.attempt.nonce,'unknown',session_id,
                    error=SubmissionError('invalid_input','deadline','not_submitted','Use the timezone-aware original deadline'))
            if deadline<=datetime.now(timezone.utc):
                return SubmissionResult(operation_id,input.attempt.nonce,'unknown',session_id,
                    error=SubmissionError('expired','deadline','not_submitted','Original deadline expired; no native submission'))
        if not callable(on_event):
            return SubmissionResult(operation_id,input.attempt.nonce,'unknown',session_id,
                error=SubmissionError('not_ready','binding','not_submitted','Persist the exact native Session/request through P28'))
        return self.entry.submit(operation_id,input,session_id,on_event,execution,on_tool)

    def check(self):
        return self.entry.check()

    def get_request(self,request_id):return self.entry.get_request(request_id)


def native_adapter(*,host_ref,resource_root,plugin_directory=None,resource_factory=None):
    root=Path(resource_root)
    if not isinstance(host_ref,str) or not host_ref or not root.is_absolute():
        raise RuntimeFault('invalid_input','Use the SDK-owned host reference and absolute resource root')
    entry=NativeTransport(host_ref,root)
    return {'host':NativeJiuwenHost(entry,resource_factory=resource_factory),
            'native_handlers':{host_ref:entry.exchange},'native_resources':{host_ref:entry}}
