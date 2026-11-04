"""Real local HTTP sidecar with explicit upstream transport; no provider calls."""
import threading

import httpx
import pytest

from ioalab_trial.model_egress import ModelServer,provider_base


@pytest.fixture
def proxy():
    requests=[]
    def upstream(request):
        requests.append(request)
        return httpx.Response(200,headers={'Content-Type':'text/event-stream','X-Private':'provider-secret'},
                              content=b'data: first\n\ndata: [DONE]\n\n')
    server=ModelServer(provider_base_url='https://provider.test/compatible-mode/v1',provider_key='provider-secret',
                      epoch_token='epoch-a-'+'a'*40,epoch='epoch-a',listen=('127.0.0.1',0),transport=httpx.MockTransport(upstream)).start()
    url='http://127.0.0.1:'+str(server.server.server_port)
    with httpx.Client(base_url=url,trust_env=False,timeout=3) as client:
        yield server,client,requests
    server.close()


def token():return {'Authorization':'Bearer '+'epoch-a-'+'a'*40}


def test_fixed_compatible_base_stream_and_header_replacement(proxy):
    _,client,observed=proxy
    response=client.post('/v1/chat/completions',json={'model':'example','stream':True,'messages':[]},
                         headers={**token(),'X-Target':'https://evil.test','Host':'evil.test'})
    assert response.status_code==200 and b'data: first' in response.content and b'[DONE]' in response.content
    assert str(observed[0].url)=='https://provider.test/compatible-mode/v1/chat/completions'
    assert observed[0].headers['Authorization']=='Bearer provider-secret'
    assert observed[0].headers['Host']=='provider.test'
    assert 'X-Target' not in observed[0].headers
    assert 'X-Private' not in response.headers and b'provider-secret' not in response.content


def test_embeddings_and_model_catalog_use_only_fixed_suffixes(proxy):
    _,client,observed=proxy
    assert client.post('/v1/embeddings',json={'model':'example','input':'test'},headers=token()).status_code==200
    assert client.get('/v1/models',headers=token()).status_code==200
    assert [str(request.url) for request in observed]==[
        'https://provider.test/compatible-mode/v1/embeddings','https://provider.test/compatible-mode/v1/models']


@pytest.mark.parametrize('method,path',[
    ('CONNECT','/provider.test:443'),('POST','http://evil.test/v1/chat/completions'),
    ('POST','/v1/chat/completions?url=https://evil.test'),('POST','/v1/../admin'),('POST','/v1/responses'),
    ('GET','/arbitrary'),
])
def test_non_allowlisted_requests_never_reach_provider(proxy,method,path):
    _,client,observed=proxy
    # An absolute URL would direct an ordinary httpx client away from the local
    # server. Send it as the actual HTTP request target over a local socket.
    if path.startswith('http:'):
        import socket
        with socket.create_connection(('127.0.0.1',client.base_url.port),timeout=3) as connection:
            request=(f'POST {path} HTTP/1.0\r\nHost: provider.test\r\nAuthorization: {token()["Authorization"]}\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{{}}').encode()
            connection.sendall(request);response=connection.recv(4096)
            assert b'403' in response.split(b'\r\n',1)[0]
    else:
        assert client.request(method,path,headers=token(),content=b'{}').status_code in (403,501)
    assert observed==[]


def test_epoch_auth_and_request_size_are_enforced(proxy):
    _,client,observed=proxy
    assert client.get('/health').status_code==403
    assert client.get('/health',headers={'Authorization':'Bearer '+'epoch-b-'+'b'*40}).status_code==403
    assert client.get('/health',headers=token()).json()=={'epoch':'epoch-a','ready':True}
    import socket
    with socket.create_connection(('127.0.0.1',client.base_url.port),timeout=3) as connection:
        request=(f'POST /v1/chat/completions HTTP/1.0\r\nHost: model-egress\r\nAuthorization: {token()["Authorization"]}\r\nContent-Length: 2097153\r\nContent-Type: application/json\r\n\r\n').encode()
        connection.sendall(request)
        assert b'413' in connection.recv(4096).split(b'\r\n',1)[0]
    assert observed==[]


def test_rejected_body_is_drained_and_consumed_json_is_not_read_twice(proxy):
    _,client,observed=proxy
    # First branch rejects the route before consuming its two-byte body.
    response=client.post('/v1/chat/completions?url=https://evil.test',json={},headers=token())
    assert response.status_code==403 and response.headers['Connection']=='close'
    # Second branch has already consumed its body before detecting invalid JSON.
    response=client.post('/v1/chat/completions',content=b'{',headers={**token(),'Content-Type':'application/json'})
    assert response.status_code==400 and response.headers['Connection']=='close'
    assert observed==[]


def test_vendor_redirect_does_not_follow_or_leak_details(proxy):
    server,client,observed=proxy
    def redirect(request):
        observed.append(request)
        return httpx.Response(302,headers={'Location':'https://evil.test/provider-secret'},content=b'provider-secret')
    server.client.close()
    server.client=httpx.Client(transport=httpx.MockTransport(redirect),trust_env=False,follow_redirects=False)
    response=client.post('/v1/chat/completions',json={},headers=token())
    assert response.status_code==502 and response.content==b''
    assert 'Location' not in response.headers and len(observed)==1


def test_revocation_stops_active_stream_before_later_chunks():
    release=threading.Event()
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: first\n\n'
            release.wait(3)
            yield b'data: forbidden-after-revoke\n\n'
        def close(self):release.set()
    server=ModelServer(provider_base_url='https://provider.test/v1',provider_key='private',epoch_token='e'*40,
        epoch='e',listen=('127.0.0.1',0),transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Stream()))).start()
    try:
        with httpx.Client(trust_env=False,timeout=3) as client:
            with client.stream('POST','http://127.0.0.1:'+str(server.server.server_port)+'/v1/chat/completions',
                json={},headers={'Authorization':'Bearer '+'e'*40}) as response:
                chunks=response.iter_raw()
                assert b'data: first' in next(chunks)
                server.close()
                assert b'forbidden-after-revoke' not in b''.join(chunks)
    finally:
        release.set()
        if not server.closed.is_set():server.close()


@pytest.mark.parametrize('value',['http://provider.test/v1','https://user:pass@provider.test/v1',
    'https://provider.test/v1?target=other','https://provider.test/v1#fragment','https://provider.test/a/../v1'])
def test_provider_base_rejects_mutable_or_credential_urls(value):
    with pytest.raises(ValueError):provider_base(value)
