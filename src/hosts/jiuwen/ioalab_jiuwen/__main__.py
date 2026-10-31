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
from ioalab_sdk.runtime.protocol import RuntimeFault,configure_stdio
from .process import ProcessHost
from .tools import ConfiguredTools


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(prog='ioalab-jiuwen')
    parser.add_argument('command',choices=('start','serve','web-extension'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--args-file',type=Path)
    parser.add_argument('--role',choices=('assistant','service'))
    args=parser.parse_args(argv)
    if args.command=='web-extension':
        if not args.args_file or not args.role:parser.error('--args-file and --role are required for web-extension')
    elif args.args_file or args.role:parser.error('--args-file and --role are only for web-extension')
    runtime=tools=None
    try:
        if args.command=='web-extension':
            from .web_setup import prepare_extension
            print(json.dumps(prepare_extension(args.config,args.args_file,args.role),ensure_ascii=False))
            return 0
        if args.command=='start':
            result=start(args.config,command=[sys.executable,'-m','ioalab_jiuwen'],assembly='ioalab-jiuwen')
            print(json.dumps(result,ensure_ascii=False))
            return 0 if result['status'] in ('completed','waiting') else 1
        config=RuntimeConfig.load(args.config)
        host_config=dict(config.host);specs=host_config.pop('business_tools',[])
        tools=ConfiguredTools(specs,config.identities,home=config.home,base_dir=args.config.resolve().parent)
        host=ProcessHost.from_config(host_config,base_dir=args.config.resolve().parent)
        personal=next((item['key'] for item in config.identities if item['role']=='assistant'),None)
        module=AssistantRole(host,tools.for_profile(personal))
        runtime=ResidentRuntime(config,modules=(module,ProviderRole(host,tools.by_profile)),host_status=lambda:{**host.capabilities(),'businessTools':tools.status()},assembly='ioalab-jiuwen')
        notices={identity.agent_id:tools.for_profile(identity.profile).get('owner.notify') for identity in runtime.identities}
        notices={actor:tool for actor,tool in notices.items() if tool is not None}
        if notices:host.notifier=lambda message:tools.notify(message,notices)
        if not host.probe()['available']:raise RuntimeFault('not_ready','Jiuwen model/capability probe did not pass','configure_host')
        tools.start()
        runtime.run()
    except RuntimeFault as exc:
        print(json.dumps({'status':'rejected','data':None,'error':public_error(exc)},ensure_ascii=False))
        return 1
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'failed','data':None,'error':{'code':'not_ready','type':type(exc).__name__,'recovery':'inspect_local_configuration'}}))
        return 1
    finally:
        # Business resources must outlive active runtime jobs, even if shutdown fails.
        if runtime:runtime.close()
        if tools:tools.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
