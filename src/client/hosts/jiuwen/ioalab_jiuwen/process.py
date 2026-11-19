"""Scoped host rounds over public Jiuwen interfaces; no business rules here."""
import asyncio
import copy
from datetime import datetime,timezone
from pathlib import Path
import os
import re
import threading
import uuid
from types import SimpleNamespace

from jiuwenswarm_sdk import Client
from ioalab_sdk.runtime import private_json
from .process_values import _Result
from .submission import Submission
from ioalab_sdk.runtime import RuntimeFault,encode,validate_output


class ProcessHost(Submission):
    def __init__(self,command,data_dir,*,model=None,environment=None,skill_directories=None,resource_factory=None):
        if not isinstance(command,(list,tuple)) or not command or any(not isinstance(item,str) or not item for item in command):
            raise RuntimeFault('invalid_input','Configure the original Jiuwen process command as an argv list')
        self.command=tuple(command)
        self.data_dir=Path(data_dir).resolve()
        self.model=model
        self.resource_factory=resource_factory
        self.environment={**os.environ,**(environment or {}),'PYTHONUTF8':'1',
                          'JIUWENSWARM_DATA_DIR':str(self.data_dir),'JIUWENSWARM_CONFIG_DIR':str(self.data_dir/'config')}
        if skill_directories is not None and not isinstance(skill_directories,dict):raise RuntimeFault('invalid_input','Business skill sources must be a name-to-directory mapping')
        self.skill_directories=dict(skill_directories or {})
        if len(self.skill_directories)>64 or any(not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',name) or not isinstance(path,(str,Path)) or not Path(path).expanduser().is_absolute() for name,path in self.skill_directories.items()) or len({name.casefold() for name in self.skill_directories})!=len(self.skill_directories):
            raise RuntimeFault('invalid_input','Configure exact business skill names and absolute local source directories')
        self.skill_directories={name:Path(path).expanduser() for name,path in self.skill_directories.items()}
        self._policy={};self._submissions={};self._request_states={}
        self._running={};self._lock=threading.RLock()

    @classmethod
    def from_config(cls,value,*,base_dir):
        allowed={'command','data_dir','model','skill_directories'}
        if not isinstance(value,dict) or set(value)-allowed or not {'command','data_dir'}.issubset(value):
            raise RuntimeFault('invalid_input','Jiuwen host needs command and data_dir; model and explicit skill_directories are optional')
        if not isinstance(value['data_dir'],str) or not value['data_dir'].strip():
            raise RuntimeFault('invalid_input','Configure the existing Jiuwen data directory')
        if value.get('model') is not None and (not isinstance(value['model'],str) or not 1<=len(value['model'])<=256):
            raise RuntimeFault('invalid_input','Use an existing Jiuwen model selection key')
        data=Path(value['data_dir']).expanduser()
        if not data.is_absolute():data=Path(base_dir)/data
        return cls(value['command'],data,model=value.get('model'),skill_directories=value.get('skill_directories'))

    def _client(self,*,cwd=None):
        return Client(command=self.command,cwd=str(cwd or self.data_dir),env=self.environment)

    def _execute(self,turn):
        originals=[tool.name for tool in turn.definition.tools]
        if len(set(originals))!=len(originals) or any(not isinstance(name,str) or not 1<=len(name)<=128 for name in originals):
            raise RuntimeFault('invalid_input','Local tool names must be distinct bounded strings')
        native=dict(turn.definition.native_tools)
        if len(native)!=len(turn.definition.native_tools) or len(native)>64 or any(not isinstance(name,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',name) or name in originals or name in ('ask_user','skill_tool','search_skill','install_skill','uninstall_skill') or level not in ('allow','ask','deny') for name,level in native.items()):
            raise RuntimeFault('invalid_input','Native tools require distinct exact owner-configured names and permissions')
        aliases={};declarations=[];metadata=set()
        reserved=set(originals)|set(native)|{'ask_user'}
        for tool in turn.definition.tools:
            name=tool.name
            if name=='ask_user' or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',name):
                base=('ioa_'+re.sub('[^A-Za-z0-9_]+','_',tool.name).strip('_'))[:50]
                name=base
                while name in reserved:name=base+'_'+uuid.uuid5(uuid.NAMESPACE_URL,name+tool.name).hex[:10]
                reserved.add(name)
            aliases[name]=tool.name
            schema=copy.deepcopy(tool.input_schema)
            if 'call_goal' not in schema.get('properties',{}):
                schema.setdefault('properties',{})['call_goal']={'type':'string','maxLength':2048,'description':'原版宿主用于展示的调用目的；不参与业务输入或权限。'}
                metadata.add(name)
            description=tool.description if name==tool.name else '[Local tool '+tool.name+'] '+tool.description
            if schema.get('properties'):
                description+='\nInput structure (JSON Schema; keep each declared nesting level exactly): '+encode(schema)
            if tool.output_schema is not None:
                description+='\nOutput contract (JSON Schema; preserve the actual result): '+encode(tool.output_schema)
            # The original public HostTool accepts only these three fields.
            declarations.append({'input_schema':schema,'name':name,'description':description})
        names=list(aliases)
        # Keep the original native Agent definition stable across phases.
        # The per-turn schema is data, not a change to the native tool catalog.
        proposal_name='ioa_propose_result';proposal=[None]
        while proposal_name in reserved:proposal_name='ioa_propose_result_'+uuid.uuid5(uuid.NAMESPACE_URL,proposal_name).hex[:10]
        declarations.append({'name':proposal_name,
            'description':'可选结构化提案，只收集数据，不执行业务。已有SDK业务工具回执时无需调用；不要重复提交已完成的效果。',
            'input_schema':{'type':'object','properties':{'value':{'type':'object'},
                'call_goal':{'type':'string'}},'required':['value'],'additionalProperties':False}})
        names.append(proposal_name)
        workspace=Path(turn.workspace)
        workspace.mkdir(parents=True,exist_ok=True)
        request_id=turn.planned_request_id
        mapping={original:name for name,original in aliases.items() if name!=original}
        instructions=turn.definition.instructions+('\nLocal tool aliases: '+str(mapping) if mapping else '')
        definition={'name':turn.definition.name,'instructions':instructions,
                    'tools':['*']}
        # '*' inherits the Runtime's configured native tools. Only our SDK
        # callback aliases receive a local grant; native permissions stay owned
        # by the user's existing Runtime configuration in every phase.
        permissions={name:'allow' for name in names}
        permissions[proposal_name]='allow' if turn.proposal_schema is not None else 'deny'
        request={'mode':'agent.work.normal','request_id':request_id,'input':turn.input,'agent':definition,
                 'model':self.model,'host_tools':declarations,
                 'permissions':{'tools':permissions},
                 'workspace':{'cwd':str(workspace),'project_dir':str(workspace)},
                 'timeout_seconds':turn.timeout_seconds}
        if turn.proposal_schema is not None:
            request['input']+='\nOptional proposal schema for this turn (data): '+encode(turn.proposal_schema)+\
                '\n可选结构化结果通过 '+proposal_name+' 提供。如已通过SDK工具完成本轮动作，直接结束并正常总结，无需另行提供提案。'
        if turn.session_id:request['session_id']=turn.session_id
        path=workspace/'runs'/(request_id+'.json')
        record={'operationId':turn.operation_id,'plannedRequestId':request_id,'requestId':None,'sessionId':turn.session_id,'state':'prepared',
                'definitionDigest':turn.definition_digest,
                'nativeToolsEnabled':turn.native_tools_enabled,
                'createdAt':datetime.now(timezone.utc).isoformat()}
        private_json(path,record)
        observed_session=[turn.session_id]
        observed_request=[None]
        unanswered=[False]

        async def execute():
            loop=asyncio.get_running_loop();cancel=asyncio.Event()
            with self._lock:self._running.setdefault(turn.operation_id,[]).append((loop,cancel))
            saved_session=[turn.session_id]
            bound=[False]
            async def session(native_record):
                if native_record.get('request_id')!=request_id:
                    raise RuntimeFault('identity_conflict','Process record does not belong to the original request')
                sid=native_record.get('session_id')
                if not isinstance(sid,str) or not sid:return
                if saved_session[0] and saved_session[0]!=sid:raise RuntimeFault('identity_conflict','Jiuwen changed its original session')
                if not bound[0]:
                    observed_session[0]=sid
                    observed_request[0]=native_record['request_id']
                    private_json(path,{**record,'state':'running','sessionId':sid,'requestId':native_record['request_id']})
                    if turn.on_session:await asyncio.to_thread(turn.on_session,sid,native_record['request_id'])
                    saved_session[0]=sid;bound[0]=True
            async def event(record):
                await session(record)
                if turn.on_event:await asyncio.to_thread(turn.on_event,record)
            async def tool(record):
                await session(record)
                payload=record['payload']
                if not bound[0] or payload['name'] not in names:
                    raise RuntimeFault('forbidden','Host tool call has no matching local execution context')
                arguments=payload['arguments']
                if payload['name']==proposal_name:
                    if turn.proposal_schema is None:
                        return {'status':'rejected','error':{'code':'forbidden','message':'No proposal channel is enabled for this turn'}}
                    candidate=arguments.get('value') if isinstance(arguments,dict) else None
                    try:validate_output(SimpleNamespace(output_schema=turn.proposal_schema),candidate)
                    except RuntimeFault:
                        return {'status':'rejected','error':{'code':'invalid_proposal','recovery':'correct_input','message':'Use the original proposal schema in value'}}
                    if proposal[0] is not None and proposal[0]!=candidate:
                        return {'status':'rejected','error':{'code':'id_conflict','message':'The original proposal cannot change'}}
                    proposal[0]=copy.deepcopy(candidate)
                    return {'status':'recorded'}
                if payload['name'] in metadata and isinstance(arguments,dict):arguments={key:value for key,value in arguments.items() if key!='call_goal'}
                if turn.on_tool is None:raise RuntimeFault('forbidden','No role callback is configured')
                return await asyncio.to_thread(turn.on_tool,aliases[payload['name']],arguments,payload['call_id'])
            async def interaction(record):
                await session(record)
                if turn.on_interaction is None:
                    # S01 carries no trusted user-answer channel: never answer a
                    # native ASK (not even a rejection) on the user's behalf.
                    unanswered[0]=True
                    raise RuntimeFault('not_ready','Real user interaction is required','wait_for_user')
                aborted=threading.Event()
                try:return await asyncio.to_thread(turn.on_interaction,{**record,'_cancelled':aborted.is_set})
                finally:aborted.set()
            try:
                async with asyncio.timeout(turn.timeout_seconds+20 if turn.timeout_seconds is not None else None):
                    result=await self._client(cwd=workspace).run(request,on_event=event,on_tool_call=tool if names else None,
                        on_interaction=interaction,cancel=cancel,deadline_seconds=turn.timeout_seconds+15 if turn.timeout_seconds is not None else None)
                await session(result)
                return result
            finally:
                with self._lock:
                    self._running[turn.operation_id].remove((loop,cancel))
                    if not self._running[turn.operation_id]:self._running.pop(turn.operation_id)
        try:
            result=asyncio.run(execute())
            outcome=_Result(result.get('status','unknown'),observed_session[0],result.get('usage') or {},result.get('error'),request_id,result,proposal[0])
            private_json(path,{**record,'state':outcome.status,'sessionId':outcome.session_id,'requestId':observed_request[0],'usage':outcome.usage,'error':outcome.error})
            return outcome
        except Exception as exc:
            if 'outcome' in locals():
                # The Process SDK already returned its validated machine result
                # and waited for matching process exit. Persistence failure does
                # not erase that fact; the S01 journal records it independently.
                return outcome
            private_json(path,{**record,'state':'unknown','sessionId':observed_session[0],'requestId':observed_request[0],'errorType':type(exc).__name__})
            code='native_interaction_unanswered' if unanswered[0] else 'host_timeout' if isinstance(exc,TimeoutError) else 'host_unknown'
            return _Result('unknown',observed_session[0],{}, {'code':code,'type':type(exc).__name__},request_id)

    def _cancel_operation(self,operation_id):
        with self._lock:running=list(self._running.get(operation_id,()))
        for loop,event in running:loop.call_soon_threadsafe(event.set)
        return bool(running)
