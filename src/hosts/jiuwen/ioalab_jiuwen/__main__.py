"""Known local assembly; the core SDK never imports a concrete host implementation."""
import argparse
import json
from pathlib import Path
import sys

from ioalab_sdk.provider import EDITABLE_SERVICE_FIELDS,validate_provider
from ioalab_sdk.runtime import start,setup_command
from ioalab_sdk.runtime import public_error
from ioalab_sdk.runtime import RuntimeFault,configure_stdio
from .assembly import assembled


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(prog='ioalab-jiuwen')
    parser.add_argument('command',choices=('start','serve','web-extension','configuration','configure','validate'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--args-file',type=Path)
    parser.add_argument('--role',choices=('assistant','service'))
    args=parser.parse_args(argv)
    if args.command=='web-extension':
        if not args.args_file or not args.role:parser.error('--args-file and --role are required for web-extension')
    elif args.command=='configure':
        if not args.args_file or args.role:parser.error('configure requires --args-file without --role')
    elif args.args_file or args.role:parser.error('--args-file is only for configure/web-extension; --role is only for web-extension')
    try:
        if args.command in ('configuration','configure','validate'):
            from .configuration import HOST_FIELDS,merge_host,validate_host
            def validate(config):
                validate_provider(config.provider,config.identities)
                validate_host(config,base_dir=args.config.resolve().parent)
            result=setup_command(args.command,args.config,args.args_file,validate=validate,service_fields=EDITABLE_SERVICE_FIELDS,
                                 host_update=merge_host,host_fields=HOST_FIELDS)
            print(json.dumps(result,ensure_ascii=False))
            return 0
        if args.command=='web-extension':
            from .web_setup import prepare_extension
            print(json.dumps(prepare_extension(args.config,args.args_file,args.role),ensure_ascii=False))
            return 0
        if args.command=='start':
            result=start(args.config,command=[sys.executable,'-m','ioalab_jiuwen'],assembly='ioalab-jiuwen')
            print(json.dumps(result,ensure_ascii=False))
            return 0 if result['status'] in ('completed','waiting') else 1
        with assembled(args.config) as assembly:assembly.run()
    except RuntimeFault as exc:
        print(json.dumps({'status':'rejected','data':None,'error':public_error(exc)},ensure_ascii=False))
        return 1
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'failed','data':None,'error':{'code':'not_ready','type':type(exc).__name__,'recovery':'inspect_local_configuration'}}))
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
