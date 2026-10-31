"""Official deployment entry; business work always belongs to resident SDK processes."""
import argparse
import json
import os
import signal
import threading
from pathlib import Path

from ioalab_sdk.runtime.protocol import RuntimeFault,configure_stdio
from ioalab_sdk.runtime.ipc import public_error
from .deployment import Deployment


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(prog='ioalab-official')
    parser.add_argument('command',choices=('prepare','authorize','serve','status'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--admin-password-env')
    args=parser.parse_args(argv)
    if (args.command=='authorize')!=bool(args.admin_password_env):parser.error('--admin-password-env is required only for authorize')
    try:
        deployment=Deployment(args.config)
        if args.command=='prepare':
            from .preparation import prepare
            result=prepare(deployment)
        elif args.command=='authorize':
            from .authorization import authorize,administrator_session
            result=authorize(deployment,administrator_session(deployment,os.environ.get(args.admin_password_env)))
        elif args.command=='status':
            from .coordinator import status
            result=status(deployment)
        else:
            from .coordinator import tick,connected
            environment=deployment.environment()
            os.environ.clear();os.environ.update(environment)
            stop=threading.Event()
            for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
            with deployment.lock():
                try:
                    while not stop.is_set():
                        print(json.dumps(tick(deployment),ensure_ascii=False),flush=True)
                        stop.wait(60)
                finally:
                    for account in deployment.state(prepared=True)['accounts']:
                        try:
                            ipc,live=connected(deployment,account,launch=False)
                            ipc.call('runtime.stop',{'bootId':live['bootId']})
                        except RuntimeFault:pass
            return 0
        print(json.dumps(result,ensure_ascii=False));return 0
    except RuntimeFault as exc:
        print(json.dumps({'status':'rejected','error':public_error(exc)},ensure_ascii=False));return 1
    except Exception as exc:
        print(json.dumps({'status':'failed','error':{'code':'not_ready','type':type(exc).__name__,'recovery':'inspect_official_configuration'}}));return 1


if __name__=='__main__':raise SystemExit(main())
