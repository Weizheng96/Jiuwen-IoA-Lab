"""One scoped Jiuwen process per job, exposing only participant-owned SDK host tools."""
import asyncio
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

class SwarmEngine:
    def __init__(self,command=None):
        self.command=command or json.loads(os.environ.get('IOALAB_SWARM_COMMAND','["jiuwenswarm-process"]'))
        if not isinstance(self.command,list) or not self.command or not all(isinstance(s,str) and s for s in self.command):raise ValueError('IOALAB_SWARM_COMMAND must be a JSON argv array')
        self.runs=set();self.lock=threading.Lock()

    def check(self):
        if shutil.which(self.command[0]) is None:raise RuntimeError('Jiuwen process CLI is not installed; configure IOALAB_SWARM_COMMAND')
        result=subprocess.run([*self.command,'--help'],capture_output=True,text=True,encoding='utf-8',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or '--run-jsonl' not in result.stdout:raise RuntimeError('Jiuwen process CLI must support --run-jsonl machine mode')

    def run(self,job,definition,workspace,url,token):
        from ioalab_sdk.provider_capability import ProviderJob
        skill=(Path(__file__).parent/'skills/ioalab-provider/SKILL.md').read_text(encoding='utf-8')
        tools=[{'name':'ioa_inspect','description':'Read the authorized current job and service definition','input_schema':{'type':'object','properties':{},'additionalProperties':False}}]
        for name in ('quote','selftest','deliver'):
            schema={'type':'object','properties':{'price':{'type':'number','minimum':0}},'required':['price'],'additionalProperties':False} if name=='quote' else {'type':'object','properties':{'data':{'type':'object'}},'required':['data'],'additionalProperties':False}
            tools.append({'name':'ioa_'+name,'description':{'quote':'Submit a fixed quote for this job','selftest':'Run the contract verifier on generated JSON','deliver':'Submit generated JSON through the Provider SDK'}[name],'input_schema':schema})
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
            async def tool_call(record):
                payload=record['payload'];name=payload['name'];arguments=payload['arguments']
                if name not in {t['name'] for t in tools}:raise ValueError('Tool is outside the current job capability')
                operation=name.removeprefix('ioa_');body=arguments if operation=='quote' else arguments.get('data',{})
                return await asyncio.to_thread(capability.call,operation,body)
            try:return await client.run(request,on_tool_call=tool_call,cancel=cancel,deadline_seconds=330)
            finally:
                with self.lock:self.runs.discard((loop,cancel))
        try:result=asyncio.run(execute())
        except Exception as exc:raise RuntimeError('Swarm task failed: '+type(exc).__name__) from exc
        if result.get('status')!='completed':raise RuntimeError('Swarm task failed or requires interaction: '+str((result.get('error') or {}).get('code','unknown')))
        return {'session':result.get('session_id'),'usage':result.get('usage',{})}

    def close(self):
        with self.lock:
            for loop,cancel in list(self.runs):loop.call_soon_threadsafe(cancel.set)
