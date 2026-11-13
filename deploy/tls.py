"""Linux internal-CA maintenance; private material never leaves this host."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from .__main__ import existing
from .files import path_without_links, save, writer
from .linux import require_host
from .units import command, directive_path


FORMAT = 'ioalab.internal-ca/1'
FIELDS = {'format', 'ca_certificate', 'ca_private_key', 'ca_sha256', 'server_certificate', 'server_private_key'}
SYSTEMD_DIRECTORY = Path('/etc/systemd/system')


def openssl(*arguments):
    process = subprocess.run(['openssl', *map(str, arguments)], capture_output=True, timeout=30)
    if process.returncode:
        raise RuntimeError('OpenSSL certificate validation or signing failed')
    return process.stdout


def hostname(value):
    if (not isinstance(value, str) or len(value) > 253 or '.' not in value
            or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', part)
                   for part in value.split('.'))):
        raise ValueError('Provide an exact DNS hostname')
    return value.lower()


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Provide a root HTTPS origin')
    return f'https://{hostname(parsed.hostname)}:{parsed.port or 443}'


def certificate(path):
    der = openssl('x509', '-in', path, '-outform', 'DER')
    dates = openssl('x509', '-in', path, '-noout', '-startdate', '-enddate').decode('ascii').splitlines()
    result = {'sha256': hashlib.sha256(der).hexdigest()}
    for line in dates:
        key, value = line.split('=', 1)
        result[key] = ssl.cert_time_to_seconds(value)
    result['expiresAt'] = datetime.fromtimestamp(result['notAfter'], timezone.utc).isoformat()
    return result


def private_file(path):
    path = path_without_links(path)
    if not path.is_file() or (os.name != 'nt' and path.stat().st_mode & 0o077):
        raise ValueError('Protect the original private file with owner-only permissions')
    return path


class Authority:
    def __init__(self, layout, source=None):
        self.layout = layout
        self.identity = layout.inspect()
        self.record = layout.config/'tls/authority.json'
        source = private_file(source or self.record)
        self.value = json.loads(source.read_text(encoding='utf-8'))
        if (set(self.value) != FIELDS or self.value['format'] != FORMAT
                or not re.fullmatch('[0-9a-f]{64}', self.value['ca_sha256'])):
            raise ValueError('Invalid CA registration')
        self.paths = {}
        for key in ('ca_certificate', 'ca_private_key', 'server_certificate', 'server_private_key'):
            path = Path(self.value[key])
            if not path.is_absolute(): raise ValueError('Certificate paths must be absolute')
            self.paths[key] = path_without_links(path)
        if len(set(self.paths.values())) != 4: raise ValueError('Certificate and private-key paths must be distinct')
        private_file(self.paths['ca_private_key'])
        self.ca = certificate(self.paths['ca_certificate'])
        if self.ca['sha256'] != self.value['ca_sha256']:
            raise ValueError('CA fingerprint differs from the registered original')
        if b'CA:TRUE' not in openssl('x509', '-in', self.paths['ca_certificate'], '-noout', '-ext', 'basicConstraints'):
            raise ValueError('Certificate is not a CA')
        if openssl('x509', '-in', self.paths['ca_certificate'], '-pubkey', '-noout') != openssl(
                'pkey', '-in', self.paths['ca_private_key'], '-passin', 'pass:', '-pubout'):
            raise ValueError('CA certificate and private key differ')
        if self.record.exists():
            if json.loads(private_file(self.record).read_text()) != self.value:
                raise ValueError('Existing CA registration cannot be replaced')

    def register(self):
        path_without_links(self.record.parent).mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.record.exists(): save(self.record, self.value)

    def status(self):
        result = {'caFingerprint': self.ca['sha256'], 'caExpiresAt': self.ca['expiresAt'], 'server': None}
        timer = SYSTEMD_DIRECTORY/'ioalab-tls-renew.timer'
        result['renewalConfigured'] = timer.is_file() and ('# ioalab-deployment:'+self.identity['deploymentId']) in timer.read_text()
        if self.paths['server_certificate'].exists():
            leaf = certificate(self.paths['server_certificate'])
            result['server'] = {'fingerprint': leaf['sha256'], 'expiresAt': leaf['expiresAt'],
                'subjectAltName': openssl('x509', '-in', self.paths['server_certificate'], '-noout', '-ext', 'subjectAltName').decode('ascii').strip()}
        return result

    def valid_leaf(self, host):
        path = self.paths['server_certificate']
        if not path.exists(): return False
        try:
            openssl('verify', '-CAfile', self.paths['ca_certificate'], '-verify_hostname', host, '-purpose', 'sslserver', path)
            private_file(self.paths['server_private_key'])
            return openssl('x509', '-in', path, '-pubkey', '-noout') == openssl(
                'pkey', '-in', self.paths['server_private_key'], '-passin', 'pass:', '-pubout')
        except (RuntimeError, ValueError):
            return False

    def issue(self, host, *, renew=False):
        host = hostname(host)
        now = time.time()
        if not self.ca['notBefore'] <= now or self.ca['notAfter'] < now + 90*86400:
            raise ValueError('CA cannot cover a new 90-day server certificate')
        if self.valid_leaf(host) and certificate(self.paths['server_certificate'])['notAfter'] > now + 30*86400:
            self.register()
            pending = self.record.parent/'pending.json'
            if renew and pending.exists():
                record = json.loads(path_without_links(pending).read_text())
                if record != {'hostname': host, 'fingerprint': certificate(self.paths['server_certificate'])['sha256']}:
                    raise ValueError('Restore the original interrupted certificate activation')
                self.reload_and_verify(host)
                pending.unlink()
            return {'changed': False, **self.status()}
        key = self.paths['server_private_key']; target = self.paths['server_certificate']
        for parent in {key.parent, target.parent}:
            path_without_links(parent).mkdir(mode=0o700, parents=True, exist_ok=True)
        if key.exists():
            private_file(key)
        else:
            # Claim the key exclusively before OpenSSL fills it; retain it on a failed attempt.
            descriptor = os.open(key, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); os.close(descriptor)
            openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', key)
        self.register()
        with tempfile.TemporaryDirectory(prefix='.tls-', dir=target.parent) as temporary:
            temporary = Path(temporary)
            csr = temporary/'request.csr'; candidate = temporary/'server.crt'
            extension = temporary/'extensions.cnf'
            extension.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:'+host+'\n', encoding='ascii')
            openssl('req', '-new', '-key', key, '-subj', '/CN='+host, '-out', csr)
            openssl('x509', '-req', '-in', csr, '-CA', self.paths['ca_certificate'], '-CAkey', self.paths['ca_private_key'],
                    '-passin', 'pass:', '-set_serial', '0x'+secrets.token_hex(16), '-days', '90', '-sha256',
                    '-extfile', extension, '-out', candidate)
            openssl('verify', '-CAfile', self.paths['ca_certificate'], '-verify_hostname', host, '-purpose', 'sslserver', candidate)
            old = target.read_bytes() if target.exists() else None
            pending = self.record.parent/'pending.json'
            if renew:
                save(pending, {'hostname': host, 'fingerprint': certificate(candidate)['sha256']})
            os.chmod(candidate, 0o644)
            os.replace(candidate, target)
            if renew:
                try:
                    self.reload_and_verify(host)
                    pending.unlink()
                except BaseException:
                    if old is not None:
                        candidate.write_bytes(old); os.chmod(candidate, 0o644); os.replace(candidate, target)
                        subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=20)
                        subprocess.run(['systemctl', 'reload', 'nginx'], check=True, capture_output=True, timeout=20)
                        pending.unlink(missing_ok=True)
                    raise
        return {'changed': True, 'activated': renew, **self.status()}

    def reload_and_verify(self, host):
        subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=20)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True, capture_output=True, timeout=20)
        expected = certificate(self.paths['server_certificate'])['sha256']
        context = ssl.create_default_context(cafile=str(self.paths['ca_certificate']))
        # New workers may not serve the first post-reload connection yet.
        for attempt in range(10):
            with socket.create_connection((host, 443), timeout=5) as stream:
                with context.wrap_socket(stream, server_hostname=host) as tls:
                    if hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest() == expected: return
            time.sleep(.2)
        raise RuntimeError('Nginx did not serve the signed server certificate')

    def export(self, public_origin, output):
        public_origin = origin(public_origin); output = path_without_links(output)
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        pem = self.paths['ca_certificate'].read_bytes()
        if b'PRIVATE KEY' in pem: raise ValueError('CA export contains private material')
        (output/'ca.pem').write_bytes(pem)
        save(output/'platform-trust.json', {'format': 'ioalab.platform-trust/1', 'origins': [public_origin],
             'anchors': [{'file': 'ca.pem', 'sha256': self.ca['sha256']}]})
        return {'origin': public_origin, 'caFingerprint': self.ca['sha256'], 'output': str(output)}


def systemctl(*arguments):
    subprocess.run(['systemctl', *arguments], check=True, capture_output=True, timeout=20)


def verify_units(paths):
    subprocess.run(['systemd-analyze', 'verify', *map(str, paths)], check=True, capture_output=True, timeout=20)


def install_renewal(layout):
    import sys
    authority = Authority(layout)
    source = Path(__file__).resolve().parent.parent
    marker = '# ioalab-deployment:'+layout.inspect()['deploymentId']
    service = '\n'.join([marker, '[Unit]', 'Description=Renew IoA internal-CA server certificate',
        'After=network-online.target nginx.service', '[Service]', 'Type=oneshot', 'UMask=0077',
        'WorkingDirectory='+directive_path(source),
        'ExecStart='+command([sys.executable, '-m', 'deploy.tls', 'renew', '--state', layout.state]),
        'TimeoutStartSec=180', ''])
    timer = '\n'.join([marker, '[Unit]', 'Description=Check IoA internal-CA certificate daily',
        '[Timer]', 'OnCalendar=daily', 'RandomizedDelaySec=1800', 'Persistent=true',
        '[Install]', 'WantedBy=timers.target', ''])
    for name, content in [('ioalab-tls-renew.service', service), ('ioalab-tls-renew.timer', timer)]:
        path = path_without_links(SYSTEMD_DIRECTORY/name)
        if path.exists() and not path.read_text().startswith(marker+'\n'):
            raise ValueError('Renewal unit belongs to another deployment')
    with tempfile.TemporaryDirectory(prefix='.ioalab-tls-', dir=SYSTEMD_DIRECTORY) as temporary:
        candidates = []
        for name, content in [('ioalab-tls-renew.service', service), ('ioalab-tls-renew.timer', timer)]:
            path = Path(temporary)/name
            path.write_text(content, encoding='utf-8'); os.chmod(path, 0o644); candidates.append(path)
        verify_units(candidates)
        for path in candidates:
            os.replace(path, SYSTEMD_DIRECTORY/path.name)
    systemctl('daemon-reload')
    systemctl('enable', '--now', 'ioalab-tls-renew.timer')
    return {'renewalConfigured': True, 'source': str(source), 'caFingerprint': authority.ca['sha256']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'issue', 'renew', 'export-ca', 'install-renewal'):
        item = commands.add_parser(name); item.add_argument('--state', type=Path, required=True)
        if name == 'issue':
            item.add_argument('--authority', type=Path, required=True); item.add_argument('--hostname', required=True)
        elif name == 'export-ca':
            item.add_argument('--origin', required=True); item.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require_host(); layout = existing(args.state)
        with writer(layout.private/'tls.lock'):
            if args.command == 'install-renewal':
                result = install_renewal(layout)
            elif args.command == 'status' and not (layout.config/'tls/authority.json').exists():
                result = {'configured': False}
            else:
                authority = Authority(layout, getattr(args, 'authority', None))
                if args.command == 'status': result = authority.status()
                elif args.command == 'issue': result = authority.issue(args.hostname)
                elif args.command == 'export-ca': result = authority.export(args.origin, args.output)
                else:
                    public = json.loads((layout.config/'core/platform.json').read_text())['runtime']['public_url']
                    result = authority.issue(urlsplit(origin(public)).hostname, renew=True)
        print(json.dumps({'status':'completed','data':result,'error':None}, ensure_ascii=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'status':'rejected','data':{},'error':{'code':type(error).__name__,
              'message':str(error) if isinstance(error, (ValueError, RuntimeError)) else 'Certificate maintenance failed; inspect the original operation'}}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
