"""SSH transport for the explicitly selected new deployment server (paramiko 4)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.agents/server_new.txt'
HOSTS = ROOT / '.agents/data/ssh/new-server-known-hosts'


def credentials(source=SOURCE):
    try:
        user, password, host = Path(source).read_text(encoding='utf-8-sig').splitlines()
        from ipaddress import ip_address
        ip_address(host)
        if not user.strip() or not password or user != user.strip():
            raise ValueError()
        return user, password, host
    except (OSError, ValueError):
        raise RuntimeError('New-server private connection source unavailable or invalid') from None


@contextmanager
def connect(*, source=SOURCE, known_hosts=HOSTS, trust_new_host=False):
    import paramiko
    user, password, host = credentials(source)
    known_hosts = Path(known_hosts)
    client = paramiko.SSHClient()
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    if trust_new_host:
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        # AutoAddPolicy saves only previously unknown hosts; changed keys still fail.
        if not known_hosts.exists():
            known_hosts.touch(mode=0o600)
        client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, password=password, look_for_keys=False,
                       allow_agent=False, timeout=20, banner_timeout=20, auth_timeout=20)
        yield client
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trust-new-host', action='store_true', help='Record first host key; changed keys are rejected')
    parser.add_argument('--known-hosts', type=Path, default=HOSTS)
    parser.add_argument('--script', type=Path, help='UTF-8 Bash script, sent through stdin')
    parser.add_argument('--upload', nargs=2, metavar=('LOCAL', 'REMOTE'))
    args = parser.parse_args()
    if bool(args.script) == bool(args.upload):
        parser.error('Select exactly one script or upload')
    try:
        with connect(known_hosts=args.known_hosts, trust_new_host=args.trust_new_host) as client:
            if args.upload:
                with client.open_sftp() as sftp:
                    sftp.put(*args.upload)
                return 0
            stdin, stdout, stderr = client.exec_command('bash -se', timeout=900)
            stdin.write(args.script.read_text(encoding='utf-8-sig'))
            stdin.channel.shutdown_write()
            # Merge output at the channel to avoid stderr backpressure.
            stdout.channel.set_combine_stderr(True)
            for line in stdout:
                print(line, end='', flush=True)
            return stdout.channel.recv_exit_status()
    except Exception as error:
        print('SSH operation failed: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
