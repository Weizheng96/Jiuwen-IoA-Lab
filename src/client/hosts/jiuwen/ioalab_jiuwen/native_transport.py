"""Private native receipts/transport; the SDK remains the submission authority."""
from dataclasses import asdict, replace
import hashlib
import json
import re
from pathlib import Path
import secrets
import threading
import time

from ioalab_sdk.runtime import (BindingAck, HostToolCall, HostToolResult, MachineTerminal, RuntimeFault,
    SubmissionError, SubmissionEvent, SubmissionResult, encode, private_json)


SOURCE_FIELDS={'sessionId','requestId','userId','channelId','workspace',
               'projectId','projectDir','mode','workMode'}
SCOPE_FIELDS={'hostRef','adapterId','actor','profile','installationRef','accountBindingRef','bootId'}
BINDING_FORMAT='ioalab.jiuwen-binding/1'
# Worker heartbeat window (polls every 0.5 s); also the dispatch liveness bound.
HEARTBEAT_SECONDS=10
PLAN_FORMAT='ioalab.jiuwen-setup-obligation/1'


def _digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()


class NativeTransport:
    def __init__(self,host_ref,root):
        self.host_ref=host_ref;self.root=Path(root)
        self._lock=threading.RLock();self._changed=threading.Condition(self._lock)
        self._started=False;self._started_at=None;self._control=None;self._binding=None;self._setup_plan=None
        self._workers={};self._states={};self._requests={};self._routes={}

    def bind_entry_control(self,control):
        if not callable(control):raise RuntimeFault('invalid_input','Use the SDK trusted setup closure')
        self._control=control

    def start(self):
        with self._lock:
            self._started=False
            if self.root.is_symlink():raise RuntimeFault('forbidden','Native receipt root cannot be a link')
            self.root.mkdir(parents=True,exist_ok=True)
            path=self.root/'binding.json'
            if path.exists():
                if path.is_symlink():raise RuntimeFault('forbidden','Restore the original native binding receipt')
                binding=json.loads(path.read_text(encoding='utf-8'))
                self._validate_binding(binding)
            else:binding=None
            plan=self.root/'setup-obligation.json'
            if plan.exists():
                if plan.is_symlink():raise RuntimeFault('forbidden','Restore the original setup obligation')
                obligation=json.loads(plan.read_text(encoding='utf-8'))
                self._validate_plan(obligation)
            else:obligation=None
            if binding and obligation and any(binding[key]!=obligation[key] for key in
                    ('scope','targetDigest','savedReceiptRef')):
                raise RuntimeFault('identity_conflict','Restore matching original binding and setup receipts')
            self._binding=binding;self._setup_plan=obligation
            self._started=True;self._started_at=time.monotonic()

    def drain(self,timeout):
        end=time.monotonic()+max(0,timeout)
        with self._changed:
            while any(not item['result'].terminal for item in self._states.values()):
                left=end-time.monotonic()
                if left<=0:return False
                self._changed.wait(left)
            return True

    def close(self):
        with self._changed:self._started=False;self._changed.notify_all()

    def _scope(self,scope):
        if (not isinstance(scope,dict) or set(scope)!=SCOPE_FIELDS or scope.get('hostRef')!=self.host_ref or
                scope.get('adapterId')!='jiuwen' or any(not isinstance(v,str) or not 1<=len(v)<=8192
                for v in scope.values())):
            raise RuntimeFault('identity_conflict','Use the authenticated original native registration')

    @staticmethod
    def _receipt_fields(record,keys):
        if any(not isinstance(record.get(k),str) or not 1<=len(record[k])<=8192 for k in keys):
            raise RuntimeFault('identity_conflict','Restore the original bounded native save receipt')
        if not isinstance(record.get('targetDigest'),str) or not re.fullmatch('[0-9a-f]{64}',record['targetDigest']):
            raise RuntimeFault('identity_conflict','Restore the original native target digest')

    def _validate_binding(self,record):
        keys={'format','hostRef','installationRef','accountBindingRef','source','scope',
              'targetDigest','claimRef','savedReceiptRef'}
        if not isinstance(record,dict) or set(record)!=keys or record['format']!=BINDING_FORMAT:
            raise RuntimeFault('identity_conflict','Restore the original declared native binding format')
        self._scope(record['scope']);self._source(record['source'])
        self._receipt_fields(record,('claimRef','savedReceiptRef','hostRef','installationRef','accountBindingRef'))
        if any(record[k]!=record['scope'][k] for k in ('hostRef','installationRef','accountBindingRef')):
            raise RuntimeFault('identity_conflict','Native binding changed its original installation/account/host')
        target={k:record[k] for k in ('hostRef','installationRef','accountBindingRef','source')}
        if _digest(target)!=record['targetDigest']:
            raise RuntimeFault('identity_conflict','Native context differs from the originally claimed target')

    def _validate_plan(self,record):
        keys={'format','scope','entryRef','intentRef','targetDigest','savedReceiptRef'}
        if not isinstance(record,dict) or set(record)!=keys or record['format']!=PLAN_FORMAT:
            raise RuntimeFault('identity_conflict','Restore the original declared setup obligation')
        self._scope(record['scope'])
        self._receipt_fields(record,('entryRef','intentRef','savedReceiptRef'))

    @staticmethod
    def _source(source):
        if (not isinstance(source,dict) or set(source)!=SOURCE_FIELDS or
                any(not isinstance(v,str) or len(v)>8192 for v in source.values()) or
                any(not source[k] for k in ('sessionId','requestId','userId','workspace','projectDir','mode')) or
                source['channelId']!='web' or not Path(source['workspace']).is_absolute() or
                not Path(source['projectDir']).is_absolute() or
                not Path(source['workspace']).resolve().is_relative_to(Path(source['projectDir']).resolve())):
            raise RuntimeFault('identity_conflict','Use actual native Web route and absolute workspace facts')

    def _setup(self,scope,entry_ref,source):
        if self._control is None:raise RuntimeFault('not_ready','SDK setup authority is not attached')
        if self._binding is not None:
            old=self._binding
            self._validate_binding(old)
            if (old['scope']['installationRef']!=scope['installationRef'] or
                    old['scope']['accountBindingRef']!=scope['accountBindingRef'] or
                    old['source']['userId']!=source['userId'] or old['source']['channelId']!=source['channelId'] or
                    old['source']['workspace']!=source['workspace']):
                raise RuntimeFault('identity_conflict','Preserve the existing native owner and installation binding')
            # Recovery acknowledges the originally saved receipt. It does not
            # claim a new intent, save a different target, or refresh permission.
            completed=self._control(scope,{'purpose':'sdk.entry','action':'complete',
                'claimRef':old['claimRef'],'targetDigest':old['targetDigest'],
                'savedReceiptRef':old['savedReceiptRef']})
            return completed.get('phase')=='bound'
        target={'hostRef':self.host_ref,'installationRef':scope['installationRef'],
            'accountBindingRef':scope['accountBindingRef'],'source':source}
        digest=_digest(target)
        plan=self._setup_plan
        if plan is None:
            pending=self._control(scope,{'purpose':'sdk.entry','action':'pending','entryRef':entry_ref})
            if pending is None:return False
            # This is an obligation journal, not saved/active native context.
            # Retain the original intent before a potentially lost claim ACK.
            plan={'format':PLAN_FORMAT,'scope':dict(scope),'entryRef':entry_ref,'intentRef':pending['intentRef'],
                  'targetDigest':digest,'savedReceiptRef':secrets.token_urlsafe(32)}
            private_json(self.root/'setup-obligation.json',plan);self._setup_plan=plan
        self._validate_plan(plan)
        if plan['scope']!=scope or plan['entryRef']!=entry_ref or plan['targetDigest']!=digest:
            raise RuntimeFault('identity_conflict','Recover the original setup obligation before another source')
        claim=self._control(scope,{'purpose':'sdk.entry','action':'claim','entryRef':plan['entryRef'],
            'intentRef':plan['intentRef'],'targetDigest':digest})
        # The SDK claim is obtained BEFORE this first adapter save obligation.
        saved={'format':BINDING_FORMAT,**target,'scope':dict(scope),'targetDigest':digest,'claimRef':claim['claimRef'],
               'savedReceiptRef':plan['savedReceiptRef']}
        self._validate_binding(saved)
        private_json(self.root/'binding.json',saved)
        self._binding=saved
        completed=self._control(scope,{'purpose':'sdk.entry','action':'complete',
            'claimRef':saved['claimRef'],'targetDigest':digest,'savedReceiptRef':saved['savedReceiptRef']})
        return completed.get('phase')=='bound'

    def exchange(self,scope,body):
        self._scope(scope)
        if not isinstance(body,dict) or body.get('purpose')!='jiuwen.native':
            raise RuntimeFault('invalid_input','Use the private native transport envelope')
        action=body.get('action')
        fields={'poll':{'purpose','action','workerId','entryRef','source','features'},
                'report':{'purpose','action','workerId','commandId','report'},
                'sdk_tool':{'purpose','action','workerId','commandId','sessionId','requestId','callId','name','arguments'}}
        if action not in fields or set(body)!=fields[action]:
            raise RuntimeFault('invalid_input','Use a fixed native transport packet')
        with self._changed:
            if not self._started:raise RuntimeFault('closed','Native resource is not accepting work')
            if action=='report':return self._report(scope,body)
            if action=='sdk_tool':return self._sdk_tool(scope,body)
            source=body['source'];self._source(source)
            worker_id=body['workerId']
            if not isinstance(worker_id,str) or not 1<=len(worker_id)<=128:
                raise RuntimeFault('invalid_input','Use a private native worker reference')
            old=self._workers.get(worker_id)
            if old and (old['scope']!=scope or old['source']!=source):
                raise RuntimeFault('identity_conflict','Preserve the original native worker source')
            bound=old['bound'] if old and old['bound'] else self._setup(scope,body['entryRef'],source)
            if not isinstance(body['features'],list) or set(body['features'])-{'request_turn'}:
                raise RuntimeFault('invalid_input','Use the installed native transport feature declaration')
            worker={'scope':dict(scope),'source':dict(source),'seen':time.monotonic(),'bound':bound,'features':body['features']}
            self._workers[worker_id]=worker
            if not bound:return {'bound':False,'command':None}
            route=(scope['actor'],scope['profile'])
            active=self._routes.get(route)
            if active and active!=worker_id and time.monotonic()-self._workers[active]['seen']<HEARTBEAT_SECONDS:
                # Fresh captures still issue their own SDK entryRef. They do not
                # create an unbounded set of idle background pollers or replace
                # the original worker already owning this profile's requests.
                self._workers.pop(worker_id,None)
                return {'bound':True,'command':None,'retire':True}
            self._routes[route]=worker_id
            for state in self._states.values():
                if state['workerId']==worker_id and state['phase']=='queued':
                    # Persist uncertainty before releasing a command. A lost
                    # HTTP response is never permission to dispatch it again.
                    state['phase']='released';self._persist(state)
                    return {'bound':True,'command':dict(state['command'])}
            return {'bound':True,'command':None}

    def _persist(self,state):
        record={k:v for k,v in state.items() if k not in
            ('path','on_event','on_tool','result')}|{'result':asdict(state['result'])}
        private_json(state['path'],record)
        result=state['result']
        if result.request_id is not None:
            # Derived original-request lookup, not a second dispatch/effect
            # authority. A crash before this write leaves query unknown.
            private_json(self.root/('known-request-'+_digest(result.request_id)+'.json'),{
                'format':'ioalab.jiuwen-request/1','operation':state['operation'],
                'submissionAttempt':state['submissionAttempt'],'nativeScope':state['scope'],
                'sessionId':result.session_id,'requestId':result.request_id,
                'record':state['path'].name,'recordDigest':_digest(record)})

    @staticmethod
    def _result(value):
        if isinstance(value,SubmissionResult):return value
        value=dict(value)
        if value.get('terminal'):value['terminal']=MachineTerminal(**value['terminal'])
        if value.get('error'):value['error']=SubmissionError(**value['error'])
        if value.get('resources'):raise RuntimeFault('identity_conflict','Native request journals carry no resources')
        return SubmissionResult(**{**value,'resources':()})

    @staticmethod
    def _undispatched(state):
        # Exact zero-dispatch proof from the worker before send_session_message.
        result=state['result']
        return (state.get('phase')=='error' and result.error is not None and result.error.dispatch=='not_submitted' and
                result.request_id is None and result.terminal is None and not state.get('toolEntered'))

    def submit(self,operation,input,session_id,on_event,execution=None,on_tool=None):
        attempt=input.attempt
        initial=SubmissionResult(operation,attempt.nonce,'unknown',session_id)
        # The SDK issues a new attempt nonce only after proven zero dispatch;
        # the original operation scope, not the nonce, owns the journal.
        fingerprint=_digest({'input':input.text,'sessionId':session_id,'scope':attempt.scope_digest,
                             'actor':attempt.actor,'profile':attempt.profile,
                             'execution':asdict(execution) if execution else None})
        path=self.root/('request-'+_digest(operation)+'.json')
        with self._changed:
            old=self._states.get(operation)
            if old is None and path.exists():
                if path.is_symlink():raise RuntimeFault('forbidden','Restore the original native request journal')
                # Durable journal is an original obligation, not a fresh queue.
                saved=json.loads(path.read_text(encoding='utf-8'))
                old={**saved,'result':self._result(saved['result'])}
            pinned=None
            if old:
                if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Preserve the original native submission')
                if old.get('attempt')==attempt.nonce:return old['result']
                if not self._undispatched(old):
                    raise RuntimeFault('id_conflict','Recover the original native attempt; it was not proven undispatched')
                # A Session created before the proven preflight failure is the
                # original business Session: reuse it, never create another.
                pinned=old.get('createdSessionId')
            workers=[(key,w) for key,w in self._workers.items() if w['bound'] and key in self._routes.values() and
                (w['scope']['actor'],w['scope']['profile'])==(attempt.actor,attempt.profile) and
                time.monotonic()-w['seen']<HEARTBEAT_SECONDS]
            if not self._started or not workers:
                return replace(initial,error=SubmissionError('not_ready','native_transport','not_submitted',
                    'Connect the explicitly bound native entry before submission'))
            worker_id,worker=workers[-1]
            target=session_id or pinned
            if execution and ('request_turn' not in worker['features'] or target==worker['source']['sessionId']):
                return replace(initial,error=SubmissionError('not_ready','native_definition','not_submitted',
                    'Prepare the isolated native business Agent before dispatch; original user chat is notice-only'))
            command_id=secrets.token_urlsafe(24)
            command={'commandId':command_id,'operationId':operation,'plannedRequestId':'ioa-'+secrets.token_hex(16),
                     'sessionId':target,'text':input.text,'execution':asdict(execution) if execution else None}
            state={'operation':operation,'fingerprint':fingerprint,'attempt':attempt.nonce,'path':path,'workerId':worker_id,
                'submissionAttempt':asdict(attempt),
                'scope':worker['scope'],'source':worker['source'],'phase':'queued','command':command,
                'result':initial,'on_event':on_event,'on_tool':on_tool,'bound':False}
            if pinned:state['createdSessionId']=pinned
            self._states[operation]=state;self._persist(state)
            while self._started and not state['result'].terminal:
                if state['phase']=='error' or state['result'].state=='waiting':break
                if time.monotonic()-self._workers[worker_id]['seen']>=HEARTBEAT_SECONDS:
                    state['result']=replace(state['result'],error=SubmissionError('native_transport_unknown',
                        'native_transport','unknown','Recover the original native request; do not submit again'))
                    self._persist(state);break
                self._changed.wait(0.5)
            return state['result']

    def _sdk_tool(self,scope,body):
        state=next((s for s in self._states.values() if s['command']['commandId']==body['commandId']),None)
        if (state is None or state['workerId']!=body['workerId'] or state['scope']!=scope or
                not state['bound'] or state['result'].terminal is not None or
                (body['sessionId'],body['requestId'])!=(state['result'].session_id,state['result'].request_id)):
            raise RuntimeFault('forbidden','Exact original native request and P28 acknowledgement required')
        if not callable(state.get('on_tool')):raise RuntimeFault('forbidden','No SDK callback belongs to this native request')
        call=HostToolCall(state['operation'],state['result'].attempt_id,body['sessionId'],
            body['requestId'],body['callId'],body['name'],body['arguments'])
        callback=state['on_tool']
        # Entering an SDK callback ends any zero-dispatch claim for this attempt.
        state['toolEntered']=True;self._persist(state)
        # Protected browser/SDK callbacks can wait. Do not block the original
        # native snapshot/poll/terminal lane behind that wait.
        self._changed.release()
        try:result=callback(call)
        finally:self._changed.acquire()
        if not isinstance(result,HostToolResult):raise RuntimeFault('invalid_tool_output','Use the original SDK callback receipt')
        if result.resources:
            # The SDK effect/receipt already exists. Keep its actual value and
            # report collection; never rerun the callback to collect files.
            error=SubmissionError('not_ready','callback_resources','accepted',
                'Original SDK callback receipt retained; native resource transport is not available','recover_original_resource')
            state['collectionError']=asdict(error)
            state['result']=replace(state['result'],error=error);self._persist(state)
            return {'value':result.value,'error':asdict(error)}
        return {'value':result.value,'error':asdict(result.error) if result.error else None}

    def _report(self,scope,body):
        state=next((s for s in self._states.values() if s['command']['commandId']==body['commandId']),None)
        if state is None or state['workerId']!=body['workerId'] or state['scope']!=scope:
            raise RuntimeFault('identity_conflict','Report only the original native command')
        report=body['report']
        if not isinstance(report,dict):raise RuntimeFault('invalid_input','Use the original native receipt')
        self._workers[body['workerId']]['seen']=time.monotonic()
        if report.get('stage')=='created':
            if set(report)!={'stage','sessionId'} or state['command']['sessionId'] is not None:
                raise RuntimeFault('identity_conflict','Keep the original new-session creation receipt')
            sid=report['sessionId']
            if not isinstance(sid,str) or not sid:raise RuntimeFault('invalid_input','Use the actual created native Session')
            if state.get('createdSessionId') not in (None,sid):raise RuntimeFault('identity_conflict','Do not replace a created Session')
            state['createdSessionId']=sid;self._persist(state);return {'ack':True}
        if report.get('stage') in ('error','preflight_error','bridge_error'):
            if state['result'].terminal:return {'ack':True}
            state['phase']='error'
            dispatch='not_submitted' if report['stage']=='preflight_error' and state['result'].request_id is None else 'unknown'
            state['result']=replace(state['result'],error=SubmissionError('native_unknown','native_request',dispatch,
                'Original native operation could not be proved; recover its saved receipt'))
            self._persist(state);self._changed.notify_all();return {'ack':True}
        if set(report) not in ({'stage','snapshot'},{'stage','snapshot','guardError'}) or report['stage']!='snapshot':
            raise RuntimeFault('invalid_input','Use the real native execution snapshot')
        snapshot=report['snapshot'];command=state['command']
        expected_sid=command['sessionId'] or state.get('createdSessionId')
        if (not isinstance(snapshot,dict) or snapshot.get('request_id')!=command['plannedRequestId'] or
                not expected_sid or snapshot.get('session_id')!=expected_sid or snapshot.get('channel_id')!='web' or
                snapshot.get('work_kind')!='session_message' or not snapshot.get('execution_id') or
                type(snapshot.get('generation')) is not int):
            raise RuntimeFault('identity_conflict','Require the original actual Session/request/execution identity')
        identity={k:snapshot[k] for k in ('session_id','request_id','execution_id','generation')}
        if state.get('nativeIdentity') not in (None,identity):raise RuntimeFault('identity_conflict','Do not substitute another native execution')
        state['nativeIdentity']=identity
        guard_error=report.get('guardError')
        if guard_error is not None:
            if not isinstance(guard_error,str) or not re.fullmatch('[a-z][a-z0-9_]{0,127}',guard_error):
                raise RuntimeFault('invalid_input','Use the bounded original native guard failure code')
            if state.get('guardError') not in (None,guard_error):
                raise RuntimeFault('identity_conflict','Preserve the original native guard failure')
            state['guardError']=guard_error
        mapping={'queued':'accepted','running':'running','waiting_for_control':'waiting',
                 'waiting_for_input':'waiting','succeeded':'completed','failed':'failed','cancelled':'cancelled'}
        status=mapping.get(snapshot.get('state'))
        if status is None:raise RuntimeFault('invalid_input','Unknown native machine state')
        if state['result'].terminal is not None:
            if status in ('completed','failed','cancelled') and status!=state['result'].terminal.state:
                raise RuntimeFault('identity_conflict','Preserve the original native machine terminal')
            return {'ack':True,'bindingAck':state['bound']}
        terminal=None
        if status in ('completed','failed','cancelled'):
            if snapshot.get('finished_at') is None:raise RuntimeFault('identity_conflict','Terminal requires the real worker finish fact')
            terminal=MachineTerminal(state['operation'],state['result'].attempt_id,expected_sid,
                snapshot['request_id'],status,str(state['path']))
        state['snapshot']=dict(snapshot)
        error=(SubmissionError('native_guard_failed','native_definition','accepted',
            'Original native request ended without its required frozen execution guard: '+state['guardError'])
            if state.get('guardError') else SubmissionError(**state['collectionError'])
            if state.get('collectionError') else state['result'].error)
        state['result']=replace(state['result'],session_id=expected_sid,request_id=snapshot['request_id'],
            state=status,delivery='handled' if terminal else 'accepted',terminal=terminal,
            error=error)
        self._requests[snapshot['request_id']]=state
        # Save real machine evidence before calling the SDK, which can fail.
        self._persist(state)
        callback=state.get('on_event')
        try:
            if not state['bound']:
                ack=callback(SubmissionEvent(state['result'].attempt_id+':session','session',state['result'])) if callback else None
                if not isinstance(ack,BindingAck) or (ack.session_id,ack.request_id)!=(expected_sid,snapshot['request_id']):
                    raise RuntimeFault('not_ready','Persist the exact native Session/request before SDK callbacks')
                state['bound']=True
            if terminal and callback:callback(SubmissionEvent(state['result'].attempt_id+':terminal','terminal',state['result']))
        except Exception:
            state['reportingError']='event_reporting_unknown'
            if state.get('guardError'):
                state['result']=replace(state['result'],error=replace(state['result'].error,
                    message=state['result'].error.message+'; SDK event reporting is unknown'))
            else:
                state['result']=replace(state['result'],error=SubmissionError('event_reporting_unknown','event','accepted',
                    'Original native identity and machine evidence retained; inspect SDK acknowledgement'))
        self._persist(state);self._changed.notify_all()
        return {'ack':True,'bindingAck':state['bound']}

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
            state=self._requests.get(request_id)
            if state:return state['result']
            if not self._started or not isinstance(request_id,str) or not 1<=len(request_id)<=8192:return None
            path=self.root/('known-request-'+_digest(request_id)+'.json')
            try:
                if path.is_symlink():return None
                index=json.loads(path.read_text(encoding='utf-8'))
                if (not isinstance(index,dict) or set(index)!={'format','operation','submissionAttempt','nativeScope',
                        'sessionId','requestId','record','recordDigest'} or index['format']!='ioalab.jiuwen-request/1' or
                        index['requestId']!=request_id or not isinstance(index['record'],str) or
                        not re.fullmatch('request-[0-9a-f]{64}\\.json',index['record'])):return None
                self._scope(index['nativeScope'])
                if self._binding is None or any(index['nativeScope'][k]!=self._binding['scope'][k]
                        for k in ('hostRef','installationRef','accountBindingRef')):return None
                receipt=self.root/index['record']
                if receipt.is_symlink():return None
                record=json.loads(receipt.read_text(encoding='utf-8'))
                if _digest(record)!=index['recordDigest']:return None
                claim=record['submissionAttempt'];result=self._result(record['result']);identity=record['nativeIdentity']
                if (set(claim)!={'nonce','scope_digest','actor','profile'} or
                        any(not isinstance(v,str) or not 1<=len(v)<=8192 for v in claim.values()) or
                        claim!=index['submissionAttempt'] or record['scope']!=index['nativeScope'] or
                        (claim['actor'],claim['profile'])!=(record['scope']['actor'],record['scope']['profile']) or
                        record['operation']!=index['operation'] or result.operation_id!=index['operation'] or
                        result.attempt_id!=claim['nonce'] or record['attempt']!=claim['nonce'] or
                        result.request_id!=request_id or identity['request_id']!=request_id or
                        record['command']['plannedRequestId']!=request_id or
                        result.session_id!=index['sessionId'] or identity['session_id']!=result.session_id):return None
                if result.terminal and (result.terminal.operation_id,result.terminal.attempt_id,
                        result.terminal.session_id,result.terminal.request_id,result.terminal.state)!=(
                        result.operation_id,result.attempt_id,result.session_id,request_id,result.state):return None
                if result.terminal is None:
                    # An on-disk running/queued snapshot is historical, not a
                    # fresh liveness observation after this adapter restarted.
                    return replace(result,state='unknown',error=result.error or SubmissionError(
                        'native_status_unknown','request_receipt','unknown',
                        'Original request retained; no fresh native terminal observation is available'))
                return result
            except (OSError,ValueError,TypeError,KeyError,RuntimeFault):
                # Missing/corrupt original receipt is unknown, never permission
                # to create a request or dispatch/collect a model result.
                return None

    def check(self):
        """P34/S02 for the resident native host: worker heartbeat only, no model or Session probe.

        A worker exists only after an IoA entry call in a native Session and is
        closed by Rail uninit, so plugin not loaded, reloaded without a new
        entry call and Jiuwen not running all look like no heartbeat here. None
        of these proves the host stopped, so they stay unknown (not counted);
        unavailable needs confirmed host-not-running evidence, which this
        transport does not hold.
        """
        with self._lock:
            if not self._started:return {'state':'unknown','basis':'Native transport not accepting; heartbeat unreadable'}
            now=time.monotonic()
            if any(w['bound'] and now-w['seen']<HEARTBEAT_SECONDS for w in self._workers.values()):
                return {'state':'available','basis':'Bound native worker heartbeat within window; no model probe'}
            if now-self._started_at<HEARTBEAT_SECONDS:
                return {'state':'unknown','basis':'No native worker heartbeat yet since transport start'}
            if any(w['bound'] for w in self._workers.values()):
                return {'state':'unknown','basis':'Bound native worker heartbeat older than window; host stop unconfirmed'}
            if any(now-w['seen']<HEARTBEAT_SECONDS for w in self._workers.values()):
                return {'state':'unknown','basis':'Native worker polling without an established entry binding'}
            return {'state':'unknown','basis':'No native worker heartbeat; plugin not loaded or not re-entered, host stop unconfirmed'}
