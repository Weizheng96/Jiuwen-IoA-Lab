"""Trial-owned SDK assembly. Confirmation authority stays in this SDK process."""
import argparse
import os
from pathlib import Path


def main():
    # The native shell shares this trial user's local SDK IPC. Prevent it from
    # reading the trusted channel secret through /proc or process memory.
    import ctypes
    if ctypes.CDLL(None,use_errno=True).prctl(4,0,0,0,0) != 0:
        raise RuntimeError('SDK trusted channel requires Linux nondumpable protection')
    from ioalab_sdk.assistant.role import AssistantRole
    from ioalab_sdk.provider import ProviderRole
    from ioalab_sdk.runtime.config import RuntimeConfig
    from ioalab_sdk.runtime.resident import ResidentRuntime
    from ioalab_sdk.runtime.trusted_user import TrustedUserPort, TrustedUserServer
    from ioalab_jiuwen.process import ProcessHost
    from ioalab_jiuwen.tools import ConfiguredTools
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--confirmation-port',type=int,default=8768)
    args = parser.parse_args()
    epoch = os.environ.pop('IOALAB_TRIAL_EPOCH')
    key = os.environ.pop('IOALAB_TRIAL_SDK_KEY')
    config = RuntimeConfig.load(args.config)
    host_config = dict(config.host)
    tools = ConfiguredTools(host_config.pop('business_tools',[]),config.identities,home=config.home,base_dir=args.config.parent)
    host = ProcessHost.from_config(host_config,base_dir=args.config.parent)
    personal = next(item['key'] for item in config.identities if item['role'] == 'assistant')
    runtime = server = None
    try:
        runtime = ResidentRuntime(config,modules=(AssistantRole(host,tools.for_profile(personal)),ProviderRole(host,tools.by_profile)),
            host_status=lambda:{**host.capabilities(),'businessTools':tools.status()},assembly='ioalab-jiuwen')
        identity = next(item.agent_id for item in runtime.identities if item.profile == personal)
        port = TrustedUserPort(runtime.confirmations,actor=identity,generation=epoch,
            authorize=lambda context,generation: context.get('generation') == epoch == generation and not Path('/epoch/control/revoked').exists())
        server = TrustedUserServer(port,key=key,listen_port=args.confirmation_port).start()
        if not host.probe()['available']:
            raise RuntimeError('Original host capability probe failed')
        tools.start()
        runtime.run()
    finally:
        if server:
            server.close()
        if runtime:
            runtime.close()
        tools.close()


if __name__ == '__main__':
    main()
