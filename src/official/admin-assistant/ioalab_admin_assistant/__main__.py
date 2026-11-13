"""Installer-only fixed setup; there is deliberately no approve or serve command."""
import argparse
import json
from pathlib import Path

from ioalab_sdk.runtime import public_error
from ioalab_sdk.runtime import RuntimeFault, configure_stdio
from .deployment import Deployment, operator
from .preparation import prepare, status


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(prog='ioalab-admin-assistant')
    parser.add_argument('command',choices=('prepare','status','configure-email'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--recipient')
    args=parser.parse_args(argv)
    try:
        operator()
        deployment=Deployment(args.config)
        if args.command=='configure-email':
            from .configuration import configure_email
            value=configure_email(deployment,args.recipient)
        else:
            value=(prepare if args.command=='prepare' else status)(deployment)
        print(json.dumps(value,ensure_ascii=False))
        return 0 if value['status']=='completed' else 2
    except RuntimeFault as exc:
        print(json.dumps({'status':'rejected','error':public_error(exc)},ensure_ascii=False)); return 1
    except Exception as exc:
        print(json.dumps({'status':'failed','error':{'code':'not_ready','type':type(exc).__name__,
              'recovery':'inspect_original_administrator_preparation'}})); return 1


if __name__=='__main__': raise SystemExit(main())
