"""Run separate processes and preserve real local evidence. Fixtures are explicit."""
import argparse,json,os,socket,subprocess,sys,time,uuid
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]

def port():
    with socket.socket() as sock: sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,default=ROOT/'data/local-e2e');p.add_argument('--port',type=int,default=8750);p.add_argument('--swarm',default='');args=p.parse_args()
    folder=args.folder.resolve();folder.mkdir(parents=True,exist_ok=True)
    config_path=folder/'run.json'
    if config_path.exists(): raise ValueError('Use a fresh output folder so prior evidence is preserved')
    provider_port,smtp_port=port(),port();base=f'http://127.0.0.1:{args.port}';provider=f'http://127.0.0.1:{provider_port}'
    import secrets
    config={'base':base,'provider':provider,'providerPort':provider_port,'smtpPort':smtp_port,'swarm':args.swarm,'folder':str(folder),'fixtures':['calendar','smtp-capture','provider-sample'],'accounts':{'a':{'username':'ISC_001','password':secrets.token_urlsafe(18)},'b':{'username':'ioa_demo_b','password':secrets.token_urlsafe(18)}}}
    config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(config_path,0o600)
    (folder/'calendar-fixture.json').write_text('[]',encoding='utf-8')
    env={**os.environ,'PYTHONUTF8':'1','IOALAB_STUB':'1','IOALAB_MODE':'dev','IOALAB_ADMIN_PASSWORD':secrets.token_urlsafe(18),'IOALAB_ASSISTANT_PASSWORD':config['accounts']['a']['password'],'IOALAB_DATA':str(folder/'core'),'IOALAB_AUTOSEED':'1','IOALAB_PORT':str(args.port),'IOALAB_PUBLIC_URL':base,'IOALAB_BASE_URL':base,'IOALAB_PROVIDER_PORT':str(provider_port),'IOALAB_PROVIDER_PUBLIC_URL':provider,'IOALAB_PROVIDER_DATA':str(folder/'providers')}
    processes=[];logs=[]
    def launch(name,command):
        log=(folder/(name+'.log')).open('w',encoding='utf-8');logs.append(log)
        proc=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));processes.append(proc);return proc
    def wait(url,proc,check=lambda d:True):
        until=time.monotonic()+90
        while time.monotonic()<until:
            if proc.poll() is not None: raise RuntimeError('Process exited; inspect '+str(folder))
            try:
                r=httpx.get(url,timeout=2,trust_env=False)
                if r.is_success and check(r.json()):return
            except httpx.HTTPError:pass
            time.sleep(.2)
        raise TimeoutError(url)
    try:
        core=launch('core',[sys.executable,'-m','ioalab','serve']);wait(base+'/api/config',core)
        b=config['accounts']['b'];httpx.post(base+'/api/auth/register',json=b,trust_env=False).raise_for_status()
        provider_proc=launch('providers',[sys.executable,str(ROOT/'official-services/serve.py'),'--only','med-reminder,triage-advice,rehab-plan']);wait(provider+'/health',provider_proc,lambda d:d['ready'])
        launch('smtp',[sys.executable,str(ROOT/'demo/smtp_capture.py'),'--port',str(smtp_port),'--folder',str(folder/'mail')])
        launch('receiver',[sys.executable,str(ROOT/'demo/receiver.py'),'--config',str(config_path),'--calendar-fixture',str(folder/'calendar-fixture.json')])
        print('LOCAL E2E: '+base+'; explicit provider/calendar/SMTP fixtures; '+('real Jiuwen' if args.swarm else 'Jiuwen not configured'),flush=True)
        print('Account credentials are only in '+str(config_path)+'; no external email delivery.',flush=True)
        while True:
            if any(proc.poll() is not None for proc in processes):raise RuntimeError('A participant exited; inspect logs')
            time.sleep(1)
    finally:
        for proc in reversed(processes):
            if proc.poll() is not None:continue
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            else:proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill()
        for log in logs:log.close()

if __name__=='__main__':main()
