"""Thin product entry: background dependencies, then installed SDK/host public commands."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def installation():
    path=Path(__file__).with_name('installer.py')
    if not path.is_file():raise RuntimeError('Download the complete platform skill ZIP, including scripts/installer.py')
    spec=importlib.util.spec_from_file_location('ioalab_skill_installer',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


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
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation');parser.add_argument('--config',type=Path);parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv);root=Path(__file__).resolve().parents[1]
    try:
        role=json.loads((root/'assets/skill.json').read_text(encoding='utf-8'))['role']
        if role not in ('assistant','service'):raise RuntimeError('The product manifest must declare assistant or service')
        prefixes=('runtime.','identity.','account.','confirmations.','operations.','assistant.' if role=='assistant' else 'provider.')
        if not args.operation.startswith(prefixes):
            example='assistant.delegation.prepare' if role=='assistant' else 'provider.tasks.get'
            emit({'status':'rejected','data':None,'error':{'code':'invalid_input',
                'message':f'Use the complete operation name for this {role} product, such as runtime.status or {example}. '
                    +'Allowed namespaces: '+', '.join(prefixes)+'. Correct the operation argument; do not change identity roles.',
                'recovery':'correct_input'}});return 1
        prepared=installation().prepare(root/'assets',launch=args.operation in ('runtime.start','runtime.install'),retry=args.operation=='runtime.install')
        if args.operation in ('runtime.install','runtime.install.status') or prepared['status']!='completed':
            emit(prepared);return 0 if prepared['status'] in ('completed','waiting') else 1
        python=Path(prepared['data']['python'])
        config=(args.config or Path.home()/'.ioalab/settings.local.json').expanduser().resolve()
        if args.operation=='account.login':
            if args.args_file:raise RuntimeError('account.login uses only the local configuration; omit args-file')
            result=invoke(python,['-m','ioalab_sdk.runtime','login','--config',str(config)])
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
        elif args.operation in ('runtime.validate','runtime.start'):
            checked=invoke(python,['-m','ioalab_jiuwen','validate','--config',str(config)])
            if checked.returncode:emit(json.loads(checked.stdout));return checked.returncode
            summary=json.loads(checked.stdout)['data']
            if not any(item['role']==role for item in summary['identities']):raise RuntimeError('Add this role to the existing shared configuration before starting it')
            if args.operation=='runtime.validate':emit(json.loads(checked.stdout));return 0
            result=invoke(python,['-m','ioalab_jiuwen','start','--config',str(config)])
        else:
            arguments=['-m','ioalab_sdk.runtime','call',args.operation,'--config',str(config)]
            if args.args_file:arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments)
        if result.stderr:print(result.stderr,end='',file=sys.stderr)
        return relay(result)
    except Exception as exc:
        message=str(exc) if isinstance(exc,RuntimeError) else 'Check the local bundle/configuration; '+type(exc).__name__
        emit({'status':'rejected','data':None,'error':{'code':'not_ready','message':message,'recovery':'inspect_local_installation'}});return 1


if __name__=='__main__':raise SystemExit(main())
