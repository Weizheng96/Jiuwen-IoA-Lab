"""Real HTTPS at the account and delivery transport boundaries."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import ssl
import threading
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import pytest

from ioalab_sdk.runtime.session import AccountSession
from ioalab_sdk.runtime import AgentConnection, RuntimeStore, RuntimeFault


@pytest.fixture
def https_platform(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'SDK TLS test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    ca = tmp_path/'ca.pem'; ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private = tmp_path/'key.pem'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({'keys': []} if self.path == '/jwks.json' else {'publicUrl': 'https://127.0.0.1'}).encode()
            self.send_response(200 if self.path in ('/api/config', '/jwks.json') else 401)
            self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.send_response(200)
            self.send_header('Set-Cookie', '__Host-ioalab_session=tls-test-session; Secure; Path=/; HttpOnly')
            self.end_headers(); self.wfile.write(b'{}')
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(ca, private)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield ca, server.server_port
    server.shutdown(); server.server_close(); thread.join(5)


@pytest.mark.parametrize('transport', ['account', 'delivery', 'official-admin', 'provider-a2c', 'assistant-a2c'])
@pytest.mark.parametrize('trust', ['trusted', 'unknown', 'wrong_ip'])
def test_sdk_uses_os_trust_and_still_rejects_unknown_ca_and_wrong_ip(tmp_path, monkeypatch, https_platform, transport, trust):
    ca, port = https_platform
    if trust != 'unknown': monkeypatch.setenv('SSL_CERT_FILE', str(ca))
    else: monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    host = '127.0.0.2' if trust == 'wrong_ip' else '127.0.0.1'
    base = f'https://{host}:{port}'
    if transport == 'account':
        if trust == 'trusted':
            account = AccountSession(base, 'test', '', tmp_path/'account', connect=False)
            account.close()
        else:
            with pytest.raises(httpx.ConnectError):
                AccountSession(base, 'test', '', tmp_path/'account', connect=False)
    elif transport == 'official-admin':
        from ioalab_services import administrator_session
        deployment = SimpleNamespace(value={'platform_url': base})
        if trust == 'trusted':
            assert administrator_session(deployment, 'admin', 'fixture-password') == 'tls-test-session'
        else:
            with pytest.raises(httpx.ConnectError):
                administrator_session(deployment, 'admin', 'fixture-password')
    elif transport in ('provider-a2c', 'assistant-a2c'):
        from ioalab_sdk.runtime.network import Network
        network = Network(base)
        if transport == 'provider-a2c':
            from ioalab_sdk.provider.execution import ProviderExecution
            runtime = SimpleNamespace(config=SimpleNamespace(provider={'services': {}}, identities=[]),account=SimpleNamespace(network=network))
            public = ProviderExecution(SimpleNamespace(identities={}), runtime, None).public
        else:
            from ioalab_sdk.assistant.trading import Trading
            public = Trading(SimpleNamespace(runtime=SimpleNamespace(store=None,account=SimpleNamespace(network=network)),
                                             identity=SimpleNamespace(agent_id='fixture'))).public
        try:
            if trust == 'trusted': assert public.jwks(base+'/jwks.json') == {'keys': []}
            else:
                with pytest.raises(httpx.ConnectError): public.jwks(base+'/jwks.json')
        finally: network.close()
    else:
        store = RuntimeStore(tmp_path/'runtime.sqlite', base, ['actor'], owner_id='test')
        connection = AgentConnection(base, 'token', ['actor'], store)
        try:
            with pytest.raises(httpx.HTTPStatusError if trust == 'trusted' else httpx.ConnectError) as error:
                connection.recover_interaction('actor', 'original')
            if trust == 'trusted': assert error.value.response.status_code == 401
        finally:
            connection.close(); store.close()


@pytest.mark.parametrize('trust',['trusted','unknown','wrong_ip'])
def test_resident_wss_uses_the_same_system_ca_and_hostname_checks(tmp_path,monkeypatch,https_platform,trust):
    from websockets.sync.server import serve
    from ioalab_sdk.runtime import RuntimeFault
    ca,_=https_platform
    if trust!='unknown':monkeypatch.setenv('SSL_CERT_FILE',str(ca))
    else:monkeypatch.delenv('SSL_CERT_FILE',raising=False)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(ca,ca.parent/'key.pem')
    authenticated=[]
    def handler(ws):
        authenticated.append(json.loads(ws.recv(timeout=3)))
        ws.send(json.dumps({'type':'connection.error','error':{'code':'not_ready','message':'TLS authentication frame received','recovery':'retry'}}))
    server=serve(handler,'0.0.0.0',0,ssl=context)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    host='127.0.0.2' if trust=='wrong_ip' else '127.0.0.1'
    base=f'https://{host}:{server.socket.getsockname()[1]}'
    store=RuntimeStore(tmp_path/'wss.sqlite',base,['actor'],owner_id='test')
    connection=AgentConnection(base,'test',['actor'],store)
    # Isolate the HTTPS ticket boundary so rejection proves the real WSS handshake.
    monkeypatch.setattr(connection.http,'post',lambda *a,**k:SimpleNamespace(status_code=200,json=lambda:{'ticket':'fixture'}))
    try:
        with pytest.raises(RuntimeFault if trust=='trusted' else ssl.SSLCertVerificationError):connection._session()
        if trust=='trusted':assert authenticated[0]['type']=='connection.auth'
        else:assert authenticated==[]
    finally:connection.close();store.close();server.shutdown();worker.join(5)


def test_failed_account_initialization_closes_its_network_pool(tmp_path,monkeypatch,https_platform):
    import ioalab_sdk.runtime.session as session
    ca,port=https_platform
    monkeypatch.delenv('SSL_CERT_FILE',raising=False)
    original=session.Network;created=[]
    def record(base):
        network=original(base);created.append(network);return network
    monkeypatch.setattr(session,'Network',record)
    with pytest.raises(httpx.ConnectError):AccountSession(f'https://127.0.0.1:{port}','test','',tmp_path/'account',connect=False)
    assert len(created)==1 and created[0].http.is_closed


def bundled_policy(tmp_path, monkeypatch, certificate, origins):
    import hashlib
    import importlib.resources
    root = tmp_path/'bundled'/'ioalab_sdk'/'runtime'; directory = root/'trust'; directory.mkdir(parents=True)
    raw = certificate.read_bytes(); (directory/'ca.pem').write_bytes(raw)
    cert = x509.load_pem_x509_certificate(raw)
    policy = {'format': 'ioalab.platform-trust/1', 'origins': origins,
              'anchors': [{'file': 'ca.pem', 'sha256': cert.fingerprint(hashes.SHA256()).hex()}]}
    (directory/'platform-trust.json').write_text(json.dumps(policy), encoding='utf-8')
    original = importlib.resources.files
    monkeypatch.setattr(importlib.resources, 'files', lambda package: root if package == 'ioalab_sdk.runtime' else original(package))
    return directory


def test_packaged_ca_authenticates_only_its_exact_platform_origin(tmp_path, monkeypatch, https_platform):
    from ioalab_sdk.runtime.network import Network
    ca, port = https_platform
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    base = f'https://127.0.0.1:{port}'
    bundled_policy(tmp_path, monkeypatch, ca, [base])
    with Network(base) as network:
        assert network.http.get(base+'/api/config').status_code == 200
        assert network.trust['source'] == 'bundled'
        with pytest.raises(RuntimeFault):
            network.http.get(f'https://127.0.0.2:{port}/api/config')
    with Network(f'https://127.0.0.2:{port}') as network:
        assert network.trust['source'] == 'system'
        with pytest.raises(httpx.ConnectError):
            network.http.get(f'https://127.0.0.2:{port}/api/config')


@pytest.mark.parametrize('damage', ['missing_policy', 'bad_digest', 'missing_ca', 'private_key', 'traversal'])
def test_damaged_packaged_trust_is_rejected_before_request(tmp_path, monkeypatch, https_platform, damage):
    from ioalab_sdk.runtime.network import Network
    ca, port = https_platform; base = f'https://127.0.0.1:{port}'
    directory = bundled_policy(tmp_path, monkeypatch, ca, [base])
    policy = json.loads((directory/'platform-trust.json').read_text())
    if damage == 'missing_policy': (directory/'platform-trust.json').unlink()
    elif damage == 'missing_ca': (directory/'ca.pem').unlink()
    elif damage == 'private_key': (directory/'ca.pem').write_bytes((ca.parent/'key.pem').read_bytes())
    else:
        if damage == 'bad_digest': policy['anchors'][0]['sha256'] = '0'*64
        else: policy['anchors'][0]['file'] = '../ca.pem'
        (directory/'platform-trust.json').write_text(json.dumps(policy))
    with pytest.raises(RuntimeFault) as error: Network(base)
    assert error.value.code == 'not_ready'


@pytest.mark.parametrize('inactive', ['expired', 'future'])
def test_public_chain_still_works_when_bundled_ca_is_inactive(tmp_path, monkeypatch, https_platform, inactive):
    from ioalab_sdk.runtime.network import Network
    ca, port = https_platform; base = f'https://127.0.0.1:{port}'
    # This HTTPS service models a chain already trusted by the client's system.
    monkeypatch.setenv('SSL_CERT_FILE', str(ca))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'retired internal CA')])
    now = datetime.now(timezone.utc)
    begin, end = ((now-timedelta(days=2), now-timedelta(days=1)) if inactive == 'expired'
                  else (now+timedelta(days=1), now+timedelta(days=2)))
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(begin).not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    anchor = tmp_path/'inactive.pem'; anchor.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    bundled_policy(tmp_path, monkeypatch, anchor, [base])
    with Network(base) as network:
        assert network.http.get(base+'/api/config').status_code == 200
        assert network.trust['source'] == 'system'
        assert network.trust['inactiveCaFingerprints'] == [cert.fingerprint(hashes.SHA256()).hex()]
    monkeypatch.delenv('SSL_CERT_FILE')
    with Network(base) as network:
        with pytest.raises(httpx.ConnectError): network.http.get(base+'/api/config')


def test_bundled_ca_does_not_disable_server_hostname_verification(tmp_path, monkeypatch, https_platform):
    from ioalab_sdk.runtime.network import Network
    ca, port = https_platform; base = f'https://127.0.0.2:{port}'
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    bundled_policy(tmp_path, monkeypatch, ca, [base])
    with Network(base) as network:
        with pytest.raises(httpx.ConnectError): network.http.get(base+'/api/config')


def test_wss_authenticates_with_packaged_ca_without_system_install(tmp_path, monkeypatch, https_platform):
    from websockets.sync.server import serve
    ca, _ = https_platform; monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(ca, ca.parent/'key.pem')
    seen = []
    def handler(ws):
        seen.append(json.loads(ws.recv(timeout=3)))
        ws.send(json.dumps({'type':'connection.error','error':{'code':'not_ready','message':'TLS passed','recovery':'retry'}}))
    server = serve(handler, '127.0.0.1', 0, ssl=context)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    base = f'https://127.0.0.1:{server.socket.getsockname()[1]}'
    bundled_policy(tmp_path, monkeypatch, ca, [base])
    store = RuntimeStore(tmp_path/'bundled-wss.sqlite', base, ['actor'], owner_id='test')
    connection = AgentConnection(base, 'test', ['actor'], store)
    monkeypatch.setattr(connection.http, 'post', lambda *a, **k: SimpleNamespace(status_code=200, json=lambda:{'ticket':'fixture'}))
    try:
        with pytest.raises(RuntimeFault): connection._session()
        assert seen[0]['type'] == 'connection.auth'
        assert connection.network.trust['source'] == 'bundled'
    finally:
        connection.close(); store.close(); server.shutdown(); worker.join(5)


@pytest.mark.parametrize('link_location', ['environment', 'trust_directory', 'certificate'])
def test_verified_environment_alias_is_allowed_but_linked_trust_is_not(tmp_path, monkeypatch, https_platform, link_location):
    import importlib.resources
    from ioalab_sdk.runtime.network import Network
    ca, port = https_platform; base = f'https://127.0.0.1:{port}'
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    directory = bundled_policy(tmp_path, monkeypatch, ca, [base])
    try:
        if link_location == 'environment':
            alias = tmp_path/'current'; alias.symlink_to(tmp_path/'bundled', target_is_directory=True)
            monkeypatch.setattr(importlib.resources, 'files', lambda package: alias/'ioalab_sdk/runtime')
        elif link_location == 'trust_directory':
            real = directory.with_name('real-trust'); directory.rename(real)
            directory.symlink_to(real, target_is_directory=True)
        else:
            real = directory/'original.pem'; (directory/'ca.pem').rename(real)
            (directory/'ca.pem').symlink_to(real)
    except OSError:
        pytest.skip('No symlink privilege; run Linux verification')
    if link_location == 'environment':
        with Network(base) as network:
            assert network.http.get(base+'/api/config').status_code == 200
    else:
        with pytest.raises(RuntimeFault): Network(base)
