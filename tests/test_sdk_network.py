"""Shared network lifetime and public credentials at the actual HTTP boundary."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import pytest
from a2c.binding import A2C_EXTENSION,to_parts
from a2a.types import Message,Role,SendMessageResponse
from google.protobuf import json_format
from ioalab_sdk.adapters import PublicTransport
from ioalab_sdk.runtime.network import Network
from ioalab_sdk.runtime import AgentConnection,RuntimeStore,RuntimeFault
from ioalab_sdk.assistant.trading import Trading
from ioalab_sdk.provider.execution import ProviderExecution


@pytest.fixture
def network_server():
    seen=[];entered=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path,dict(self.headers)))
            if self.path=='/slow':entered.set();assert release.wait(5)
            value={'keys':[]} if self.path.endswith('.json') else {}
            if self.path.endswith('agent-card.json'):
                value={'capabilities':{'extensions':[{'uri':A2C_EXTENSION,'params':{'jwksUri':base+'/jwks.json'}}]},
                       'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':base+'/rpc'}]}
            self.send_response(302 if self.path=='/redirect' else 200)
            self.send_header('Set-Cookie','private=secret; Path=/')
            self.send_header('Location',base+'/not-followed')
            self.end_headers();self.wfile.write(json.dumps(value).encode())
        def do_POST(self):
            seen.append((self.path,dict(self.headers)))
            rpc=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert rpc['method']=='SendMessage'
            assert self.headers['A2A-Version']=='1.0'
            result=SendMessageResponse(message=Message(role=Role.ROLE_AGENT,message_id='reply',parts=to_parts({'op':'template.get','templates':[]})))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'jsonrpc':'2.0','id':rpc['id'],'result':json_format.MessageToDict(result)}).encode())
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);base='http://127.0.0.1:'+str(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    network=Network(base)
    try:yield network,seen,entered,release
    finally:release.set();network.close();server.shutdown();server.server_close();thread.join(5)


def test_public_original_a2a_template_and_keys_have_no_account_credentials_or_cookies(network_server):
    network,seen,_,_=network_server;public=PublicTransport(network)
    # A previous authenticated response must never turn a public read into a cookie-authenticated request.
    network.http.get(network.base+'/account',headers={'Authorization':'Bearer fixture-token'})
    assert public.jwks(network.base+'/agent')=={'keys':[]}
    assert public.send(network.base+'/agent','template.get',{}).body['templates']==[]
    for path,headers in seen[1:]:
        assert 'Authorization' not in headers and 'Cookie' not in headers
    response=network.http.get(network.base+'/redirect')
    assert response.status_code==302 and not any(path=='/not-followed' for path,_ in seen)
    with pytest.raises(RuntimeFault):public.jwks('https://outside.invalid/jwks.json')
    with pytest.raises(RuntimeFault):public.send(network.base,'work.start',{})
    with pytest.raises(RuntimeFault):public.send(network.base,'template.get',{'op':'work.start'})
    assert public.jwks(network.base+'/agent/.well-known/agent-card.json')=={'keys':[]}


def test_two_roles_borrow_one_pool_and_closing_delivery_does_not_close_owner(network_server,tmp_path):
    network,_,_,_=network_server
    runtime=SimpleNamespace(account=SimpleNamespace(network=network),store=None,
        config=SimpleNamespace(provider={'services':{}},identities=[]))
    assistant=Trading(SimpleNamespace(runtime=runtime,identity=SimpleNamespace(agent_id='fixture')))
    provider=ProviderExecution(SimpleNamespace(identities={}),runtime,None)
    assert assistant.public.network is provider.public.network is network
    store=RuntimeStore(tmp_path/'runtime.sqlite',network.base,['actor'],owner_id='test')
    connection=AgentConnection(network.base,'token',['actor'],store,network=network)
    try:
        assert connection.http is network.http
        assistant.close();provider.close();connection.close()
        assert assistant.public.jwks(network.base+'/jwks.json')=={'keys':[]}
    finally:store.close()


def test_slow_request_does_not_serialize_other_shared_pool_reads(network_server):
    network,_,entered,release=network_server
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow=pool.submit(network.http.get,network.base+'/slow')
        try:
            assert entered.wait(3)
            assert pool.submit(PublicTransport(network).jwks,network.base+'/jwks.json').result(timeout=2)=={'keys':[]}
        finally:release.set()
        assert slow.result(timeout=3).status_code==200


def test_same_origin_normalizes_default_ports_and_rejects_embedded_credentials():
    with Network('https://example.test') as network:
        assert network.public_url('https://example.test:443/jwks.json').endswith('/jwks.json')
        with pytest.raises(RuntimeFault):network.public_url('https://@example.test/jwks.json')
        with pytest.raises(RuntimeFault):network.public_url('https://example.test:444/jwks.json')
