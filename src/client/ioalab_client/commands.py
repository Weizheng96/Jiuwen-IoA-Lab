"""Role-bound calls through the single pip-installed client."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def installation():
    from . import installation as installed
    return installed


def invoke(python,arguments):
    env={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE')}
    return subprocess.run([str(python),'-I','-X','utf8',*arguments],env={**env,'PYTHONUTF8':'1','PYTHONNOUSERSITE':'1'},capture_output=True,text=True,encoding='utf-8')


def emit(value):
    print(json.dumps(value,ensure_ascii=True))


def relay(result):
    try:
        value=json.loads(result.stdout)
        if not isinstance(value,dict) or value.get('status') not in ('completed','accepted','running','waiting','rejected','failed','unknown'):
            raise ValueError('Invalid SDK result')
    except ValueError:
        emit({'status':'unknown','data':None,'error':{'code':'unknown_result',
            'message':'SDK result is incomplete or invalid; preserve the original arguments and query the original operation',
            'recovery':'resume_original'}})
        return 1
    emit(value)
    return result.returncode


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):stream.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--role',choices=('assistant','service'),required=True,action='append');parser.add_argument('operation');parser.add_argument('--config',type=Path);parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv);root=Path(__file__).resolve().parents[1]
    if len(args.role)!=1:parser.error('Select exactly one role; a role skill cannot override its bound role')
    try:
        role=args.role[0]
        if role not in ('assistant','service'):raise RuntimeError('The product manifest must declare assistant or service')
        prefixes=('runtime.','identity.','account.','confirmations.','operations.','assistant.' if role=='assistant' else 'provider.')
        if not args.operation.startswith(prefixes):
            example='assistant.delegation.prepare' if role=='assistant' else 'provider.tasks.get'
            emit({'status':'rejected','data':None,'error':{'code':'invalid_input',
                'message':f'Use the complete operation name for this {role} product, such as runtime.status or {example}. '
                    +'Allowed namespaces: '+', '.join(prefixes)+'. Correct the operation argument; do not change identity roles.',
                'recovery':'correct_input'}});return 1
        prepared=installation().prepare()
        if args.operation == 'runtime.install.status' or prepared['status']!='completed':
            emit(prepared);return 0 if prepared['status'] in ('completed','waiting') else 1
        python=Path(prepared['data']['python'])
        config=(args.config or Path.home()/'.ioalab/settings.local.json').expanduser().resolve()
        if args.operation=='runtime.mcp.configuration':
            emit({'status':'completed','data':{'name':'ioalab','transport':'stdio','command':str(python),
                 'args':['-m','ioalab_sdk.mcp','--config',str(config)],
                 'codexCommand':['codex','mcp','add','ioalab','--',str(python),'-m','ioalab_sdk.mcp','--config',str(config)],
                 'claudeCommand':['claude','mcp','add','--transport','stdio','--scope','user','ioalab','--',str(python),'-m','ioalab_sdk.mcp','--config',str(config)],
                 'jiuwenEntry':{'name':'ioalab','transport':'stdio','command':str(python),'args':['-m','ioalab_sdk.mcp','--config',str(config)],'enabled':True},
                 'jiuwenJson':{'mcpServers':{'ioalab':{'command':str(python),'args':['-m','ioalab_sdk.mcp','--config',str(config)]}}},
                 'notice':'九问原生入口：扩展 → 我的扩展 → 连接器 → 创建 → 配置连接器，在添加JSON代码中粘贴 jiuwenJson 并确认。'
                          '没有原生登记工具时向用户展示该JSON和步骤，等待登记；不要读写宿主config.yaml、MCP私有目录或猜测API。'
                          '配置完成、加载成功、MCP Apps 和后台 AgentHost 能力分别核验。'},'error':None});return 0
        if args.operation in ('account.setup','account.setup.status') or args.operation=='runtime.connect' and not config.exists():
            arguments=['-m','ioalab_sdk.access','account-setup-status' if args.operation=='account.setup.status' else 'account-setup','--config',str(config)]
            if args.operation in ('account.setup','runtime.connect'):arguments+=['--role',role]
            if args.args_file:
                if args.operation.endswith('.status'):raise RuntimeError('Account status uses only --config')
                arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments)
        elif args.operation=='account.login':
            if args.args_file:raise RuntimeError('account.login uses only the local configuration; omit args-file')
            result=invoke(python,['-m','ioalab_sdk.access','login','--config',str(config)])
        elif args.operation=='runtime.web-extension':
            if not args.args_file:raise RuntimeError('Use an args-file containing the owned profile key')
            result=invoke(python,['-m','ioalab_jiuwen','web-extension','--config',str(config),
                '--args-file',str(args.args_file.expanduser().resolve()),'--role',role])
        elif args.operation in ('runtime.configuration','runtime.configure'):
            if args.operation=='runtime.configure' and not args.args_file:raise RuntimeError('Create a separate non-credential changes file')
            if args.operation=='runtime.configuration' and args.args_file:raise RuntimeError('runtime.configuration only uses --config')
            arguments=['-m','ioalab_jiuwen',args.operation.split('.',1)[1],'--config',str(config)]
            if args.args_file:arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments)
        elif args.operation in ('runtime.validate','runtime.start','runtime.connect'):
            checked=invoke(python,['-m','ioalab_jiuwen','validate','--config',str(config)])
            if checked.returncode:emit(json.loads(checked.stdout));return checked.returncode
            summary=json.loads(checked.stdout)['data']
            if not any(item['role']==role for item in summary['identities']):raise RuntimeError('Add this role to the existing shared configuration before starting it')
            if args.operation=='runtime.validate':emit(json.loads(checked.stdout));return 0
            if args.operation=='runtime.connect':
                inspected=invoke(python,['-m','ioalab_jiuwen','configuration','--config',str(config)])
                if inspected.returncode:return relay(inspected)
                public=json.loads(inspected.stdout)['data']
                missing=['host.'+key for key in ('command','data_dir') if key not in public['hostFields']]
                if missing:
                    emit({'status':'waiting','stage':'configuration','nextAction':'runtime.configure',
                          'data':{'configurationDigest':public['configurationDigest'],'requiredFields':missing},
                          'error':{'code':'not_ready','message':'Account is configured. Configure the existing Jiuwen Process command and workspace through runtime.configure, then resume runtime.connect. Do not read account configuration contents.',
                                   'recovery':'runtime.configure'}});return 0
            result=invoke(python,['-m','ioalab_jiuwen','start','--config',str(config)])
        else:
            arguments=['-m','ioalab_sdk.access','call',args.operation,'--config',str(config)]
            if args.args_file:arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments)
        if result.stderr:print(result.stderr,end='',file=sys.stderr)
        return relay(result)
    except Exception as exc:
        message=str(exc) if isinstance(exc,RuntimeError) else 'Check the local bundle/configuration; '+type(exc).__name__
        emit({'status':'rejected','data':None,'error':{'code':'not_ready','message':message,'recovery':'inspect_local_installation'}});return 1


if __name__=='__main__':raise SystemExit(main())
