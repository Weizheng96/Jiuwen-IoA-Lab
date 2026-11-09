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
    from ioalab_sdk.runtime import TrustedUserPort, TrustedUserServer
    from ioalab_jiuwen import assembled
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--confirmation-port',type=int,default=8768)
    args = parser.parse_args()
    epoch = os.environ.pop('IOALAB_TRIAL_EPOCH')
    key = os.environ.pop('IOALAB_TRIAL_SDK_KEY')
    # The trial only adds the trusted confirmation service around the host's own assembly.
    with assembled(args.config) as assembly:
        personal = next(item['key'] for item in assembly.config.identities if item['role'] == 'assistant')
        identity = next(item.agent_id for item in assembly.runtime.identities if item.profile == personal)
        port = TrustedUserPort(assembly.runtime.confirmations,actor=identity,generation=epoch,
            authorize=lambda context,generation: context.get('generation') == epoch == generation and not Path('/epoch/control/revoked').exists())
        server = TrustedUserServer(port,key=key,listen_port=args.confirmation_port).start()
        try:
            assembly.run()
        finally:
            server.close()


if __name__ == '__main__':
    main()
