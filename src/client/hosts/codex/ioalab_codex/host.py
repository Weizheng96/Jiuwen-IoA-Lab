"""Explicit restricted SDK execution through the single S01 submission contract."""
from dataclasses import asdict,replace
from datetime import datetime,timezone
import hashlib,json,threading,time,re
from pathlib import Path
from jsonschema import Draft202012Validator
from ioalab_sdk.runtime import (HostRegistration,HostInput,HostExecution,HostResource,SubmissionResult,
 SubmissionError,MachineTerminal,SubmissionEvent,BindingAck,HostToolCall,HostToolResult,
 RuntimeFault,private_json,read_json)
# Formal move imports the existing adapter-owned transport.
from ioalab_codex.transport import Stdio,WireError

DISABLED=('shell_tool','shell_snapshot','apps','plugins','hooks','multi_agent','multi_agent_v2',
          'browser_use','browser_use_external','in_app_browser','computer_use','image_generation',
          'view_image','skill_search','skill_mcp_dependency_install','code_mode')
CONFIG={'web_search':'disabled','project_doc_max_bytes':0,'skills.include_instructions':False,
        'skills.bundled.enabled':False,'memories.use_memories':False,'memories.generate_memories':False,
        'features.skip_host_skill_discovery':True,'features.code_mode_host':True}

class CodexHost:
    registration=HostRegistration('codex','0.0.178',('get_request','cancel'),readonly_context='isolated_session')
    def __init__(self,command,*,model,skill_directories=None,read_definition_resource=None):
        if not isinstance(command,(list,tuple))or not command or not all(isinstance(x,str)and x for x in command):raise RuntimeFault('invalid_input','Use an explicit Codex argv')
        if not isinstance(model,str)or not model.strip():raise RuntimeFault('invalid_input','Use the explicit SDK model')
        if skill_directories is not None and not isinstance(skill_directories,dict):raise RuntimeFault('invalid_input','Use named absolute SDK skill directories')
        sources=dict(skill_directories or {})
        if len(sources)>64 or any(not isinstance(k,str)or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',k)or not isinstance(v,str)or not Path(v).is_absolute()for k,v in sources.items()):
            raise RuntimeFault('invalid_input','Use bounded SDK skill names and absolute directories')
        if len({k.casefold()for k in sources})!=len(sources):raise RuntimeFault('invalid_input','SDK skill names must be case-insensitively unique')
        self.command=tuple(command);self.model=model;self.skill_directories=sources
        self._read_definition=read_definition_resource;self._lock=threading.RLock();self._operations={};self._requests={};self._running={}
    @classmethod
    def from_config(cls,value,*,base_dir):
        if not isinstance(value,dict)or set(value)-{'command','model','skill_directories'}or not {'command','model'}<=set(value):
            raise RuntimeFault('invalid_input','Codex SDK host requires command/model and optional skill_directories')
        return cls(value['command'],model=value['model'],skill_directories=value.get('skill_directories'))
    def check(self):
        # S02 is runtime-only and never a submit/config/start precondition.
        with self._lock:
            if self._running:return {'state':'busy','evidence':'This adapter owns an unfinished native request'}
        try:
            with Stdio([*self.command,'app-server','--listen','stdio://'],cwd=None)as wire:
                deadline=time.monotonic()+5;wire.initialize(deadline)
                cursor=None
                for _ in range(16):
                    page=wire.request('model/list',{'includeHidden':True,**({'cursor':cursor}if cursor else{})},deadline)
                    found=self.model in {x.get('model')for x in page.get('data',[])}|{x.get('id')for x in page.get('data',[])}
                    if found:return {'state':'available','evidence':'Read-only public model inventory; no inference'}
                    cursor=page.get('nextCursor')
                    if not cursor:return {'state':'unavailable','evidence':'Complete public inventory does not contain the explicit SDK model'}
                return {'state':'unknown','evidence':'Public inventory pagination exceeded the bounded check'}
        except Exception:return {'state':'unknown','evidence':'Bounded public inventory could not be read'}
    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        if not isinstance(input,HostInput)or input.attempt is None:raise RuntimeFault('invalid_input','Use SDK S01 input and original attempt')
        attempt=input.attempt.nonce
        if not isinstance(execution,HostExecution):
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('not_ready','definition','not_submitted','Explicit SDK calls require a frozen execution'))
        if (execution.actor,execution.profile)!=(input.attempt.actor,input.attempt.profile):
            raise RuntimeFault('identity_conflict','Execution actor/profile differs from the original attempt')
        native_enabled=execution.authorization.get('nativeToolsEnabled',False)
        if type(native_enabled)is not bool:
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('invalid_input','native_permissions','not_submitted','SDK native-tool authorization must be a strict boolean'))
        if native_enabled:
            # This explicit SDK driver only offers its original restricted
            # dynamic-tool execution. An empty requirements map does not mean
            # that a true native-capability authorization can be ignored.
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('not_ready','native_permissions','not_submitted','This explicit SDK driver cannot honor nativeToolsEnabled=true'))
        if not isinstance(execution.workspace,str)or not Path(execution.workspace).is_absolute():
            raise RuntimeFault('invalid_input','Use the original absolute SDK workspace')
        if execution.proposal_schema is not None:
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('not_ready','proposal_schema','not_submitted','Preserve the original proposal schema until supported'))
        if execution.deadline and datetime.fromisoformat(execution.deadline.replace('Z','+00:00'))<=datetime.now(timezone.utc):
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('expired','deadline','not_submitted','Original execution deadline has passed'))
        # File/resource behavior cannot be silently dropped. P26 is the only reader.
        if (input.resources or execution.definition.resources)and self._read_definition is None:
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('not_ready','resource_mapping','not_submitted','Inject the original SDK P26 frozen resource reader'))
        if any(v!='deny'for v in execution.authorization.get('nativeTools',{}).values()):
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('not_ready','native_permissions','not_submitted','Restricted explicit SDK native permissions are not supported'))
        if input.resources or execution.definition.resources:
            return SubmissionResult(operation_id,attempt,'failed',session_id,error=SubmissionError('not_ready','resource_mapping','not_submitted','Await final SDK P26 frozen reader/sink contract'))
        definition=asdict(execution);fingerprint=hashlib.sha256(json.dumps({'text':input.text,'resources':[asdict(x)for x in input.resources],'execution':definition},sort_keys=True).encode()).hexdigest()
        workspace=Path(execution.workspace or '').resolve()
        if execution.workspace is None:raise RuntimeFault('invalid_input','Use the original SDK workspace')
        workspace.mkdir(parents=True,exist_ok=True)
        path=workspace/'codex-submissions'/(hashlib.sha256(operation_id.encode()).hexdigest()+'.json')
        with self._lock:
            if path.exists():
                saved=read_json(path)
                if saved['fingerprint']!=fingerprint or saved['actor']!=input.attempt.actor or saved['profile']!=input.attempt.profile:raise RuntimeFault('identity_conflict','Keep the original S01 scope')
                if session_id is not None and saved['result'].get('session_id')!=session_id:raise RuntimeFault('identity_conflict','Keep the actual original session')
                return self._restore(saved['result']) # unknown is never re-dispatched
            result=SubmissionResult(operation_id,attempt,'unknown',session_id)
            saved={'fingerprint':fingerprint,'actor':input.attempt.actor,'profile':input.attempt.profile,'result':asdict(result)}
            private_json(path,saved);self._operations[operation_id]=result
        deadline=None
        if execution.deadline:
            deadline=time.monotonic()+max(0,(datetime.fromisoformat(execution.deadline.replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds())
        def handshake_deadline():
            limit=time.monotonic()+15
            return limit if deadline is None else min(limit,deadline)
        aliases={'ioa_'+hashlib.sha256(t.name.encode()).hexdigest()[:32]:t for t in execution.tools}
        sid=session_id;request=None;wire=None;bound=False;terminal=None;output=[];usage={};cancelled=threading.Event()
        def publish(kind,value):
            nonlocal result
            result=value;saved['result']=asdict(value);private_json(path,saved)
            with self._lock:
                self._operations[operation_id]=value
                if value.request_id:self._requests[value.request_id]=value
            if on_event:
                ack=on_event(SubmissionEvent(hashlib.sha256(json.dumps([operation_id,attempt,kind,asdict(value)],sort_keys=True).encode()).hexdigest(),kind,value))
                if kind=='session'and(not isinstance(ack,BindingAck)or ack.session_id!=value.session_id or ack.request_id!=value.request_id):
                    raise RuntimeFault('identity_conflict','SDK P28 did not persist the real native session')
                return ack
        def packet(message):
            nonlocal terminal,usage
            method=message.get('method');params=message.get('params',{})
            if params.get('threadId')!=sid:
                if 'id'in message:wire.send({'id':message['id'],'error':{'code':-32602,'message':'Unbound thread'}})
                return
            if method=='turn/started':
                if request is None or params['turn']['id']!=request:raise WireError('Unbound native turn start')
            if method=='thread/tokenUsage/updated':usage=params.get('tokenUsage',{})
            if method=='turn/completed':
                actual=params['turn']
                if request is None or actual['id']!=request:raise WireError('Unbound native terminal')
                terminal=actual
            if method=='item/completed'and params.get('item',{}).get('type')=='agentMessage':
                if params.get('turnId')!=request:raise WireError('Unbound native output')
                output.append(params['item'].get('text',''))
            if method=='item/tool/call':
                tool=aliases.get(params.get('tool'))
                if not bound or not request or params.get('turnId')!=request or params.get('namespace')!='ioa_sdk' or tool is None:raise WireError('Unbound SDK tool')
                problem=next(Draft202012Validator(tool.input_schema).iter_errors(params['arguments']),None)
                if cancelled.is_set():value={'status':'rejected','error':{'code':'closed'}}
                elif problem:value={'status':'rejected','error':{'code':'invalid_input','message':problem.message}}
                else:
                    if on_tool is None:raise WireError('Missing original SDK P40 callback')
                    returned=on_tool(HostToolCall(operation_id,attempt,sid,request,params['callId'],tool.name,params['arguments']))
                    if not isinstance(returned,HostToolResult):raise WireError('Invalid neutral tool result')
                    if returned.resources:raise WireError('SDK tool output resources require the P26 output mapper')
                    value=returned.value if returned.error is None else {'status':'rejected','error':asdict(returned.error)}
                wire.send({'id':message['id'],'result':{'success':True,'contentItems':[{'type':'inputText','text':json.dumps(value,ensure_ascii=False)}]}})
            elif 'id'in message:wire.send({'id':message['id'],'error':{'code':-32601,'message':'This explicit restricted SDK execution has no native approval transport'}})
        def acknowledge_request():
            nonlocal bound
            if bound:return
            if on_event is None:raise RuntimeFault('not_ready','SDK P28 binding acknowledgement is required')
            publish('session',SubmissionResult(operation_id,attempt,'accepted',sid,request))
            bound=True
        try:
            # Explicit SDK's existing restricted capabilities remain explicit.
            argv=[*self.command,'app-server','--listen','stdio://']
            for name in DISABLED:argv+=['--disable',name]
            for key,value in CONFIG.items():argv+=['-c',key+'='+json.dumps(value)]
            wire=Stdio(argv,cwd=str(workspace));wire.initialize(handshake_deadline())
            if any(v!='deny'for v in execution.authorization.get('nativeTools',{}).values()):raise RuntimeFault('not_ready','Restricted explicit SDK native permissions are not supported')
            if input.resources or execution.definition.resources:raise RuntimeFault('not_ready','P26 mapping requires the final reader/sink seam before native dispatch')
            current=wire.request('config/read',{'includeLayers':False,'cwd':str(workspace)},handshake_deadline()).get('config',{})
            overrides={**CONFIG,**{'features.'+name:False for name in DISABLED}}
            overrides['mcp_servers']={name:{**{k:v for k,v in settings.items()if v is not None},'enabled':False}for name,settings in current.get('mcp_servers',{}).items()}
            # Retain explicit SDK isolation; this is never the product native factory.
            params={'model':self.model,'cwd':str(workspace),'sandbox':'read-only','approvalPolicy':'never','baseInstructions':execution.definition.instructions,'config':overrides}
            if session_id:created=wire.request('thread/resume',{**params,'threadId':session_id,'excludeTurns':True},handshake_deadline())
            else:created=wire.request('thread/start',{**params,'dynamicTools':[{'type':'namespace','name':'ioa_sdk','description':'Original controlled SDK tools','tools':[{'type':'function','name':alias,'description':t.description,'inputSchema':t.input_schema,'deferLoading':False}for alias,t in aliases.items()]}]},handshake_deadline())
            actual_sid=created['thread'].get('id')
            if not isinstance(actual_sid,str)or not 1<=len(actual_sid)<=8192:raise WireError('Codex did not return an actual native session')
            sid=actual_sid
            if session_id and sid!=session_id:raise WireError('Original SID changed')
            if created.get('model')!=self.model or created.get('sandbox',{}).get('type')!='readOnly':raise WireError('Explicit SDK model or sandbox changed')
            if created['thread'].get('status',{}).get('type')=='active':raise WireError('Original explicit SDK thread is already active')
            publish('session',SubmissionResult(operation_id,attempt,'accepted',sid))
            if on_event is None:raise RuntimeFault('not_ready','SDK P28 binding acknowledgement is required')
            early=[];early_bytes=0
            def buffer(message):
                nonlocal early_bytes
                early_bytes+=len(json.dumps(message).encode())
                if len(early)>=128 or early_bytes>1024*1024:raise WireError('Codex early event buffer exceeds its bound')
                early.append(message)
            started=wire.request('turn/start',{'threadId':sid,'input':[{'type':'text','text':input.text}],'model':self.model,'approvalPolicy':'never'},handshake_deadline(),buffer)
            actual_request=started.get('turn',{}).get('id')
            if not isinstance(actual_request,str)or not 1<=len(actual_request)<=8192:raise WireError('Codex did not return an actual native request')
            request=actual_request
            # Check every early turn reference before P28 activation or any tool
            # callback: a later foreign packet cannot validate an earlier effect.
            for message in early:
                p=message.get('params',{})
                if p.get('threadId')!=sid:continue
                actual=p.get('turn',{}).get('id')if message.get('method')in('turn/started','turn/completed')else p.get('turnId')
                if actual is not None and actual!=request:raise WireError('Early event belongs to another native turn')
            acknowledge_request()
            with self._lock:self._running[request]=(wire,sid,cancelled)
            publish('running',SubmissionResult(operation_id,attempt,'running',sid,request))
            for message in early:packet(message)
            while terminal is None:packet(wire.receive(deadline))
            status={'completed':'completed','failed':'failed','interrupted':'cancelled'}.get(terminal.get('status'))
            if status is None:raise WireError('Unknown machine terminal')
            machine=MachineTerminal(operation_id,attempt,sid,request,status,'codex-event:'+hashlib.sha256(json.dumps(terminal,sort_keys=True).encode()).hexdigest())
            saved['nativeTerminal']=terminal
            result=SubmissionResult(operation_id,attempt,status,sid,request,'handled','\n'.join(output),terminal=machine,usage=usage)
            publish('terminal',result)
        except Exception as exc:
            if terminal is not None and request and terminal.get('id')==request and terminal.get('status')in('completed','failed','interrupted'):
                state={'completed':'completed','failed':'failed','interrupted':'cancelled'}[terminal['status']]
                machine=MachineTerminal(operation_id,attempt,sid,request,state,'codex-event:'+hashlib.sha256(json.dumps(terminal,sort_keys=True).encode()).hexdigest())
                result=SubmissionResult(operation_id,attempt,state,sid,request,'handled','\n'.join(output),terminal=machine,usage=usage)
                saved['nativeTerminal']=terminal
            error=SubmissionError('host_event_error'if result.terminal else 'host_unknown','event_callback'if result.terminal else 'native_protocol','accepted'if result.terminal else 'unknown',type(exc).__name__)
            result=replace(result,error=error)if result.terminal else SubmissionResult(operation_id,attempt,'unknown',sid,request,error=error)
            saved['result']=asdict(result);saved['notificationError']=asdict(error);private_json(path,saved)
            with self._lock:
                self._operations[operation_id]=result
                if request:self._requests[request]=result
        finally:
            with self._lock:
                if request:self._running.pop(request,None)
            if wire:wire.close()
        return result
    @staticmethod
    def _restore(value):
        value=dict(value)
        if value.get('error'):value['error']=SubmissionError(**value['error'])
        if value.get('terminal'):value['terminal']=MachineTerminal(**value['terminal'])
        value['resources']=tuple(HostResource(**item)for item in value.get('resources',()))
        return SubmissionResult(**value)
    def get_request(self,request_id):
        with self._lock:
            value=self._requests.get(request_id)
            if value is None:raise RuntimeFault('unknown_result','No machine binding for the original native request')
            return value
    def cancel(self,request_id):
        with self._lock:owned=self._running.get(request_id)
        if owned is None:return {'state':'unknown','requestId':request_id}
        wire,sid,cancelled=owned;cancelled.set()
        try:wire.begin('turn/interrupt',{'threadId':sid,'turnId':request_id});return {'state':'accepted','requestId':request_id}
        except Exception:return {'state':'unknown','requestId':request_id}
