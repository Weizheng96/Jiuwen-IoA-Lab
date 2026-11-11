"""SSH transport for the explicitly selected new deployment server (paramiko 4)."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.agents/server-connection.json'


def configuration(source=SOURCE):
    try:
        value = json.loads(Path(source).read_text(encoding='utf-8-sig'))
        fields = {'format', 'host', 'port', 'username', 'password', 'host_key_type',
                  'host_key_base64', 'host_key_sha256', 'connect_timeout_seconds'}
        if not isinstance(value, dict) or set(value) != fields or value['format'] != 'ioalab.server-connection/1':
            raise ValueError()
        from ipaddress import ip_address
        ip_address(value['host'])
        if (not isinstance(value['username'], str) or not value['username'].strip()
                or value['username'] != value['username'].strip()
                or not isinstance(value['password'], str) or not value['password']
                or type(value['port']) is not int or not 1 <= value['port'] <= 65535
                or type(value['connect_timeout_seconds']) is not int or not 1 <= value['connect_timeout_seconds'] <= 120
                or not isinstance(value['host_key_type'], str) or not value['host_key_type']):
            raise ValueError()
        key = base64.b64decode(value['host_key_base64'], validate=True)
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
        if not key or fingerprint != value['host_key_sha256']:
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError('Private server connection configuration unavailable or invalid') from None


def credentials(source=SOURCE):
    value = configuration(source)
    return value['username'], value['password'], value['host']


@contextmanager
def connect(*, source=SOURCE):
    import paramiko
    value = configuration(source)
    host, port = value['host'], value['port']
    key = paramiko.PKey.from_type_string(value['host_key_type'], base64.b64decode(value['host_key_base64']))
    client = paramiko.SSHClient()
    client.get_host_keys().add(host if port == 22 else '[' + host + ']:' + str(port), key.get_name(), key)
    try:
        timeout = value['connect_timeout_seconds']
        client.connect(host, port=port, username=value['username'], password=value['password'], look_for_keys=False,
                       allow_agent=False, timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
        yield client
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', type=Path, help='UTF-8 Bash script, sent through stdin')
    parser.add_argument('--upload', nargs=2, metavar=('LOCAL', 'REMOTE'))
    args = parser.parse_args()
    if bool(args.script) == bool(args.upload):
        parser.error('Select exactly one script or upload')
    try:
        with connect() as client:
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
