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
                               start_new_session=os.name!='nt',
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
    for name in ('account-setup','account-setup-status'):
        item=commands.add_parser(name)
        item.add_argument('--config',type=Path,required=True)
        if name=='account-setup':
            item.add_argument('--platform');item.add_argument('--role',choices=('assistant','service'))
            item.add_argument('--worker');item.add_argument('--args-file',type=Path);item.add_argument('--browser')
            item.add_argument('--both-roles',action='store_true');item.add_argument('--embedded',action='store_true')
    call=commands.add_parser('call')
    call.add_argument('operation')
    call.add_argument('--config',type=Path,required=True)
    call.add_argument('--args-file',type=Path)
    call.add_argument('--entry-ref',help=argparse.SUPPRESS)
    call.add_argument('--request-stdin',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    runtime=None
    native_operation=None
    native_submitted=False
    try:
        if args.command=='serve':
            from .resident import ResidentRuntime
            runtime=ResidentRuntime(RuntimeConfig.load(args.config),modules=modules())
            runtime.run()
            return 0
        if args.command in ('account-setup','account-setup-status'):
            from .account_setup import launch,serve,status
            if args.command=='account-setup-status':result=status(args.config)
            else:
                options={'role':args.role or 'assistant'}
                if args.platform:options['platform_url']=args.platform
                if args.browser:options['user_browser']=args.browser
                if args.both_roles:options['prepare_both_roles']=True
                if args.args_file:
                    from .files import read_json
                    options=read_json(args.args_file)
                    if not isinstance(options,dict):
                        raise RuntimeFault('invalid_input','Account setup arguments must be an object','correct_input')
                    if args.role is not None:
                        if options.get('role',args.role)!=args.role:
                            raise RuntimeFault('invalid_input','Account role must match the selected role skill','correct_input')
                        options={**options,'role':args.role}
                    if args.both_roles:options={**options,'prepare_both_roles':True}
                if args.worker:
                    serve(args.config,options,args.worker,embedded=args.embedded)
                    return 0
                result=launch(args.config,options)
        elif args.command=='login':
            from .authentication import login
            result=login(args.config)
        elif args.command in ('validate','configuration','configure'):result=setup_command(args.command,args.config,getattr(args,'args_file',None),validate=validate,service_fields=service_fields())
        elif args.command=='start':result=start(args.config)
        else:
            if args.operation not in READ_ONLY and not args.args_file and not args.request_stdin:
                raise RuntimeFault('invalid_input','Create an arguments file before a mutating call')
            packet=None
            if args.request_stdin:
                if args.args_file or args.entry_ref:raise RuntimeFault('invalid_input','Use one native transport envelope')
                raw=sys.stdin.read(128*1024+1)
                if len(raw)>128*1024:raise RuntimeFault('invalid_input','Native request exceeds 128 KiB')
                try:packet=json.loads(raw)
                except ValueError:raise RuntimeFault('invalid_input','Use a UTF-8 JSON native operation envelope') from None
                if (not isinstance(packet,dict) or set(packet)!={'operation','operationId','arguments','contextRef'} or
                    packet['operation']!=args.operation or not isinstance(packet['arguments'],dict) or 'operationId' in packet['arguments'] or
                    not isinstance(packet['operationId'],str) or not 1<=len(packet['operationId'])<=128 or
                    (packet['contextRef'] is not None and (not isinstance(packet['contextRef'],dict) or
                     set(packet['contextRef'])!={'entryRef'} or not isinstance(packet['contextRef']['entryRef'],str) or
                     not 1<=len(packet['contextRef']['entryRef'])<=8192))):
                    raise RuntimeFault('invalid_input','Use the original native operation envelope')
                native_operation=packet['operationId']
                values={**packet['arguments'],'operationId':packet['operationId']}
            config_path=args.config.resolve();document=read_configuration(config_path)
            home=data_directory(config_path,document)
            if not args.request_stdin:values=read_arguments(args.args_file,configuration=config_path,home=home) if args.args_file else {}
            boot_id=None
            if args.operation not in SAFE_CALLS:
                config=RuntimeConfig.from_mapping(config_path,document)
                state=LocalClient(home).call('runtime.status')['data']
                require_configuration(state,config,assembly=(state.get('installation') or {}).get('assembly'))
                boot_id=state['bootId']
            operation=values.get('operationId') or str(uuid.uuid4())
            from .client import ClientOperations
            client=ClientOperations(config_path,boot_id=boot_id)
            if args.operation=='runtime.stop' and 'bootId' not in values:
                values['bootId']=client.call('runtime.status')['data']['bootId']
            values['operationId']=operation
            if args.args_file:private_json(args.args_file,values)
            metadata=packet['contextRef'] if packet else ({'entryRef':args.entry_ref} if args.entry_ref else None)
            native_submitted=bool(native_operation)
            result=client.call(args.operation,{key:value for key,value in values.items() if key!='operationId'},operation_id=operation,context_ref=metadata)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result.get('status') in ('rejected','failed','unknown') else 0
    except RuntimeFault as exc:
        association=({'phase':'preflight','dispatch':'not_submitted','operationKind':'control_association'}
                     if native_operation and not native_submitted and exc.operation_id is None else {})
        print(json.dumps({**association,'operationId':native_operation if association else exc.operation_id,'status':'unknown' if exc.code in ('unknown_result','result_unknown') else 'rejected',
                          'data':None,'error':public_error(exc)},ensure_ascii=False))
        return 1
    except KeyboardInterrupt:return 130
    except Exception as exc:
        association=({'operationId':native_operation,'phase':'preflight','dispatch':'not_submitted','operationKind':'control_association'}
                     if native_operation and not native_submitted else {})
        if native_submitted:
            result={'operationId':native_operation,'status':'unknown','data':None,'error':{
                'code':'unknown_result','message':'The original RPC result is unknown; inspect the original operation',
                'dispatch':'unknown','recovery':'resume_original'}}
        else:result={**association,'status':'failed','data':None,'error':{'code':'not_ready','message':type(exc).__name__,
                    'recovery':'inspect_local_configuration'}}
        print(json.dumps(result,ensure_ascii=False))
        return 1
    finally:
        if runtime:runtime.close()
