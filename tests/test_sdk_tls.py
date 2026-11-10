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
from ioalab_sdk.runtime import AgentConnection, RuntimeStore


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
