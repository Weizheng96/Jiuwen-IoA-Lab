"""Role-bound calls through the single pip-installed client."""
import argparse
from contextlib import ExitStack
import tempfile
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def installation():
    from . import installation as installed
    return installed


def invoke(python,arguments,*,input=None):
    env={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE')}
    return subprocess.run([str(python),'-I','-X','utf8',*arguments],env={**env,'PYTHONUTF8':'1','PYTHONNOUSERSITE':'1'},capture_output=True,text=True,encoding='utf-8',input=input)


def emit(value):
    print(json.dumps(value,ensure_ascii=True))


def relay(result,*,expected_operation=None,script_control=False):
    try:
        value=json.loads(result.stdout)
        if not isinstance(value,dict) or value.get('status') not in ('completed','accepted','running','waiting','rejected','failed','unknown'):
            raise ValueError('Invalid SDK result')
    except ValueError:
        emit({**({'operationId':expected_operation} if expected_operation else {}),'status':'unknown','data':None,'error':{'code':'unknown_result',
            'message':'SDK result is incomplete or invalid; preserve the original arguments and query the original operation',
            'recovery':'inspect_control_state' if script_control else 'resume_original'}})
        return 1
    if expected_operation:
        if script_control and not value.get('operationId'):
            value={**value,'operationId':expected_operation,'operationKind':'control_association'}
        elif value.get('operationId')!=expected_operation:
            emit({'operationId':expected_operation,'status':'unknown','data':None,'error':{
                'code':'unknown_result','message':'The caller result belongs to another operation; retain the original request',
                'recovery':'inspect_control_state' if script_control else 'resume_original'}})
            return 1
    emit(value)
    return result.returncode


def source_operations():
    """One public catalog classification for native wrappers and caller startup."""
    from ioalab_sdk.runtime import CATALOG,ASSISTANT,PROVIDER
    return tuple(sorted(name for name,value in CATALOG.items() if value.handler in (ASSISTANT,PROVIDER)))


def control_module(config):
    """The SDK identifies the account's assembly; host fields never select an entry."""
    from ioalab_sdk.runtime import runtime_assembly, RuntimeFault
    modules = {'ioalab-client': 'ioalab_client.runtime', 'ioalab-jiuwen': 'ioalab_jiuwen',
               'ioalab-codex': 'ioalab_codex', 'ioalab-claude-code': 'ioalab_claude_code',
               None: 'ioalab_sdk.access'}
    assembly = runtime_assembly(config)
    if assembly not in modules:
        raise RuntimeFault('not_ready', 'Use the verified original assembly control entry', 'inspect_original_assembly')
    return modules[assembly]


def ensure_running(python,config):
    """Reuse a verified live assembly; only a known stopped runtime may be started."""
    current = invoke(python, ['-m','ioalab_sdk.access','call','runtime.status','--config',str(config)])
    try: state = json.loads(current.stdout)
    except ValueError: return current
    if state.get('status') == 'completed': return current
    if (state.get('status') != 'rejected' or
            (state.get('error') or {}).get('recovery') != 'runtime.start'): return current
    return invoke(python,['-m',control_module(config),'start','--config',str(config)])


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):stream.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--role',choices=('assistant','service'),required=True,action='append');parser.add_argument('operation',nargs='?');parser.add_argument('--config',type=Path);parser.add_argument('--args-file',type=Path)
    parser.add_argument('--native-input',action='store_true',help=argparse.SUPPRESS);parser.add_argument('--host-ref');parser.add_argument('--host-type',choices=('jiuwen','codex','claude_code'))
    args=parser.parse_args(argv);root=Path(__file__).resolve().parents[1]
    if len(args.role)!=1:parser.error('Select exactly one role; a role skill cannot override its bound role')
    packet=None
    def output(value):
        if isinstance(packet,dict) and isinstance(packet.get('operationId'),str) and not value.get('operationId'):
            value={**value,'operationId':packet['operationId'],'operationKind':'control_association'}
        emit(value)
    def startup(result):
        value=json.loads(result.stdout)
        output({'operationId':packet['operationId'],'status':value['status'],'phase':'ensure_client',
                'dispatch':'not_submitted','operationKind':'control_association',
                'data':{'startup':value},'error':value.get('error')})
        return result.returncode
    cleanup=ExitStack()
    try:
        if args.native_input:
            raw=sys.stdin.read(128*1024+1)
            if len(raw)>128*1024:raise RuntimeError('Native request exceeds 128 KiB')
            packet=json.loads(raw)
            if (args.operation or args.args_file or not isinstance(packet,dict) or
                set(packet)!={'operation','operationId','arguments','contextRef'} or
                not isinstance(packet['operation'],str) or not isinstance(packet['operationId'],str) or
                not 1<=len(packet['operationId'])<=128 or not isinstance(packet['arguments'],dict) or
                (packet['contextRef'] is not None and (not isinstance(packet['contextRef'],dict) or set(packet['contextRef'])!={'entryRef'} or
                 not isinstance(packet['contextRef']['entryRef'],str) or not 1<=len(packet['contextRef']['entryRef'])<=8192)) or
                'operationId' in packet['arguments']):
                raise RuntimeError('Use the bound native entry request envelope')
            args.operation=packet['operation']
        elif not args.operation:raise RuntimeError('Select a complete operation')
        role=args.role[0]
        if role not in ('assistant','service'):raise RuntimeError('The product manifest must declare assistant or service')
        prefixes=('runtime.','identity.','account.','confirmations.','operations.','assistant.' if role=='assistant' else 'provider.')
        if not args.operation.startswith(prefixes):
            example='assistant.delegation.prepare' if role=='assistant' else 'provider.tasks.get'
            output({'status':'rejected','data':None,'error':{'code':'invalid_input',
                'message':f'Use the complete operation name for this {role} product, such as runtime.status or {example}. '
                    +'Allowed namespaces: '+', '.join(prefixes)+'. Correct the operation argument; do not change identity roles.',
                'recovery':'correct_input'}});return 1
        from ioalab_sdk.runtime import CATALOG,NATIVE_ENTRY_BIND_OPERATION
        if args.operation not in CATALOG:
            output({'status':'rejected','data':None,'error':{'code':'invalid_input','message':'Use an operation from the public catalog','recovery':'correct_input'}});return 1
        if args.operation==NATIVE_ENTRY_BIND_OPERATION:
            if not packet or packet['contextRef'] is None:
                raise RuntimeError('Complete this fixed setup through the installed native entry; do not provide session or path fields')
            if packet['arguments']:
                raise RuntimeError('Native entry setup accepts no business arguments')
        native_operations=source_operations()
        if packet and args.operation in native_operations and packet['contextRef'] is None:
            raise RuntimeError('Use the installed native role entry to capture the actual source')
        prepared=installation().prepare()
        if args.operation == 'runtime.install.status' or prepared['status']!='completed':
            output(prepared);return 0 if prepared['status'] in ('completed','waiting') else 1
        python=Path(prepared['data']['python'])
        from .layout import configuration_path
        config=configuration_path(args.config)
        if packet and packet['arguments']:
            from ioalab_sdk.runtime import CATALOG,private_json
            declaration=CATALOG.get(args.operation)
            if declaration and declaration.handler=='script':
                # This is a private transport adaptation to the existing controlled
                # args-file boundary, never a second config parser/effect path.
                temporary=Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='ioalab-native-control-')))
                args.args_file=temporary/'arguments.json'
                private_json(args.args_file,packet['arguments'])
        if args.operation=='runtime.mcp.configuration':
            from .native_skills import owner
            output({'status':'completed','data':{'stage':'mcp','nextAction':'enable_in_current_chat',
                 'accountRequired':False,'setupArguments':{'role':role},
                 'name':'ioalab','transport':'stdio','command':str(python),
                 'args':['-m','ioalab_sdk.mcp','--config',str(config)],
                 'codexCommand':['codex','mcp','add','ioalab','--',str(python),'-m','ioalab_sdk.mcp','--config',str(config)],
                 'claudeCommand':['claude','mcp','add','--transport','stdio','--scope','user','ioalab','--',str(python),'-m','ioalab_sdk.mcp','--config',str(config)],
                 'jiuwenEntry':{'name':'ioalab','transport':'stdio','command':str(python),'args':['-m','ioalab_sdk.mcp','--config',str(config)],'enabled':True},
                 'jiuwenJson':{'mcpServers':{'ioalab':{'command':str(python),'args':['-m','ioalab_sdk.mcp','--config',str(config)]}}},
                 'jiuwenAutoSetup':owner('jiuwen').mcp_setup(python,config),
                 'notice':'这里只生成登记资料，不表示已连接。账号配置可以尚不存在，不预创建配置、不先启动业务实例或打开浏览器登录页。'
                          '九问当前提供connect_mcp时直接调用jiuwenAutoSetup中的原生请求，自动复用或创建连接器并加载当前聊天；不让用户复制JSON。'
                          '只有原生回执sessionEnabled=true且当前聊天能调用ioa_setup才继续账号接入；保留宿主原生审批。'
                          '仅没有原生自动登记工具时，才指导用户先选择已有IoA连接器；没有已有项再展示JSON创建步骤。'
                          '手动创建入口：扩展 → 我的扩展 → 连接器 → 创建 → 配置连接器，在添加JSON代码中粘贴 jiuwenJson 并确认。'
                          '不要读写宿主config.yaml、MCP私有目录或猜测API。'
                          '登记后仍须在当前会话实际看到并调用ioa_setup，按setupArguments及用户给定平台进入账号页。'
                          '当前会话未挂载不代表宿主不支持；先等待启用，不直接回退account.setup。'
                          '配置完成、加载成功、MCP Apps 和后台 AgentHost 能力分别核验。'},'error':None});return 0
        if args.operation=='runtime.binding':
            if args.args_file:raise RuntimeError('runtime.binding does not use args-file')
            from .layout import locations
            output({'status':'completed','data':locations(config,python),'error':None});return 0
        elif args.operation=='runtime.onboarding':
            if not args.args_file:raise RuntimeError('Use an args-file containing the calling host')
            from ioalab_sdk.runtime import RuntimeConfig,read_arguments
            values=read_arguments(args.args_file,configuration=config,home=RuntimeConfig.load(config).home)
            if set(values)!={'host'} or values['host'] not in ('jiuwen','codex','claude_code'):
                raise RuntimeError('Use only host: jiuwen, codex or claude_code')
            from .onboarding import summary
            output(summary(values['host'],config=config,python=python,host_ref=args.host_ref));return 0
        elif args.operation in ('account.setup','account.setup.status') or args.operation=='runtime.connect' and not config.exists():
            arguments=['-m','ioalab_sdk.access','account-setup-status' if args.operation=='account.setup.status' else 'account-setup','--config',str(config)]
            if args.operation in ('account.setup','runtime.connect'):arguments+=['--role',role,'--both-roles']
            if args.args_file:
                if args.operation.endswith('.status'):raise RuntimeError('Account status uses only --config')
                arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments)
        elif args.operation=='account.login':
            if args.args_file:raise RuntimeError('account.login uses only the local configuration; omit args-file')
            result=invoke(python,['-m','ioalab_sdk.access','login','--config',str(config)])
        elif args.operation=='runtime.web-extension':
            if not args.args_file:raise RuntimeError('Use an args-file containing the owned profile key')
            result=invoke(python,['-m',control_module(config),'web-extension','--config',str(config),
                '--args-file',str(args.args_file.expanduser().resolve()),'--role',role])
        elif args.operation in ('runtime.configuration','runtime.configure'):
            if args.operation=='runtime.configure' and not args.args_file:raise RuntimeError('Create a separate non-credential changes file')
            if args.operation=='runtime.configuration' and args.args_file:raise RuntimeError('runtime.configuration only uses --config')
            arguments=['-m',control_module(config),args.operation.split('.',1)[1],'--config',str(config)]
            if args.args_file:arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments)
        elif args.operation in ('runtime.validate','runtime.start','runtime.connect'):
            module=control_module(config)
            checked=invoke(python,['-m',module,'validate','--config',str(config)])
            if checked.returncode:output(json.loads(checked.stdout));return checked.returncode
            summary=json.loads(checked.stdout)['data']
            if not any(item['role']==role for item in summary['identities']):raise RuntimeError('Add this role to the existing shared configuration before starting it')
            if args.operation=='runtime.validate':output(json.loads(checked.stdout));return 0
            result=invoke(python,['-m',module,'start','--config',str(config)])
        else:
            if args.operation in native_operations and CATALOG[args.operation].effect != 'read':
                started=ensure_running(python,config)
                readiness=json.loads(started.stdout)
                if readiness.get('status')!='completed':return startup(started) if packet else relay(started)
            arguments=['-m','ioalab_sdk.access','call',args.operation,'--config',str(config)]
            if packet:arguments+=['--request-stdin']
            if args.args_file:arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments,input=json.dumps(packet)) if packet else invoke(python,arguments)
        if result.stderr:print(result.stderr,end='',file=sys.stderr)
        if packet and args.operation=='runtime.start':return startup(result)
        return relay(result,expected_operation=packet['operationId'],script_control=CATALOG[args.operation].handler=='script') if packet else relay(result)
    except Exception as exc:
        from ioalab_sdk.runtime import RuntimeFault,public_error
        if isinstance(exc,RuntimeFault):
            output({'status':'rejected','data':None,'error':public_error(exc)});return 1
        message=str(exc) if isinstance(exc,RuntimeError) else 'Check the local bundle/configuration; '+type(exc).__name__
        output({'status':'rejected','data':None,'error':{'code':'not_ready','message':message,'recovery':'inspect_local_installation'}});return 1

    finally:
        cleanup.close()


if __name__=='__main__':raise SystemExit(main())
