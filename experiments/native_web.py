"""Start an isolated native web verification using an already installed Swarm."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from dotenv import dotenv_values
import yaml
from ioalab_jiuwen.web_setup import prepare_extension
from ioalab_jiuwen.web_notifications import WebNotifications
from ioalab_sdk.runtime.files import restrict


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',type=Path,required=True)
    parser.add_argument('--source-workspace',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--port-offset',type=int,default=0)
    args=parser.parse_args()
    python=args.python.resolve();source=args.source_workspace.resolve();root=args.output.resolve()
    if root.exists():raise RuntimeError('Use a new verification output directory')
    if root.is_relative_to(source):raise RuntimeError('Keep verification output outside the existing user workspace')
    if not 0<=args.port_offset<=1000:raise ValueError('Use a port offset from0 to1000')
    ports={key:port+args.port_offset for key,port in {'agent':18192,'web':19190,'gateway':19191,'frontend':15173}.items()}
    for port in ports.values():
        with socket.socket() as probe:probe.bind(('127.0.0.1',port))
    originals={name:sha(source/'config'/name) for name in ('.env','config.yaml')}
    root.mkdir(parents=True)
    restrict(root,directory=True)
    swarm=root/'swarm';swarm.mkdir()
    environment={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','JIUWENSWARM_DATA_DIR':str(swarm),
                 'JIUWENSWARM_CONFIG_DIR':str(swarm/'config')}
    script="from pathlib import Path; import os; from jiuwenswarm.common.utils import prepare_workspace; prepare_workspace(False,preferred_language='zh',workspace_dir=Path(os.environ['JIUWENSWARM_DATA_DIR']))"
    with (root/'initialize.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(python),'-B','-c',script],env=environment,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
    config=yaml.safe_load((source/'config/config.yaml').read_text(encoding='utf-8'))
    config.setdefault('updater',{})['enabled']=False
    config.setdefault('extensions',{})['extension_dirs']=''
    (swarm/'config/config.yaml').write_text(yaml.safe_dump(config,allow_unicode=True),encoding='utf-8')
    values=dict(dotenv_values(source/'config/.env',interpolate=False))
    values.update(JIUWENSWARM_DATA_DIR=str(swarm),JIUWENSWARM_CONFIG_DIR=str(swarm/'config'),
                  AGENT_SERVER_PORT=str(ports['agent']),AGENT_PORT=str(ports['agent']),AGENT_SERVER_URL=f"ws://127.0.0.1:{ports['agent']}",
                  WEB_PORT=str(ports['web']),GATEWAY_PORT=str(ports['gateway']),FRONTEND_PORT=str(ports['frontend']),EXTENSION_DIRS='',HEALTH_CHECK_INTERVAL='86400')
    for key in ('IOALAB_SWARM_BRIDGE_SECRET','IOALAB_SWARM_USER_ID'):values.pop(key,None)
    envfile=swarm/'config/.env'
    envfile.write_text(''.join(key+'='+json.dumps(value)+'\n' for key,value in values.items() if value is not None),encoding='utf-8')
    user='ioa-native-verification'
    options={'url':f"ws://127.0.0.1:{ports['web']}/ws?user_id={user}",'user_id':user,
             'secret_env':'IOALAB_NATIVE_VERIFICATION_SECRET','credentials_file':'notification.env','retry_seconds':1}
    settings={'platform_url':'http://127.0.0.1:8750','username':'native-web-verification','password':'unused-local-verification',
              'data_dir':str(root/'access'),'identities':[{'key':'personal','role':'assistant','name':'Native web verification'}],
              'host':{'command':[str(python.parent/('jiuwenswarm-process.exe' if os.name=='nt' else 'jiuwenswarm-process'))],'data_dir':str(swarm),
                      'business_tools':[{'factory':'jiuwen-web','profile':'personal','options':options}]}}
    configfile=root/'settings.local.json';configfile.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
    prepared=prepare_extension(configfile,'personal')
    processes=[]
    for label,module in [('app','jiuwenswarm.app'),('web','jiuwenswarm.channels.web.app_web')]:
        with (root/(label+'.log')).open('w',encoding='utf-8') as log:
            process=subprocess.Popen([str(python),'-B','-m',module,'--dotenv',str(envfile)],env={**environment,**{key:str(value) for key,value in values.items() if value is not None}},
                                     cwd=swarm,stdout=log,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        processes.append(process)
    record={'root':str(root),'pids':[process.pid for process in processes],'config':str(configfile),'web':f"http://127.0.0.1:{ports['frontend']}/chat/new?user_id="+user,
            'scope':'Independent native verification instance; existing source workspace and installation are not changed',
            'originalConfigHashes':originals}
    (root/'processes.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    queue=WebNotifications(options,home=root/'access',profile='personal',base_dir=root)
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        if any(process.poll() is not None for process in processes):raise RuntimeError('Native verification process exited; inspect private logs')
        try:
            store=queue.client.probe()
            assert originals=={name:sha(source/'config'/name) for name in originals}
            print(json.dumps({'status':'native_ready','root':str(root),'pids':record['pids'],'web':record['web'],
                              'receiptStoreId':store,'prepared':prepared['status'],'sourceWorkspaceUnchanged':True},ensure_ascii=False),flush=True)
            return 0
        except Exception:time.sleep(1)
    raise TimeoutError('Native instance has not passed the notification capability probe; inspect its private logs, do not restart another instance')


if __name__=='__main__':raise SystemExit(main())
