"""Local lifecycle commands and RPC client; never opens participant SQLite or private keys.

The SDK assembly passes the roles, service validation and editable fields to `run`."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .lease import Lease
from .config import RuntimeConfig,read_configuration,data_directory
from .files import private_json,read_arguments,prepare_home
from .ipc import LocalClient,public_error
from .protocol import RuntimeFault,configure_stdio
from .installation import require_matching,SAFE_CALLS


READ_ONLY={'runtime.status','identity.status','operations.get'}


def setup_command(command,path,arguments=None,*,validate,service_fields,host_update=None,host_fields=()):
    from .configuration import inspect_config,configure
    if command=='validate':
        config=RuntimeConfig.load(path);validate(config)
        data=config.public_summary()
    elif command=='configuration':data=inspect_config(path,validate=validate,host_fields=host_fields)
    else:data=configure(path,arguments,service_fields=service_fields,validate=validate,
                        host_update=host_update,host_fields=host_fields)
    return {'status':'completed','data':data,'error':None}


def require_configuration(state,config,*,assembly=None):
    account=state['account']
    if account['username']!=config.username or account['platform']!=config.platform:
        raise RuntimeFault('identity_conflict','Running instance belongs to another configured account')
    require_matching(state.get('installation'),assembly=assembly)
    if state.get('configurationDigest')!=config.public_summary()['configurationDigest']:
        raise RuntimeFault('configuration_changed','The running instance uses another configuration; safely stop it before starting the edited configuration','runtime.stop')


def start(config_path,*,timeout=15,command=None,assembly=None,environment=None):
    config=RuntimeConfig.load(config_path)
    def matching(result):
        require_configuration(result['data'],config,assembly=assembly)
        return result
    prepare_home(config.home)
    client=LocalClient(config.home,timeout=.5)
    try:
        result=client.call('runtime.status')
    except RuntimeFault:
        pass
    else:
        return matching(result)
    try:
        # An OS-owned lock is current evidence; a PID/descriptor file is not.
        probe=Lease(config.home/'client.lock',timeout=0)
    except RuntimeError:
        return {'operationId':str(uuid.uuid4()),'status':'waiting','data':{'startingOrBusy':True},
                'error':{'code':'not_ready','message':'The instance has a live owner; wait for its RPC or stop it explicitly','recovery':'runtime.status'}}
    probe.close()
    config.home.mkdir(mode=0o700,parents=True,exist_ok=True)
    log_path=config.home/'runtime.log'
    with log_path.open('a',encoding='utf-8') as stream:
        if os.name!='nt':os.chmod(log_path,0o600)
        entry=command or [sys.executable,'-m','ioalab_sdk.access']
        child=subprocess.Popen([*entry,'serve','--config',str(Path(config_path).resolve())],
                               cwd=Path(config_path).resolve().parent,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env=environment)
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if child.poll() is not None:
            raise RuntimeFault('not_ready','Runtime bootstrap exited; inspect the private runtime log','inspect_local_log')
        try:result=client.call('runtime.status')
        except RuntimeFault:time.sleep(.1)
        else:return matching(result)
    return {'operationId':str(uuid.uuid4()),'status':'waiting','data':{'pid':child.pid,'starting':True},
            'error':{'code':'not_ready','message':'Bootstrap is still running; wait without starting another instance','recovery':'runtime.status'}}


def run(argv,*,modules,validate,service_fields):
    configure_stdio()
    parser=argparse.ArgumentParser(prog='ioalab-access')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('serve','start','validate','login','configuration','configure'):
        item=commands.add_parser(name)
        item.add_argument('--config',type=Path,required=True)
        if name=='configure':item.add_argument('--args-file',type=Path,required=True)
    call=commands.add_parser('call')
    call.add_argument('operation')
    call.add_argument('--config',type=Path,required=True)
    call.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv)
    runtime=None
    try:
        if args.command=='serve':
            from .resident import ResidentRuntime
            runtime=ResidentRuntime(RuntimeConfig.load(args.config),modules=modules())
            runtime.run()
            return 0
        if args.command=='login':
            from .authentication import login
            result=login(args.config)
        elif args.command in ('validate','configuration','configure'):result=setup_command(args.command,args.config,getattr(args,'args_file',None),validate=validate,service_fields=service_fields())
        elif args.command=='start':result=start(args.config)
        else:
            if args.operation not in READ_ONLY and not args.args_file:
                raise RuntimeFault('invalid_input','Create an arguments file before a mutating call')
            config_path=args.config.resolve();document=read_configuration(config_path)
            home=data_directory(config_path,document)
            values=read_arguments(args.args_file,configuration=config_path,home=home) if args.args_file else {}
            operation=values.get('operationId') or str(uuid.uuid4())
            client=LocalClient(home)
            if args.operation not in SAFE_CALLS:
                config=RuntimeConfig.from_mapping(config_path,document)
                state=client.call('runtime.status')['data'];installation=state.get('installation')
                require_configuration(state,config,assembly=installation.get('assembly') if isinstance(installation,dict) else None)
                client=LocalClient(home,boot_id=state['bootId'])
            if args.operation=='runtime.stop' and 'bootId' not in values:
                values['bootId']=client.call('runtime.status')['data']['bootId']
            values['operationId']=operation
            if args.args_file:private_json(args.args_file,values)
            result=client.call(args.operation,{key:value for key,value in values.items() if key!='operationId'},operation_id=operation)
            if args.operation=='runtime.status' and result['status']=='completed':
                actual=result['data'].get('installation')
                try:require_matching(actual,assembly=actual.get('assembly') if isinstance(actual,dict) else None)
                except RuntimeFault as exc:result={**result,'status':'rejected','error':public_error(exc)}
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result.get('status') in ('rejected','failed','unknown') else 0
    except RuntimeFault as exc:
        print(json.dumps({'operationId':exc.operation_id,'status':'unknown' if exc.code in ('unknown_result','result_unknown') else 'rejected',
                          'data':None,'error':public_error(exc)},ensure_ascii=False))
        return 1
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'failed','data':None,'error':{'code':'not_ready','message':type(exc).__name__,
                         'recovery':'inspect_local_configuration'}},ensure_ascii=False))
        return 1
    finally:
        if runtime:runtime.close()
