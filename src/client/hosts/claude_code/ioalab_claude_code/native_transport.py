"""Private S01 control handler for the SDK resident's authenticated native route.

No listener, account configuration, SDK claim or business effect is created here.
The SDK authenticates grant scope before calling exchange; native facts remain
adapter-owned. In-memory loss cannot prove that a native request never ran.
"""
import copy
from concurrent.futures import Future
from dataclasses import replace,asdict
import hashlib
import json
import threading
import time
from pathlib import Path

from ioalab_sdk.runtime import (RuntimeFault,SubmissionResult,SubmissionError,SubmissionEvent,
                               BindingAck,MachineTerminal,HostToolCall,HostToolResult,private_json)


class NativeNotSubmitted(RuntimeFault):
    """Internal proof only: no payload has crossed this adapter's dispatch boundary."""


class NativeTransport:
    def __init__(self,*,host_ref,receipt_root=None,launch_store=None):
        self.host_ref=host_ref
        self.lock=threading.RLock()
        self.condition=threading.Condition(self.lock)
        self.sessions={}
        self.requests={}
        self.active={}
        self.setup_callbacks=0
        self.route_bound=False
        self.closed=False
        self.receipt_root=Path(receipt_root) if receipt_root is not None else None
        self.launch_store=launch_store
        self.observations=_ObservationLane(host_ref=host_ref,persist=self._persist_observation)

    def bind_entry_control(self,callback):
        """SDK-only resource injection; ordinary native packets cannot bind it."""
        if self.launch_store is None:
            raise RuntimeFault('not_ready','Bind private native launch storage')
        self.launch_store.bind_entry_control(callback)

    def _setup_call(self,callback):
        with self.condition:self.setup_callbacks+=1
        try:return callback()
        finally:
            with self.condition:
                self.setup_callbacks-=1
                self.condition.notify_all()

    def _persist_observation(self,record):
        if self.receipt_root is None:raise RuntimeFault('not_ready','Bind private observation receipt storage')
        result=record['result']
        key=hashlib.sha256(json.dumps([record['scope'],result['operation_id'],result['attempt_id']],sort_keys=True).encode()).hexdigest()
        private_json(self.receipt_root/(key+'.json'),record)

    def observe(self,operation_id,input,session_id,*,execution,on_event):
        # Internal lane. It never waits behind an ordinary chat's active item.
        with self.lock:
            peer=self.sessions.get(session_id)
            if (self.closed or not self.route_bound or self.receipt_root is None or not peer or
                peer['profiles'].get(input.attempt.profile)!=input.attempt.actor):
                raise NativeNotSubmitted('not_ready','Bind the real business session and observation resources')
        if input.resources or execution.definition.resources:
            raise NativeNotSubmitted('not_ready','Bind the original authorized bytes reader for readonly materials')
        return self.observations.open(operation_id,input,execution,session_id,on_event=on_event)

    def wait_observation(self,key,timeout):
        return self.observations.wait(key,timeout)

    def wait_session(self,intended,attempt,timeout):
        with self.condition:
            return self.condition.wait_for(lambda:bool(self.sessions.get(intended) and
                self.sessions[intended]['profiles'].get(attempt.profile)==attempt.actor),timeout)

    def start(self):
        """Private assembly resource hook, not HostAdapter capability discovery."""
        with self.lock:self.route_bound=True

    def submit(self,operation_id,input,session_id,*,on_event,execution=None,on_tool=None):
        return self._queue(operation_id,input,session_id,on_event=on_event,allow_pending=False,execution=execution,on_tool=on_tool)

    def submit_new(self,operation_id,input,intended,*,on_event,execution=None,on_tool=None):
        # Intended is only a launch locator until a native API hello supplies it.
        return self._queue(operation_id,input,intended,on_event=on_event,allow_pending=True,execution=execution,on_tool=on_tool)

    def wait_request(self,key,timeout):
        with self.condition:
            entry=self.requests[key]
            def finished():
                return ((entry['result'].terminal is not None and entry['pending_terminal'] is None and not entry.get('publishing')) or
                    entry['result'].state=='unknown' and entry['result'].error is not None)
            if not self.condition.wait_for(finished,timeout):
                if not entry['polled']:
                    entry['abandoned']=True
                    entry['result']=replace(entry['result'],state='waiting',error=SubmissionError(
                        'not_ready','native_wait','not_submitted','The original prompt has not left the native queue','wait_original'))
                else:
                    entry['result']=replace(entry['result'],state='unknown',error=SubmissionError(
                        'unknown_result','native_wait','unknown','Inspect the original native request; do not resubmit','inspect_original_request'))
            return copy.deepcopy(entry['result'])

    def _queue(self,operation_id,input,session_id,*,on_event,allow_pending,execution,on_tool):
        if input.attempt is None:raise RuntimeFault('invalid_input','Use the SDK submission attempt')
        if on_event is None:raise RuntimeFault('not_ready','Native submissions require persistent event ACKs')
        if execution is not None:
            if (execution.actor,execution.profile)!=(input.attempt.actor,input.attempt.profile):
                raise NativeNotSubmitted('identity_conflict','Preserve the SDK-owned native execution target')
            if execution.kind!='business':raise NativeNotSubmitted('not_ready','Bind the restricted native observation engine')
            # D37: nativeToolsEnabled=false is submitted unrestricted (no static restriction declared).
            if execution.tools and not callable(on_tool):raise NativeNotSubmitted('not_ready','Bind the original SDK tool callback')
        with self.lock:
            if self.closed or not self.route_bound:
                raise NativeNotSubmitted('not_ready','Bind the native control route on the original resident','bind_native_transport')
            session=self.sessions.get(session_id)
            if session and session['profiles'].get(input.attempt.profile)!=input.attempt.actor:
                session=None
            if session is None and not allow_pending:
                raise NativeNotSubmitted('not_ready','Enable the packaged IoA plugin in the original Claude session; reload or resume that ID','enable_native_extension')
            key=(operation_id,input.attempt.nonce)
            if key in self.requests:
                old=self.requests[key]
                if (old['payload']['scopeDigest']!=input.attempt.scope_digest or old['payload']['text']!=input.text or
                        old['target']!=(input.attempt.actor,input.attempt.profile) or old['wanted']!=session_id or
                        old['execution']!=execution):
                    raise RuntimeFault('id_conflict','The original native transport payload changed')
                return old['result']
            result=SubmissionResult(operation_id,input.attempt.nonce,'waiting',session_id if session else None)
            payload={'operationId':operation_id,'attemptId':input.attempt.nonce,'scopeDigest':input.attempt.scope_digest,
                     'text':input.text,'textDigest':hashlib.sha256(input.text.encode()).hexdigest()}
            self.requests[key]={'result':result,'payload':payload,'on_event':on_event,'polled':False,'ready':False,'wanted':session_id,
                                'target':(input.attempt.actor,input.attempt.profile),'execution':copy.deepcopy(execution),'on_tool':on_tool,
                                'grant_binding':copy.deepcopy(session['scopes'][input.attempt.profile]) if session else None,
                                'calls':{},'callbacks':0,'pending_terminal':None,'tool_unknown':False}
        if session is None:return result
        return self._bind(key,session_id)

    def _bind(self,key,session_id,request_id=None):
        with self.lock:
            entry=self.requests[key]
            if entry['result'].request_id not in (None,request_id):
                raise RuntimeFault('identity_conflict','Preserve the original native request')
            result=replace(entry['result'],session_id=session_id,request_id=request_id,
                           state='running' if request_id else entry['result'].state)
            on_event=entry['on_event']
            if entry['grant_binding'] is None:
                entry['grant_binding']=copy.deepcopy(self.sessions[session_id]['scopes'][entry['target'][1]])
        # No transport lock spans persistence callbacks.
        ack=on_event(SubmissionEvent(result.attempt_id+':session:'+str(request_id),'session',result))
        if not isinstance(ack,BindingAck) or (ack.session_id,ack.request_id)!=(session_id,request_id):
            raise RuntimeFault('identity_conflict','The SDK did not acknowledge the native session')
        with self.lock:
            self.requests[key]['result']=result
            self.requests[key]['ready']=True
        return result

    def exchange(self,scope,body):
        """Called only by the SDK-authenticated route; body cannot choose scope."""
        if not isinstance(scope,dict) or scope.get('hostRef')!=self.host_ref or scope.get('adapterId')!='claude_code':
            raise RuntimeFault('forbidden','Native control belongs to another adapter')
        observation_kinds=('poll_observation','observation_reference','observation_dispatch','observation_unknown','observation_return')
        if not isinstance(body,dict) or body.get('kind') not in ('hello','entry_setup','entry_recover','poll','accepted','uncertain','reference','terminal','tool',*observation_kinds):
            raise RuntimeFault('invalid_input','Use a native transport message')
        sid,generation=body.get('sessionId'),body.get('generation')
        if not all(isinstance(x,str) and 1<=len(x)<=128 for x in (sid,generation)):
            raise RuntimeFault('invalid_input','Use actual native session and generation')
        if body['kind']=='hello':
            from pathlib import Path
            cwd=body.get('workspace')
            if not isinstance(cwd,str) or not Path(cwd).is_absolute():raise RuntimeFault('invalid_input','Capture the actual native cwd')
            with self.lock:
                old=self.sessions.get(sid)
                if old and (old['generation'],old['workspace'])!=(generation,cwd):
                    raise RuntimeFault('identity_conflict','Restore the original native writer generation')
                if old is None:
                    old={'generation':generation,'workspace':cwd,'profiles':{},'scopes':{}}
                    self.sessions[sid]=old
                actor=old['profiles'].get(scope['profile'])
                if actor is not None and actor!=scope['actor']:raise RuntimeFault('identity_conflict','The native role binding changed')
                old['profiles'][scope['profile']]=scope['actor']
                old['scopes'][scope['profile']]=copy.deepcopy(scope)
                pending=[key for key,value in self.requests.items() if value['wanted']==sid and not value['ready']
                         and value['target']==(scope['actor'],scope['profile'])]
            for key in pending:self._bind(key,sid)
            with self.condition:self.condition.notify_all()
            # Only ACK an already saved receipt. A hello never creates an intent,
            # claims permission or replaces the original launch selection.
            if self.launch_store is not None:
                try:self._setup_call(lambda:self.launch_store.recover(scope))
                except RuntimeFault:pass
            return {'registered':True}
        with self.lock:
            session=self.sessions.get(sid)
            if not session or session['generation']!=generation or session['profiles'].get(scope['profile'])!=scope['actor']:
                raise RuntimeFault('identity_conflict','Native control changed its original source')
        if body['kind']=='entry_recover':
            if set(body)!={'kind','sessionId','generation'} or self.launch_store is None:
                raise RuntimeFault('invalid_input','Recover only the original saved native launch receipt')
            return self._setup_call(lambda:self.launch_store.recover(scope))
        if body['kind']=='entry_setup':
            if set(body)!={'kind','sessionId','generation','entryRef','capture'}:
                raise RuntimeFault('invalid_input','Use the fixed native setup capture; no caller claim is accepted')
            if not isinstance(body['entryRef'],str) or not body['entryRef'] or self.launch_store is None:
                raise RuntimeFault('not_ready','Bind the original explicit SDK setup and private launch resource')
            if self.closed:raise RuntimeFault('closed','Native setup no longer accepts a new capture')
            return self._setup_call(lambda:self.launch_store.capture(scope,entry_ref=body['entryRef'],capture=body['capture'],workspace=session['workspace']))
        if body['kind'] in observation_kinds:
            if body['kind']=='poll_observation':return {'item':None if self.closed else self.observations.poll(scope,sid,generation)}
            return self.observations.event(scope,generation,body)
        with self.lock:
            if body['kind']=='poll':
                if self.closed or sid in self.active:return {'item':None}
                for key,entry in self.requests.items():
                    if (entry['result'].session_id==sid and entry['target']==(scope['actor'],scope['profile'])
                            and entry['grant_binding']==scope and entry['ready'] and not entry['polled'] and not entry.get('abandoned')):
                        entry['polled']=True
                        self.active[sid]=key  # Irreversible uncertainty boundary before returning bytes.
                        return {'item':copy.deepcopy(entry['payload'])}
                return {'item':None}
            key=(body.get('operationId'),body.get('attemptId'))
            entry=self.requests.get(key)
            replay=bool(entry and entry['result'].terminal and body['kind']=='terminal')
            if (not entry or not replay and self.active.get(sid)!=key or entry['target']!=(scope['actor'],scope['profile']) or entry['grant_binding']!=scope):
                raise RuntimeFault('identity_conflict','Preserve the original native submission and role')
            if body['kind'] in ('reference','terminal','tool'):
                request_id=body.get('requestId');proof=body.get('proof')
                self._verify_proof(entry,generation,request_id,proof)
                old=entry.get('proof')
                if old is not None and old!=proof:raise RuntimeFault('identity_conflict','Original native evidence changed')
                entry['proof']=copy.deepcopy(proof)
            else:request_id=None
            before=entry['result']
        if body['kind']=='reference':
            self._bind(key,sid,request_id)
            return {'recorded':True,'requestId':request_id}
        if body['kind']=='tool':return self._tool(key,sid,generation,request_id,body)
        if body['kind']=='terminal':
            # A terminal cannot bind a request implicitly or bypass the SDK ACK.
            with self.lock:
                entry=self.requests[key]
                if not entry['ready'] or entry['result'].request_id!=request_id:
                    raise RuntimeFault('not_ready','Persist the actual native request before terminal delivery')
                state=body.get('state')
                if state not in ('completed','failed','cancelled') or body.get('coreComplete') is not True:
                    raise RuntimeFault('invalid_input','Use a core turn.complete machine event')
                evidence='claude-native:'+generation+':'+request_id+':'+proof['rowUuid']
                terminal=MachineTerminal(key[0],key[1],sid,request_id,state,evidence)
                before=entry['result']
                if before.terminal and before.terminal!=terminal:
                    raise RuntimeFault('identity_conflict','Preserve the original machine terminal')
                if before.terminal:
                    if before.output!=body.get('output'):raise RuntimeFault('identity_conflict','Preserve the original terminal output')
                    return {'recorded':True}
                result=replace(before,state=state,terminal=terminal,output=body.get('output'),error=None)
                entry['pending_terminal']=result
                entry['terminal_fact']={'proof':copy.deepcopy(proof),'nativeGeneration':generation}
                draining=bool(entry['callbacks'])
            if not draining:self._publish_terminal(key)
            return {'recorded':True,'draining':draining}
        with self.lock:
            # Native acceptance may arrive after the stronger request binding.
            before=entry['result']
            accepted=body['kind']=='accepted'
            result=replace(before,state=before.state if before.request_id else 'accepted' if accepted else 'unknown',delivery='accepted' if accepted else before.delivery,
                error=None if accepted else SubmissionError('unknown_result','native_submit','unknown','Preserve the original native request'))
            entry['result']=result
            callback=entry['on_event']
        callback(SubmissionEvent(result.attempt_id+':'+body['kind'],'accepted' if accepted else 'error',result))
        with self.condition:self.condition.notify_all()
        return {'recorded':True}

    def _publish_terminal(self,key):
        with self.lock:
            entry=self.requests[key]
            result=entry['pending_terminal']
            if result is None or entry['callbacks'] or entry.get('publishing'):return
            entry['publishing']=True
            if entry['tool_unknown']:
                result=replace(result,error=SubmissionError('tool_unknown','sdk_tool','accepted','Inspect the original SDK tool receipt'))
            callback=entry['on_event']
            record={'hostRef':self.host_ref,'grantScope':entry['grant_binding'],'operationId':key[0],'attemptId':key[1],'scopeDigest':entry['payload']['scopeDigest'],
                'actor':entry['target'][0],'profile':entry['target'][1],**entry['terminal_fact'],'result':asdict(result)}
        try:
            if self.receipt_root is None:raise RuntimeFault('not_ready','Bind private native terminal storage')
            name=hashlib.sha256(json.dumps([key,entry['target']],sort_keys=True).encode()).hexdigest()+'.json'
            private_json(self.receipt_root/'requests'/name,record)
        except Exception:
            with self.condition:
                entry['publishing']=False
                entry['result']=replace(entry['result'],state='unknown',error=SubmissionError(
                    'unknown_result','native_receipt','unknown','Preserve the original machine fact and restore its private receipt','restore_original_receipt'))
                self.condition.notify_all()
            return
        try:callback(SubmissionEvent(key[1]+':terminal:'+result.request_id,'terminal',result))
        except Exception:
            if result.error is None:result=replace(result,error=SubmissionError(
                'unknown_result','event_reporting','accepted','The original machine terminal is saved; restore SDK event reporting','inspect_original_request'))
        finally:
            with self.condition:
                entry['result']=result
                entry['publishing']=False
                self.active.pop(result.session_id,None)
                entry['pending_terminal']=None
                self.condition.notify_all()

    def _tool(self,key,sid,generation,request_id,body):
        with self.lock:
            entry=self.requests[key];execution=entry['execution'];callback=entry['on_tool']
            if (not entry['ready'] or entry['result'].request_id!=request_id or entry['pending_terminal'] is not None or
                execution is None or callback is None):
                raise RuntimeFault('not_ready','Bind the original native request and SDK tool scope before effects')
            name,args,call_id=body.get('name'),body.get('arguments'),body.get('callId')
            if not isinstance(args,dict) or name not in {tool.name for tool in execution.tools}:
                raise RuntimeFault('forbidden','Use only the original frozen SDK tools')
            fingerprint=hashlib.sha256(json.dumps({'name':name,'arguments':args},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            proof=body.get('toolProof')
            if (not isinstance(call_id,str) or not call_id or not isinstance(proof,dict) or
                proof.get('callId')!=call_id or proof.get('requestId')!=request_id or
                proof.get('observerGeneration')!=generation or proof.get('core') is not True or
                proof.get('nativeName')!='mcp__ioa-native-client__sdk_tool' or proof.get('argumentsSha256')!=fingerprint):
                raise RuntimeFault('identity_conflict','Use the actual core native tool call and arguments')
            old=entry['calls'].get(call_id)
            if old:
                if old[0]!=fingerprint:raise RuntimeFault('id_conflict','The original native tool arguments changed')
                future=old[1];owner=False
            else:
                future=Future();entry['calls'][call_id]=(fingerprint,future);entry['callbacks']+=1;owner=True
        if owner:
            try:
                value=callback(HostToolCall(key[0],key[1],sid,request_id,call_id,name,copy.deepcopy(args)))
                if not isinstance(value,HostToolResult):raise RuntimeFault('invalid_tool_output','Use the original SDK tool result')
                if value.error and value.error.dispatch!='not_submitted':
                    with self.lock:entry['tool_unknown']=True
                future.set_result(asdict(value))
            except BaseException as exc:
                with self.lock:entry['tool_unknown']=True
                future.set_exception(exc)
            finally:
                with self.condition:
                    entry['callbacks']-=1
                    finish=entry['pending_terminal'] is not None and not entry['callbacks']
                    self.condition.notify_all()
                if finish:self._publish_terminal(key)
        # No host/SDK/store lock spans a callback or another call's wait.
        return {'toolResult':copy.deepcopy(future.result())}

    def _verify_proof(self,entry,generation,request_id,proof):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128 or not isinstance(proof,dict):
            raise RuntimeFault('invalid_input','Use the actual native turn and digest evidence')
        expected=entry['payload']['textDigest']
        if (proof.get('observerGeneration')!=generation or proof.get('inputSha256')!=expected or
            proof.get('storedSha256')!=proof.get('turnSha256') or
            not isinstance(proof.get('storedSha256'),str) or len(proof['storedSha256'])!=64 or
            not isinstance(proof.get('rowUuid'),str) or not 1<=len(proof['rowUuid'])<=128 or
            not isinstance(proof.get('engineBuild'),str) or not proof['engineBuild'] or
            any(proof.get(field) is not True for field in ('rawInputMatch','originVerified','uuidPinned','core','promptVerified','uniqueTurn'))):
            raise RuntimeFault('identity_conflict','The operation is not proven to be the original native turn')

    def check(self):
        """Resident control facts only; launch prerequisites are judged by the host."""
        with self.lock:
            if self.closed or not self.route_bound:
                return {'state':'unavailable','basis':'Native control route is closed or not bound on this resident'}
            if self.active or self.observations.pending():
                return {'state':'busy','basis':'An original native submission or observation is executing'}
            return {'state':'available','basis':'Native control route bound; no model routing or inference implied'}

    def close(self):
        with self.lock:
            if self.active or self.observations.pending() or self.setup_callbacks:raise RuntimeFault('not_ready','Keep native control until original executions end')
            self.closed=True

    def drain(self,timeout):
        deadline=None if timeout is None else time.monotonic()+timeout
        with self.condition:
            if not self.condition.wait_for(lambda:not self.active and not self.setup_callbacks,timeout):return False
        return self.observations.drain(None if deadline is None else max(0,deadline-time.monotonic()))


class _ObservationLane:
    def __init__(self,*,host_ref,persist):
        self.host_ref,self.persist=host_ref,persist
        self.lock=threading.RLock()
        self.changed=threading.Condition(self.lock)
        self.entries={}

    def pending(self):
        with self.lock:return any(entry['stage'] not in ('returned','not_submitted') for entry in self.entries.values())

    def drain(self,timeout):
        with self.changed:return self.changed.wait_for(lambda:not self.pending(),timeout)

    def wait(self,key,timeout):
        with self.changed:
            entry=self.entries[key]
            done=self.changed.wait_for(lambda:entry['stage'] in ('returned','unknown','not_submitted'),timeout)
            if not done:
                before=entry['result']
                if entry['reference'] is None and not entry['dispatched']:
                    entry.update(stage='not_submitted',result=replace(before,state='waiting',
                        error=SubmissionError('not_ready','observation_wait','not_submitted','No readonly API was dispatched; retain the original native session','wait_original')))
                else:
                    entry.update(stage='unknown',result=replace(before,state='unknown',
                        error=SubmissionError('unknown_result','observation_wait','unknown','Inspect the original readonly invocation','inspect_original_request')))
                self.changed.notify_all()
            return copy.deepcopy(entry['result'])

    def open(self,operation,input,execution,sid,*,on_event):
        # SDK P20 supplied execution/attempt and authenticated observer permit.
        # No parent/model flag in the ordinary business JSON authorizes this.
        attempt=input.attempt
        if execution.kind!='observation' or (execution.actor,execution.profile)!=(attempt.actor,attempt.profile) or not sid:
            raise RuntimeFault('identity_conflict','Use the original SDK readonly scope and actual business session')
        key=(operation,attempt.nonce)
        result=SubmissionResult(operation,attempt.nonce,'waiting',sid)
        with self.lock:
            if key in self.entries:raise RuntimeFault('unknown_result','Inspect the original readonly invocation')
            self.entries[key]={'result':result,'attempt':attempt,'callback':on_event,'boot':None,'generation':None,
                'stage':'binding','reference':None,'dispatched':False,'job':{'operationId':operation,'attemptId':attempt.nonce,
                'scopeDigest':attempt.scope_digest,'actor':attempt.actor,'profile':attempt.profile,'hostRef':self.host_ref,
                'sessionId':sid,'prompt':input.text,'instructions':execution.definition.instructions,
                'inputSha256':hashlib.sha256(input.text.encode()).hexdigest()}}
        ack=on_event(SubmissionEvent(attempt.nonce+':observation-session','session',result))
        if not isinstance(ack,BindingAck) or (ack.session_id,ack.request_id)!=(sid,None):
            raise RuntimeFault('not_ready','Persist the actual business session before readonly dispatch')
        with self.lock:self.entries[key]['stage']='queued'
        return key

    def poll(self,scope,sid,generation):
        with self.lock:
            for entry in self.entries.values():
                if (entry['stage']=='queued' and entry['result'].session_id==sid and
                    (entry['attempt'].actor,entry['attempt'].profile)==(scope['actor'],scope['profile']) and scope['hostRef']==self.host_ref):
                    entry.update(stage='polled',boot=scope['bootId'],generation=generation,
                        grant_binding={field:scope[field] for field in ('hostRef','adapterId','profile','actor','installationRef','accountBindingRef','bootId')})
                    # This is the uncertainty boundary, not an execution proof.
                    return copy.deepcopy(entry['job'])
        return None

    def event(self,scope,generation,body):
        key=(body['operationId'],body['attemptId'])
        with self.lock:
            entry=self.entries.get(key)
            if (not entry or entry['boot']!=scope['bootId'] or body.get('bootId')!=scope['bootId'] or entry['generation']!=generation or
                any(scope.get(field)!=value for field,value in entry.get('grant_binding',{}).items()) or
                (entry['attempt'].actor,entry['attempt'].profile)!=(scope['actor'],scope['profile']) or
                scope['hostRef']!=self.host_ref or any(body.get(field)!=entry['job'][field]
                    for field in ('sessionId','scopeDigest','inputSha256','hostRef','actor','profile'))):
                raise RuntimeFault('identity_conflict','Readonly event changed the original SDK/native scope')
            reference=body.get('reference')
            if not isinstance(reference,str) or not reference.startswith('claude-completion:'):
                raise RuntimeFault('invalid_input','Use an API invocation receipt, not a chat turn identifier')
            if entry['reference'] not in (None,reference):raise RuntimeFault('identity_conflict','Original API reference changed')
            kind=body['kind'];before=entry['result'];callback=entry['callback']
        if kind=='observation_reference':
            with self.lock:
                if entry['reference']==reference:
                    return {'requestId':reference,'sessionId':before.session_id}
                if entry['stage']!='polled':raise RuntimeFault('identity_conflict','Preserve the original observation stage')
            result=replace(before,request_id=reference)
            ack=callback(SubmissionEvent(key[1]+':'+reference,'session',result))
            if not isinstance(ack,BindingAck) or (ack.session_id,ack.request_id)!=(result.session_id,reference):
                raise RuntimeFault('not_ready','SDK must acknowledge this API invocation before it starts')
            with self.lock:entry.update(reference=reference,result=result,stage='bound')
            return {'requestId':reference,'sessionId':result.session_id}
        with self.lock:
            if entry['reference']!=reference:raise RuntimeFault('not_ready','Bind the original API invocation first')
            if kind=='observation_dispatch':
                if entry['stage']!='bound':raise RuntimeFault('unknown_result','Do not dispatch this invocation again')
                entry.update(stage='dispatched',dispatched=True)
                return {'recorded':True}
            if kind=='observation_unknown':
                if entry['stage']=='returned':return {'recorded':True}
                entry.update(stage='unknown',result=replace(before,state='unknown'))
                self.changed.notify_all()
                return {'recorded':True}
            if kind!='observation_return' or not entry['dispatched'] or body.get('state') not in ('completed','failed'):
                raise RuntimeFault('identity_conflict','A real API closure return is required')
            if entry['stage']=='returned':
                if before.state!=body['state'] or before.output!=body.get('output'):
                    raise RuntimeFault('identity_conflict','Original readonly terminal changed')
                return {'recorded':True}
            state=body['state']
            if state=='completed' and (body.get('reason')!='answered' or not isinstance(body.get('output'),str)):
                raise RuntimeFault('invalid_input','Use the actual readonly API answer')
            if state=='failed' and body.get('reason') not in ('api-error','empty-reply'):
                raise RuntimeFault('invalid_input','Use a recognized readonly API failure return')
            terminal=MachineTerminal(key[0],key[1],before.session_id,reference,state,'native-api:'+reference)
            result=replace(before,state=state,output=body.get('output'),terminal=terminal)
        # Actual private return persistence precedes SDK terminal/possible sealing.
        # This readonly leaf has no output file to collect; business collectors
        # must perform their real collection attempt between these two actions.
        self.persist({'scope':entry['grant_binding'],
            'nativeGeneration':generation,'inputSha256':body['inputSha256'],'result':asdict(result)})
        callback(SubmissionEvent(key[1]+':returned:'+reference,'terminal',result))
        with self.changed:
            entry.update(result=result,stage='returned');self.changed.notify_all()
        return {'recorded':True}
