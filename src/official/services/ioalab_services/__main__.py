"""Official deployment entry; business work always belongs to resident SDK processes."""
import argparse
import json
import os
from pathlib import Path

from ioalab_sdk.runtime import RuntimeFault,configure_stdio
from ioalab_sdk.runtime import public_error
from .deployment import Deployment


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(prog='ioalab-official')
    parser.add_argument('command',choices=('prepare','authorize','serve','status','stop'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--admin-password-env')
    parser.add_argument('--admin-username')
    parser.add_argument('--wait-seconds',type=float)
    args=parser.parse_args(argv)
    if args.wait_seconds is not None and (args.command!='stop' or not 0<=args.wait_seconds<=30):parser.error('--wait-seconds is only for stop and must be between zero and thirty')
    if (args.command=='authorize')!=bool(args.admin_password_env):parser.error('--admin-password-env is required only for authorize')
    if (args.command=='authorize')!=bool(args.admin_username):parser.error('--admin-username is required only for authorize')
    try:
        deployment=Deployment(args.config)
        if args.command=='prepare':
            from .preparation import prepare
            result=prepare(deployment)
        elif args.command=='authorize':
            from .authorization import authorize,administrator_session
            result=authorize(deployment,administrator_session(deployment,args.admin_username,os.environ.get(args.admin_password_env)))
        elif args.command=='status':
            from .coordinator import status
            result=status(deployment)
        elif args.command=='stop':
            from .lifecycle import stop
            result=stop(deployment,wait_seconds=args.wait_seconds or 0)
        else:
            from .lifecycle import serve
            environment=deployment.environment()
            os.environ.clear();os.environ.update(environment)
            return serve(deployment)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='completed' else 2 if result['status']=='waiting' else 1
    except RuntimeFault as exc:
        print(json.dumps({'status':'rejected','error':public_error(exc)},ensure_ascii=False));return 1
    except Exception as exc:
        print(json.dumps({'status':'failed','error':{'code':'not_ready','type':type(exc).__name__,'recovery':'inspect_official_configuration'}}));return 1


if __name__=='__main__':raise SystemExit(main())
