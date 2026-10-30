"""Known local assembly; the core SDK never imports a concrete host implementation."""
import argparse
import json
from pathlib import Path
import sys

from ioalab_sdk.assistant.role import AssistantRole
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.__main__ import start
from ioalab_sdk.runtime.ipc import public_error
from ioalab_sdk.runtime.protocol import RuntimeFault
from .process import ProcessHost


def main(argv=None):
    parser=argparse.ArgumentParser(prog='ioalab-jiuwen')
    parser.add_argument('command',choices=('start','serve'))
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args(argv)
    runtime=None
    try:
        if args.command=='start':
            result=start(args.config,command=[sys.executable,'-m','ioalab_jiuwen'])
            print(json.dumps(result,ensure_ascii=False))
            return 0 if result['status'] in ('completed','waiting') else 1
        config=RuntimeConfig.load(args.config)
        host=ProcessHost.from_config(config.host,base_dir=args.config.resolve().parent)
        module=AssistantRole(host)
        runtime=ResidentRuntime(config,modules=(module,ProviderRole()),host_status=host.capabilities)
        if not host.probe()['available']:raise RuntimeFault('not_ready','Jiuwen model/capability probe did not pass','configure_host')
        runtime.run()
    except RuntimeFault as exc:
        print(json.dumps({'status':'rejected','data':None,'error':public_error(exc)},ensure_ascii=False))
        return 1
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'failed','data':None,'error':{'code':'not_ready','type':type(exc).__name__,'recovery':'inspect_local_configuration'}}))
        return 1
    finally:
        if runtime:runtime.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
