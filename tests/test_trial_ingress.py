"""Real bounded TCP/HTTP/WS peers; dummy targets never touch formal data."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
from websockets.sync.client import connect
from websockets.sync.server import serve
from ioalab_trial import ingress


def until(predicate,seconds=2):
    end=time.monotonic()+seconds
    while not predicate():
        assert time.monotonic()<end
        time.sleep(.01)


@contextmanager
def relay(dial):
    value=ingress.Ingress(host='127.0.0.1',ports=(0,0),dial=dial)
    thread=threading.Thread(target=value.serve);thread.start()
    try:yield value,[listener.getsockname()[1] for listener in value.listeners]
    finally:value.stop();thread.join(3);assert not thread.is_alive() and not value.workers


def test_fixed_http_ws_targets_never_interpret_connect_destination():
    methods=[];dialled=[]
    class Http(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            methods.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'owned-http')
        def do_CONNECT(self):
            methods.append(self.path);self.send_response(405);self.end_headers()
    http=ThreadingHTTPServer(('127.0.0.1',0),Http)
    thread=threading.Thread(target=http.serve_forever);thread.start()
    with serve(lambda ws:ws.send(ws.recv()),'127.0.0.1',0) as ws:
        worker=threading.Thread(target=ws.serve_forever);worker.start()
        endpoints={8080:http.server_port,8767:ws.socket.getsockname()[1]}
        def dial(target,timeout):
            dialled.append(target);assert target[0]=='seat' and target[1] in endpoints
            return socket.create_connection(('127.0.0.1',endpoints[target[1]]),timeout)
        try:
            with relay(dial) as (_,ports):
                assert httpx.get(f'http://127.0.0.1:{ports[0]}/owned',trust_env=False).content==b'owned-http'
                with connect(f'ws://127.0.0.1:{ports[1]}',proxy=None) as channel:
                    channel.send(b'owned-websocket');assert channel.recv()==b'owned-websocket'
                with socket.create_connection(('127.0.0.1',ports[0])) as channel:
                    channel.sendall(b'CONNECT outside.invalid:443 HTTP/1.0\r\n\r\n')
                    assert b'405' in channel.recv(1024)
            assert methods==['/owned','outside.invalid:443']
            assert set(dialled)=={('seat',8080),('seat',8767)}
        finally:ws.shutdown();worker.join();http.shutdown();http.server_close();thread.join()


def test_both_listeners_share_32_slots_idle_timeout_and_active_stop(monkeypatch):
    peers=[];dialled=[]
    def dial(target,timeout):
        left,right=socket.socketpair();peers.append(right);dialled.append(target);return left
    clients=[]
    with relay(dial) as (instance,ports):
        try:
            for index in range(32):
                client=socket.create_connection(('127.0.0.1',ports[index%2]));clients.append(client)
                until(lambda:len(dialled)==index+1)
            overflow=socket.create_connection(('127.0.0.1',ports[0]));overflow.settimeout(1)
            assert overflow.recv(1)==b'';overflow.close()
            assert len(dialled)==32 and {port for _,port in dialled}=={8080,8767}
            monkeypatch.setattr(ingress,'IDLE_TIMEOUT',.05)
            until(lambda:not instance.workers)
            client=socket.create_connection(('127.0.0.1',ports[1]));clients.append(client)
            until(lambda:len(dialled)==33)
            instance.stop();until(lambda:not instance.workers)
            client.settimeout(1);assert client.recv(1)==b''
        finally:
            for client in clients:client.close()
            for peer in peers:peer.close()


def test_pinned_numeric_address_has_no_dns_or_configurable_target(tmp_path,monkeypatch):
    hosts=tmp_path/'hosts';hosts.write_text('127.0.0.1 localhost\n172.18.0.1 seat\n')
    observed=[]
    class Peer:
        def settimeout(self,value):observed.append(('timeout',value))
        def connect(self,target):observed.append(('connect',target))
        def close(self):pass
    monkeypatch.setattr(ingress.socket,'socket',lambda family,kind:Peer())
    monkeypatch.setattr(ingress.socket,'getaddrinfo',lambda *a:pytest.fail('Fixed numeric connect must not resolve DNS'))
    dial=ingress.seat_connector(hosts)
    dial(('seat',8080),5)
    assert observed==[('timeout',5),('connect',('172.18.0.1',8080))]
    with pytest.raises(ValueError):dial(('outside.invalid',443),5)
    for bad in ('::1 seat\n','172.18.0.1 seat\n172.18.0.2 seat\n','127.0.0.1 seat\n',''):
        hosts.write_text(bad)
        with pytest.raises(ValueError):ingress.seat_connector(hosts)


@pytest.mark.skipif(sys.platform=='win32',reason='SIGTERM requires POSIX')
def test_sigterm_during_connect_and_blocked_write_closes_clients_and_exits():
    # Controlled child code exercises real SIGTERM without sending the signal to pytest.
    program=r'''
import signal,socket,sys,threading,time
from ioalab_trial import ingress
mode=sys.argv[1]
class Wrapped:
 def __init__(self,sock):self.sock=sock
 def __getattr__(self,key):return getattr(self.sock,key)
 def sendall(self,data):
  print('writing',flush=True);self.sock.sendall(data)
def dial(target,timeout):
 print('connecting',flush=True)
 if mode=='connect':time.sleep(.3);raise TimeoutError()
 left,right=socket.socketpair();right.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
 left.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024);held.append(right);return Wrapped(left)
held=[]
relay=ingress.Ingress(host='127.0.0.1',ports=(0,0),dial=dial)
signal.signal(signal.SIGTERM,lambda *_:relay.stop())
print(next(iter(relay.listeners)).getsockname()[1],flush=True)
relay.serve()
for peer in held:peer.close()
print('closed',flush=True)
'''
    for phase in ('connect','write'):
        child=subprocess.Popen([sys.executable,'-u','-c',program,phase],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            env={**os.environ,'PYTHONPATH':str(Path(ingress.__file__).resolve().parents[1])})
        client=None
        try:
            ready=child.stdout.readline();assert ready,child.stderr.read()
            port=int(ready);client=socket.create_connection(('127.0.0.1',port))
            assert child.stdout.readline().strip()=='connecting'
            if phase=='write':
                client.sendall(b'x'*65536)
                assert child.stdout.readline().strip()=='writing'
                time.sleep(.05)  # Leave the real small socket buffers backpressured.
            child.terminate();out,err=child.communicate(timeout=4)
            assert child.returncode==0 and out.strip().endswith('closed') and err==''
            client.settimeout(1);assert client.recv(1)==b''
        finally:
            if client:client.close()
            if child.poll() is None:child.kill();child.wait()
