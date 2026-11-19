"""S01/S02 boundary for the explicit SDK Process application."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from importlib import metadata
import os
from pathlib import Path
import shutil
import threading
import uuid

from ioalab_sdk.runtime import (AgentDefinition, BindingAck, HostExecution, HostInput,
    HostRegistration, HostToolCall, HostToolResult, MachineTerminal, RuntimeFault,
    SubmissionError, SubmissionEvent, SubmissionResult, ToolBinding, encode, private_json, output_schema_check, validate_output)
from .process_values import _Round
from .process_files import ProcessFiles

# S02 self-check bound: filesystem metadata only. A hung network drive is
# unknown, never a reason to start the original Process.
CHECK_SECONDS=5


def _registration():
    try:version=metadata.version('ioalab-jiuwen')
    except metadata.PackageNotFoundError:version='uninstalled'
    return HostRegistration('jiuwen-process-sdk',version,('get_request','cancel'),readonly_context='isolated_session')


class Submission:
    registration=_registration()

    def check(self):
        """P34/S02 for a per-call Process: can it be started? No process, handshake or model."""
        with self._lock:
            if self._running:return {'state':'busy','basis':'Original Process request still active'}
        found=[]
        def inspect():
            try:found.append(self._startable())
            except Exception as exc:found.append({'state':'unknown','basis':'Start prerequisites unreadable','errorType':type(exc).__name__})
        probe=threading.Thread(target=inspect,name='ioalab-jiuwen-check',daemon=True)
        probe.start();probe.join(CHECK_SECONDS)
        if not found:return {'state':'unknown','basis':'Start prerequisite self-check did not finish in time'}
        return found[0]

    def _startable(self):
        # Evidence-based missing/unreadable is unavailable; other OS errors stay unknown.
        executable=self.command[0]
        if Path(executable).is_absolute():
            located=Path(executable) if Path(executable).is_file() and os.access(executable,os.X_OK) else None
        else:
            located=shutil.which(executable,path=self.environment.get('PATH'))
        if located is None:return {'state':'unavailable','basis':'Configured Jiuwen Process command is not an executable file'}
        if not self.data_dir.is_dir():return {'state':'unavailable','basis':'Configured Jiuwen data directory is missing'}
        config=self.data_dir/'config'/'config.yaml'
        if not config.is_file():return {'state':'unavailable','basis':'Jiuwen model configuration file is missing'}
        try:
            # Opening proves readability; the content (credentials) is never read.
            with open(config,'rb'):pass
            with os.scandir(self.data_dir) as entries:next(entries,None)
        except PermissionError:return {'state':'unavailable','basis':'Jiuwen data directory or model configuration is not readable'}
        return {'state':'available','basis':'Process command, data directory and model configuration present; model execution unverified'}

    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        if not isinstance(operation_id,str) or not operation_id or not isinstance(input,HostInput) or input.attempt is None or not isinstance(execution,HostExecution):
            raise RuntimeFault('invalid_input','Use original operation, full SDK attempt and frozen execution')
        attempt=input.attempt.nonce
        initial=SubmissionResult(operation_id,attempt,'unknown',session_id)
        def reject(code,phase,message):return replace(initial,error=SubmissionError(code,phase,'not_submitted',message))
        if session_id is not None and (not isinstance(session_id,str) or not session_id):
            return reject('invalid_input','session','Use the real Session or None')
        if (input.attempt.actor,input.attempt.profile)!=(execution.actor,execution.profile):
            return reject('identity_conflict','scope','Keep original SDK actor/profile')
        if not execution.workspace or not Path(execution.workspace).is_absolute():
            return reject('invalid_input','workspace','Original absolute SDK workspace required')
        submission_resources=(*input.resources,*execution.definition.resources)
        resource_factory=self.resource_factory
        if submission_resources and not callable(resource_factory):
            return reject('not_ready','resources','Bind the original SDK per-submission resource factory')
        if execution.proposal_schema is not None:
            try:output_schema_check(execution.proposal_schema)
            except RuntimeFault:return reject('invalid_proposal_schema','proposal','Use the original bounded self-contained proposal schema')
        if execution.tools and (not callable(on_tool) or not callable(on_event)):
            return reject('not_ready','binding','SDK tool effects require binding acknowledgement and original callback')
        timeout=None
        if execution.deadline is not None:
            try:
                deadline=datetime.fromisoformat(execution.deadline.replace('Z','+00:00'))
                if deadline.tzinfo is None:raise ValueError()
                timeout=(deadline-datetime.now(timezone.utc)).total_seconds()
            except (TypeError,ValueError,AttributeError):return reject('invalid_input','deadline','Use timezone-aware original deadline')
            if timeout<=0:return reject('expired','deadline','Original deadline expired; no native submission')
        workspace=Path(execution.workspace)
        fingerprint=hashlib.sha256(encode({'input':input.text,'resources':[asdict(r) for r in input.resources],
            'session':session_id,'execution':asdict(execution)}).encode()).hexdigest()
        path=workspace/'runs'/('submission-'+hashlib.sha256(operation_id.encode()).hexdigest()+'.json')
        with self._lock:
            old=self._submissions.get(operation_id)
            previous=[]
            if old:
                if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Preserve original SDK submission')
                result=old['result']
                if (result.attempt_id==attempt or not old['finished'] or not result.error
                        or result.error.dispatch!='not_submitted' or result.request_id or result.terminal or old['bound']):
                    return result
            if path.exists():
                saved=json.loads(path.read_text(encoding='utf-8'));receipt=saved['result']
                if saved['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Preserve original SDK submission')
                if ((receipt.get('error') or {}).get('dispatch')!='not_submitted'
                        or receipt.get('request_id') or receipt.get('terminal') or receipt.get('resources')
                        or receipt.get('output') is not None or receipt.get('proposal') is not None):
                    raise RuntimeFault('unknown_result','Recover original Process receipt; never resubmit unknown')
                if receipt['attempt_id']==attempt:
                    return SubmissionResult(**{**receipt,'error':SubmissionError(**receipt['error'])})
                previous=[*saved.get('previousAttempts',[]),
                          {'plannedRequestId':saved['plannedRequestId'],'result':receipt}]
            if any(not s['finished'] and (s['workspace']==str(workspace) or session_id and s['result'].session_id==session_id) for s in self._submissions.values()):
                return reject('busy','session','Original request still owns this SDK workspace/Session')
            request='ioa-'+uuid.uuid4().hex
            state={'fingerprint':fingerprint,'result':initial,'plannedRequestId':request,'finished':False,
                   'workspace':str(workspace),'path':path,'bound':False,'operation':operation_id,'previousAttempts':previous}
            self._submissions[operation_id]=state
            self._persist(state)
        files=None
        resource_lock=threading.Lock()
        def materialize(resources):
            nonlocal files
            # Serialize mapping within this attempt so callbacks cannot race to publish the same file.
            with resource_lock:
                if files is None:
                    if not callable(resource_factory):
                        raise RuntimeFault('not_ready','Bind the original SDK per-submission resource factory')
                    reader,sink=resource_factory(operation_id,input.attempt)
                    files=ProcessFiles(workspace,operation_id,input.attempt,reader,sink)
                return files.materialize(resources)
        def bind(sid,actual_request):
            # This callback is only invoked after the Process protocol echoed
            # the exact planned request and a validated real Session together.
            if actual_request!=state['plannedRequestId'] or session_id and sid!=session_id:
                raise RuntimeFault('identity_conflict','Original Process request/Session echo required')
            value=replace(state['result'],state='running',session_id=sid,request_id=actual_request)
            with self._lock:
                state['result']=value
                self._request_states[actual_request]=state
            self._persist(state)
            if on_event is None and not execution.tools:
                # Synchronous read-only/no-tool results carry the original
                # binding back to P28; there is no pre-binding SDK effect.
                state['bound']=True
                return
            ack=on_event(SubmissionEvent(attempt+':session','session',value)) if on_event else None
            if not isinstance(ack,BindingAck) or (ack.session_id,ack.request_id)!=(sid,request):
                raise RuntimeFault('not_ready','Persist exact original Session/request before SDK effects')
            state['bound']=True
        def tool(name,args,call):
            if not state['bound']:raise RuntimeFault('forbidden','Native Session has no SDK binding acknowledgement')
            value=on_tool(HostToolCall(operation_id,attempt,state['result'].session_id,request,call,name,args))
            if not isinstance(value,HostToolResult):raise RuntimeFault('invalid_tool_output','Use the structured original SDK callback receipt')
            if value.resources:
                try:
                    mapped=materialize(value.resources)
                    return {'value':value.value,'resources':mapped,'error':asdict(value.error) if value.error else None}
                except Exception as exc:
                    # Original SDK effects/receipts already exist. Preserve the
                    # value and report mapping failure, never rerun its callback.
                    error=SubmissionError(getattr(exc,'code','resource_mapping_failed'),'callback_resources','accepted',
                        'Original SDK callback receipt retained; recover its authorized bytes','recover_original_resource')
                    state['resourceError']=error
                    return {'value':value.value,'resources':[],'error':asdict(error)}
            return value.value if value.error is None else {'error':asdict(value.error)}
        definition=AgentDefinition('ioa_sdk',execution.definition.instructions,
            tuple(ToolBinding(t.name,t.description,t.input_schema,lambda *_:None,output_schema=t.output_schema) for t in execution.tools),
            native_tools=tuple(execution.authorization.get('nativeTools',{}).items()))
        round=_Round(operation_id,execution.actor,execution.interaction_id,definition,input.text,str(workspace),request,
                     timeout_seconds=timeout,session_id=session_id,on_session=bind,on_tool=tool,
                     native_tools_enabled=execution.authorization.get('nativeToolsEnabled') is True,
                     definition_digest=execution.definition.digest,proposal_schema=deepcopy(execution.proposal_schema))
        try:
            if submission_resources:
                try:
                    mapped=materialize(submission_resources)
                except Exception as exc:
                    value=reject(getattr(exc,'code','resource_mapping_failed'),'resources',
                        'Original authorized bytes could not be mapped; no native request submitted')
                    state['result']=value;state['finished']=True;self._persist(state)
                    return value
                # Per-turn data. Original SDK callbacks own read proof.
                round.input+='\nSDK authorized resource mappings (data): '+encode(mapped)
            result=self._execute(round)
            raw=result.native_record
            expected={'completed':(0,),'failed':(1,2),'cancelled':(130,),'timed_out':(124,)}
            verified=(state['bound'] and raw.get('type')=='result' and raw.get('request_id')==request and
                raw.get('session_id')==result.session_id and type(raw.get('exit_code')) is int and
                raw.get('exit_code') in expected.get(result.status,()))
            terminal=None;status='unknown'
            if verified:
                status='failed' if result.status=='timed_out' else result.status
                terminal=MachineTerminal(operation_id,attempt,result.session_id,request,status,str(path))
            value=replace(state['result'],state=status,session_id=result.session_id,terminal=terminal,
                output=raw.get('output_json') if raw.get('output_json') is not None else raw.get('output'),usage=result.usage)
            # The optional collector has no effect until the matching machine
            # terminal succeeds. Plain text and events are never proposals.
            if terminal is not None and status=='completed' and execution.proposal_schema is not None and result.proposal is not None:
                try:
                    validate_output(SimpleNamespace(output_schema=execution.proposal_schema),result.proposal)
                    value=replace(value,proposal=deepcopy(result.proposal))
                except RuntimeFault:
                    value=replace(value,error=SubmissionError('invalid_proposal','proposal','accepted',
                        'Original structured result does not match its proposal schema','inspect_original_result'))
            if result.error:value=replace(value,error=SubmissionError(str(result.error.get('code','host_error')),'process','unknown','Original Process returned an error'))
            if state.get('resourceError') is not None:value=replace(value,error=state['resourceError'])
            state['result']=value;self._persist(state)
            if terminal is not None and on_event:
                try:on_event(SubmissionEvent(attempt+':terminal','terminal',value))
                except Exception:value=replace(value,error=SubmissionError('event_reporting_unknown','event','accepted','Machine terminal preserved; inspect SDK event delivery'))
        except RuntimeFault as exc:
            # Invocation engine only lets validation faults escape before its
            # native run; callback/transport failures return a private unknown.
            known=state['result']
            value=replace(known,error=SubmissionError(exc.code,'validation','accepted' if known.terminal else 'not_submitted',
                'Original machine result preserved' if known.terminal else 'Process input rejected before native dispatch'))
        except Exception as exc:
            known=state['result']
            value=replace(known,state=known.state if known.terminal else 'unknown',
                error=SubmissionError('host_unknown','process','accepted' if known.terminal else 'unknown',type(exc).__name__))
        state['result']=value;state['finished']=value.terminal is not None or bool(value.error and value.error.dispatch=='not_submitted');self._persist(state)
        return value

    def _persist(self,state):
        private_json(state['path'],{'fingerprint':state['fingerprint'],
            'plannedRequestId':state['plannedRequestId'],'result':asdict(state['result']),
            'previousAttempts':state.get('previousAttempts',[]),
            'collectionError':asdict(state['resourceError']) if state.get('resourceError') else None})

    def get_request(self,query):
        from ioalab_sdk.runtime import SubmissionQuery
        if not isinstance(query,SubmissionQuery):
            raise RuntimeFault('invalid_input','Use the original submission query')
        if query.request_id is None:return None
        result=self._request_state(query.request_id)
        if result is not None and not query.matches(result):
            raise RuntimeFault('identity_conflict','Original request query changed its submission')
        return result

    def _request_state(self,request_id):
        with self._lock:
            state=self._request_states.get(request_id)
            return state['result'] if state else None

    def cancel(self,request_id):
        with self._lock:state=self._request_states.get(request_id)
        if not state:return {'state':'unknown','requestId':request_id}
        sent=self._cancel_operation(state['operation'])
        return {'state':'accepted' if sent else 'unknown','requestId':request_id,'ended':False}
