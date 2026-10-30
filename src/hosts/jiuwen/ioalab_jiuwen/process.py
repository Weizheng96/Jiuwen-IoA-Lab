"""Scoped host rounds over public Jiuwen interfaces; no business rules here."""
import asyncio
from datetime import datetime,timezone
from pathlib import Path
import os
import re
import threading
import uuid

from a2c.codec import digest
from jiuwenswarm_sdk import Client
from ioalab_sdk.runtime.files import private_json
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.protocol import RuntimeFault


class ProcessHost:
    def __init__(self,command,data_dir,*,model=None,environment=None,notifier=None):
        if not isinstance(command,(list,tuple)) or not command or any(not isinstance(item,str) or not item for item in command):
            raise RuntimeFault('invalid_input','Configure the original Jiuwen process command as an argv list')
        self.command=tuple(command)
        self.data_dir=Path(data_dir).resolve()
        self.model=model
        self.environment={**os.environ,**(environment or {}),'PYTHONUTF8':'1',
                          'JIUWENSWARM_DATA_DIR':str(self.data_dir),'JIUWENSWARM_CONFIG_DIR':str(self.data_dir/'config')}
        self.notifier=notifier
        self._capabilities={'available':False,'reason':'Host capability probe has not completed'}
        self._running={};self._lock=threading.RLock()

    @classmethod
    def from_config(cls,value,*,base_dir):
        allowed={'command','data_dir','model'}
        if not isinstance(value,dict) or set(value)-allowed or not {'command','data_dir'}.issubset(value):
            raise RuntimeFault('invalid_input','Jiuwen host needs command and data_dir; model is optional')
        data=Path(value['data_dir']).expanduser()
        if not data.is_absolute():data=Path(base_dir)/data
        return cls(value['command'],data,model=value.get('model'))

    def _client(self,*,cwd=None):
        return Client(command=self.command,cwd=str(cwd or self.data_dir),env=self.environment)

    def probe(self):
        async def execute():
            client=self._client()
            capabilities=await client.query('protocol.capabilities',deadline_seconds=30)
            models=await client.query('model.list',deadline_seconds=45)
            return capabilities,models
        try:capabilities,models=asyncio.run(execute())
        except Exception as exc:
            self._capabilities={'available':False,'reason':'Jiuwen capability query failed','errorType':type(exc).__name__}
            return self.capabilities()
        caps=capabilities.get('data') or {};catalog=models.get('data') or {}
        choices={model.get('selection_key') for model in catalog.get('models',[]) if model.get('selection_key')}
        selected=self.model or catalog.get('current_selection')
        required=('custom_agent','host_tools','session_resume','interaction_callback')
        available=capabilities.get('status')=='completed' and models.get('status')=='completed' and selected in choices and all(caps.get('features',{}).get(key) for key in required)
        self.model=selected
        self._capabilities={'available':bool(available),'model':selected,'protocol':caps.get('schema_version'),
                            'features':caps.get('features',{}),'notify':self.notifier is not None,
                            'reason':None if available else 'Configure a supported original Jiuwen runtime and model selection'}
        return self.capabilities()

    def capabilities(self):return dict(self._capabilities)

    def run(self,turn):
        if not self._capabilities['available']:raise RuntimeFault('not_ready','The original Jiuwen host has not passed capability/model checks','configure_host')
        originals=[tool.name for tool in turn.definition.tools]
        if len(set(originals))!=len(originals) or any(not isinstance(name,str) or not 1<=len(name)<=128 for name in originals):
            raise RuntimeFault('invalid_input','Local tool names must be distinct bounded strings')
        aliases={};declarations=[]
        reserved=set(originals)|{'ask_user'}
        for tool in turn.definition.tools:
            name=tool.name
            if name=='ask_user' or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',name):
                name='ioa_tool_'+uuid.uuid5(uuid.NAMESPACE_URL,tool.name).hex
                while name in reserved:name='ioa_tool_'+uuid.uuid5(uuid.NAMESPACE_URL,name).hex
                reserved.add(name)
            aliases[name]=tool.name
            declarations.append({**tool.declaration(),'name':name,'description':tool.description if name==tool.name else '[Local tool '+tool.name+'] '+tool.description})
        names=list(aliases)
        workspace=Path(turn.workspace)
        workspace.mkdir(parents=True,exist_ok=True)
        request_id='ioa-'+uuid.uuid4().hex
        mapping={original:name for name,original in aliases.items() if name!=original}
        instructions=turn.definition.instructions+('\nLocal tool aliases: '+str(mapping) if mapping else '')
        definition={'name':turn.definition.name,'instructions':instructions,
                    'tools':names+(['ask_user'] if turn.on_interaction else [])}
        request={'mode':'agent.work.normal','request_id':request_id,'input':turn.input,'agent':definition,
                 'model':self.model,'host_tools':declarations,
                 'workspace':{'cwd':str(workspace),'project_dir':str(workspace)},
                 'timeout_seconds':turn.timeout_seconds,'max_turns':16}
        if turn.definition.skills:request['skills']=list(turn.definition.skills)
        if turn.session_id:request['session_id']=turn.session_id
        path=workspace/'runs'/(request_id+'.json')
        record={'operationId':turn.operation_id,'requestId':request_id,'sessionId':turn.session_id,'state':'prepared',
                'definitionDigest':digest({'agent':definition,'skills':list(turn.definition.skills),
                                          'tools':[tool.policy() for tool in turn.definition.tools]}),
                'createdAt':datetime.now(timezone.utc).isoformat()}
        private_json(path,record)
        observed_session=[turn.session_id]

        async def execute():
            loop=asyncio.get_running_loop();cancel=asyncio.Event()
            with self._lock:self._running.setdefault(turn.operation_id,[]).append((loop,cancel))
            saved_session=[turn.session_id]
            bound=[False]
            async def session(record):
                sid=record.get('session_id')
                if not isinstance(sid,str) or not sid:return
                if saved_session[0] and saved_session[0]!=sid:raise RuntimeFault('identity_conflict','Jiuwen changed its original session')
                if not bound[0]:
                    observed_session[0]=sid
                    private_json(path,{**record,'state':'running','sessionId':sid})
                    if turn.on_session:await asyncio.to_thread(turn.on_session,sid)
                    saved_session[0]=sid;bound[0]=True
            async def event(record):
                await session(record)
                if turn.on_event:await asyncio.to_thread(turn.on_event,record)
            async def tool(record):
                await session(record)
                payload=record['payload']
                if not bound[0] or payload['name'] not in names or turn.on_tool is None:
                    raise RuntimeFault('forbidden','Host tool call has no matching local execution context')
                return await asyncio.to_thread(turn.on_tool,aliases[payload['name']],payload['arguments'],payload['call_id'])
            async def interaction(record):
                await session(record)
                if turn.on_interaction is None:raise RuntimeFault('not_ready','Real user interaction is required','wait_for_user')
                aborted=threading.Event()
                try:return await asyncio.to_thread(turn.on_interaction,{**record,'_cancelled':aborted.is_set})
                finally:aborted.set()
            try:
                result=await self._client(cwd=workspace).run(request,on_event=event,on_tool_call=tool if names else None,
                    on_interaction=interaction,cancel=cancel,deadline_seconds=turn.timeout_seconds+15)
                await session(result)
                return result
            finally:
                with self._lock:
                    self._running[turn.operation_id].remove((loop,cancel))
                    if not self._running[turn.operation_id]:self._running.pop(turn.operation_id)
        try:
            result=asyncio.run(execute())
            outcome=HostResult(result.get('status','unknown'),observed_session[0],result.get('usage') or {},result.get('error'))
            private_json(path,{**record,'state':outcome.status,'sessionId':outcome.session_id,'usage':outcome.usage,'error':outcome.error})
            return outcome
        except Exception as exc:
            private_json(path,{**record,'state':'unknown','sessionId':observed_session[0],'errorType':type(exc).__name__})
            return HostResult('unknown',observed_session[0],{}, {'code':'host_unknown','type':type(exc).__name__})

    def cancel(self,operation_id):
        with self._lock:running=list(self._running.get(operation_id,()))
        for loop,event in running:loop.call_soon_threadsafe(event.set)
        return bool(running)

    def notify(self,message):
        if not self.notifier:return {'status':'not_ready','reason':'A public web notification bridge is not configured'}
        return self.notifier(message)
