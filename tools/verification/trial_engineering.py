"""One isolated engineering epoch, never entered into the public seat allocator."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(action,config_path,state,inputs):
    from ioalab_trial.sandbox import DockerSandbox
    config_path=Path(config_path).resolve();state=Path(state).resolve()
    config=json.loads(config_path.read_text())
    if config.get('deployment_profile')!='linux-server' or not config['sandbox']['deployment'].endswith('-engineering'):
        raise ValueError('Use a separate linux-server engineering resource identity')
    sandbox=DockerSandbox(config['sandbox'],deployment_profile='linux-server')
    binding={'configSha256':sha(config_path),'imageInputs':json.loads(Path(inputs).read_text())}
    if state.exists():
        operation=json.loads(state.read_text())
        if operation['binding']!=binding:raise ValueError('Continue the exact original engineering configuration')
    else:
        if action!='prepare':raise ValueError('The original engineering operation is absent')
        operation={'binding':binding,'epoch':uuid.uuid4().hex,'seat':1,'phase':'preparing'}
        state.parent.mkdir(parents=True,exist_ok=True)
        with state.open('x') as stream:json.dump(operation,stream)
        state.chmod(0o600)
    seat,epoch=operation['seat'],operation['epoch']
    if action=='prepare':
        if operation['phase']!='preparing':raise ValueError('Engineering resources already exist; observe or retire them')
        # A failed/partial prepare remains unknown, never automatically adopted.
        operation['phase']='creating';state.write_text(json.dumps(operation))
        sandbox.prepare(seat,epoch)
        operation.update(phase='prepared',imageId=sandbox.image_id)
    elif action=='observe':
        if operation['phase']!='prepared':raise ValueError('Inspect partial engineering creation before proceeding')
        import httpx
        from websockets.sync.client import connect
        ui=sandbox.ui(seat,epoch)
        response=httpx.get(ui+'/?user_id=trial',trust_env=False,timeout=10);response.raise_for_status()
        observations={'nativeHttp':{'status':response.status_code,'bodySha256':hashlib.sha256(response.content).hexdigest()}}
        for method in ('skills.installed','config.get','session.list'):
            identifier=uuid.uuid4().hex
            with connect(ui.replace('http:','ws:',1)+'/ws?user_id=trial',open_timeout=5,close_timeout=2) as socket:
                socket.send(json.dumps({'type':'req','id':identifier,'method':method,'params':{}}))
                for _ in range(100):
                    reply=json.loads(socket.recv(timeout=5))
                    if reply.get('id')!=identifier or reply.get('type')!='res':continue
                    if reply.get('ok') is not True:raise RuntimeError('Original native public query rejected')
                    data=reply.get('payload',reply.get('data',{}))
                    observations[method]={'responseReceived':True}
                    if method=='config.get':observations[method]['a2uiEnabled']=data.get('a2ui_enabled') in (True,'true')
                    if method=='skills.installed':observations[method]['assistantSkillPresent']='ioalab-delegate' in json.dumps(data)
                    break
                else:raise RuntimeError('No matching original native response')
        operation['engineeringObservations']=observations
    elif action=='retire':
        sandbox.stop(seat,epoch);sandbox.verify_stopped(seat,epoch);sandbox.wipe(seat,epoch)
        operation['phase']='retired'
    else:raise ValueError('Unknown engineering action')
    state.write_text(json.dumps(operation,indent=2)+'\n')
    return {'engineeringOnly':True,'productionReady':False,'phase':operation['phase'],
            'seat':seat,'epoch':epoch,'imageId':operation.get('imageId'),
            'sourceRevision':binding['imageInputs']['sourceRevision'],
            'contextSha256':binding['imageInputs']['contextSha256'],
            'nativeLoopback':sandbox.ui(seat,epoch) if operation['phase']=='prepared' else None,
            'observations':operation.get('engineeringObservations')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','observe','retire'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.action,args.config,args.state,args.inputs)));return 0


if __name__=='__main__':raise SystemExit(main())
