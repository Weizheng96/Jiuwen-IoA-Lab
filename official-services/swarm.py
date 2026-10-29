"""One scoped Jiuwen process per job, exposing only participant-owned SDK host tools."""
import copy
import asyncio
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

def assemble_text(data, fields):
    """Pure JSON/text conversion; no filesystem, code execution or inferred content."""
    if not isinstance(data,dict) or not isinstance(fields,list) or len(fields)>32:
        raise ValueError('Expected JSON data and at most 32 text fields')
    result=copy.deepcopy(data);seen=set();size=0
    for field in fields:
        if not isinstance(field,dict) or set(field)!={'path','lines'}:raise ValueError('Invalid text field')
        path,lines=field['path'],field['lines']
        if not isinstance(path,list) or not 1<=len(path)<=8 or any(not isinstance(k,str) or not k or len(k)>200 for k in path):raise ValueError('Invalid text field path')
        if not isinstance(lines,list) or len(lines)>4000 or any(not isinstance(line,str) for line in lines):raise ValueError('Text must be a list of lines')
        key=tuple(path)
        if key in seen:raise ValueError('Duplicate text field')
        seen.add(key);text='\n'.join(lines);size+=len(text)
        if size>200000:raise ValueError('Text fields are too large')
        node=result
        for part in path[:-1]:
            if part not in node:node[part]={}
            node=node[part]
            if not isinstance(node,dict):raise ValueError('Text path must refer to object members')
        if path[-1] in node:raise ValueError('Text field must not overwrite JSON data')
        node[path[-1]]=text
    return result


class SwarmEngine:
    def __init__(self,command=None):
        self.command=command or json.loads(os.environ.get('IOALAB_SWARM_COMMAND','["jiuwenswarm-process"]'))
        if not isinstance(self.command,list) or not self.command or not all(isinstance(s,str) and s for s in self.command):raise ValueError('IOALAB_SWARM_COMMAND must be a JSON argv array')
        self.runs=set();self.lock=threading.Lock()

    def check(self):
        if shutil.which(self.command[0]) is None:raise RuntimeError('Jiuwen process CLI is not installed; configure IOALAB_SWARM_COMMAND')
        result=subprocess.run([*self.command,'--help'],capture_output=True,text=True,encoding='utf-8',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or '--run-jsonl' not in result.stdout:raise RuntimeError('Jiuwen process CLI must support --run-jsonl machine mode')
        catalog=self.model_catalog()
        keys={m.get('selection_key') for m in catalog.get('models',[]) if m.get('selection_key')}
        selected=os.getenv('IOALAB_SERVICE_MODEL') or catalog.get('current_selection')
        if not selected or selected not in keys:
            raise RuntimeError('Configure IOALAB_SERVICE_MODEL with a model.list selection_key, not a Web model resource ID')

    def model_catalog(self):
        """Read local model choices before registering any service or accepting fees."""
        async def query():
            from jiuwenswarm_sdk import Client
            client=Client(command=self.command,env={**os.environ,'PYTHONUTF8':'1'})
            return await client.query('model.list',deadline_seconds=45)
        try:result=asyncio.run(query())
        except Exception as exc:raise RuntimeError('Jiuwen model catalog is unavailable: '+type(exc).__name__) from None
        if result.get('status')!='completed':raise RuntimeError('Jiuwen model catalog query failed')
        return result.get('data',{})

    def run(self,job,definition,workspace,url,token):
        from ioalab_sdk.provider_capability import ProviderJob
        skill=(Path(__file__).parent/'skills/ioalab-provider/SKILL.md').read_text(encoding='utf-8')
        tools=[{'name':'ioa_inspect','description':'Read the authorized current job and service definition','input_schema':{'type':'object','properties':{},'additionalProperties':False}}]
        for name in ('quote','selftest','deliver'):
            schema={'type':'object','properties':{'price':{'type':'number','minimum':0}},'required':['price'],'additionalProperties':False} if name=='quote' else {'type':'object','properties':{'data':{'type':'object'}},'required':['data'],'additionalProperties':False}
            if name=='selftest':
                schema['properties']['text_fields']={'type':'array','maxItems':32,'items':{'type':'object','properties':{'path':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':8},'lines':{'type':'array','items':{'type':'string'},'maxItems':4000}},'required':['path','lines'],'additionalProperties':False}}
            if name=='deliver':schema.pop('required',None)
            tools.append({'name':'ioa_'+name,'description':{'quote':'Submit a fixed quote for this job','selftest':'Run the contract verifier; source text may use text_fields line arrays','deliver':'Submit data through Provider SDK; with empty arguments use the last passed selftest candidate'}[name],'input_schema':schema})
        request={'schema_version':'0.1','type':'run','request_id':job['id'],'mode':'agent.work.normal',
                 'input':'只处理本单。先ioa_inspect，再根据kind报价或生产。使用当前任务definition.generator和合同spec自行生成JSON；禁止照抄sample。生产先ioa_selftest，失败按判据修正最多3次，通过后ioa_deliver。只有SDK返回delivered或quoted才报告完成。无需读取文件、执行Shell或请求用户交互。',
                 'agent':{'name':'ioa_service','instructions':skill,'tools':[t['name'] for t in tools]},
                 'host_tools':tools,'workspace':{'cwd':str(workspace),'project_dir':str(workspace)},'timeout_seconds':300,'max_turns':16}
        if os.getenv('IOALAB_SERVICE_MODEL'):request['model']=os.environ['IOALAB_SERVICE_MODEL']
        capability=ProviderJob(url,token)
        async def execute():
            from jiuwenswarm_sdk import Client
            loop=asyncio.get_running_loop();cancel=asyncio.Event()
            with self.lock:self.runs.add((loop,cancel))
            client=Client(command=self.command,cwd=workspace,env={**os.environ,'PYTHONUTF8':'1'})
            passed=[None]
            async def tool_call(record):
                payload=record['payload'];name=payload['name'];arguments=payload['arguments']
                if name not in {t['name'] for t in tools}:raise ValueError('Tool is outside the current job capability')
                operation=name.removeprefix('ioa_');body=arguments if operation=='quote' else arguments.get('data',{})
                if operation=='selftest':
                    passed[0]=None
                    body=assemble_text(body,arguments.get('text_fields',[]))
                elif operation=='deliver' and 'data' not in arguments:
                    if passed[0] is None:raise ValueError('No passed selftest candidate in this run')
                    body=copy.deepcopy(passed[0])
                result=await asyncio.to_thread(capability.call,operation,body)
                if operation=='selftest' and result.get('status')=='pass':passed[0]=copy.deepcopy(body)
                return result
            events=Path(workspace)/'swarm-events.jsonl'
            async def event(record):
                # Current-job tool traffic only; host capability tokens never enter records.
                with events.open('a',encoding='utf-8') as stream:stream.write(json.dumps(record,ensure_ascii=False)+'\n')
            try:
                result=await client.run(request,on_event=event,on_tool_call=tool_call,cancel=cancel,deadline_seconds=330)
                (Path(workspace)/'swarm-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                return result
            finally:
                with self.lock:self.runs.discard((loop,cancel))
        try:result=asyncio.run(execute())
        except Exception as exc:raise RuntimeError('Swarm task failed: '+type(exc).__name__) from exc
        if result.get('status')!='completed':raise RuntimeError('Swarm task failed or requires interaction: '+str((result.get('error') or {}).get('code','unknown')))
        return {'session':result.get('session_id'),'usage':result.get('usage',{})}

    def close(self):
        with self.lock:
            for loop,cancel in list(self.runs):loop.call_soon_threadsafe(cancel.set)
