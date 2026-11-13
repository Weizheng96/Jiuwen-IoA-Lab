"""Optional trusted-user port. Never register this controller in model RPC."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import threading

from .confirmation_view import present
from .protocol import RuntimeFault


class TrustedUserPort:
    def __init__(self, confirmations, *, actor, generation, authorize):
        if not isinstance(actor,str) or not actor or not isinstance(generation,str) or not generation or not callable(authorize):
            raise ValueError('A fixed actor, generation and trusted authorization callback are required')
        self.confirmations,self.actor,self.generation,self.authorize=confirmations,actor,generation,authorize
        self._authority=confirmations.user_channel()
        self._lock=threading.RLock()
        self._closed=False

    def close(self):
        with self._lock:self._closed=True

    def _check(self,context):
        if self._closed or self.authorize(context,self.generation) is not True:
            raise RuntimeFault('forbidden','Trusted user access is no longer valid')

    def _get(self,reference):
        item=self.confirmations.get(reference,detail=True)
        if item['agentId']!=self.actor:raise RuntimeFault('forbidden','Confirmation belongs to another actor')
        return present(item)

    def list(self,context):
        with self._lock:
            self._check(context)
            return [present(item) for item in self.confirmations.list() if item['agentId']==self.actor]

    def get(self,context,reference):
        with self._lock:
            self._check(context)
            return self._get(reference)

    def decide(self,context,reference,binding_digest,decision,*,event_id,answers=None):
        with self._lock:
            self._check(context)
            self._get(reference)
            if not isinstance(event_id,str) or not 1<=len(event_id)<=256:
                raise RuntimeFault('invalid_input','A trusted user event identifier is required')
            return self.confirmations.decide(reference,binding_digest,decision,authority=self._authority,
                source='trusted_user:'+self.generation+':'+event_id,answers=answers)


class TrustedUserServer:
    """Loopback-only, fixed-generation transport for a separately authenticated gateway.

    The key and port are passed to the trusted gateway by deployment, never model
    RPC. The gateway owns browser authentication/CSRF and supplies a real event ID.
    Closing the controller revokes requests already accepted by the listener.
    """
    def __init__(self,port,*,key,listen_port=0):
        if not isinstance(key,str) or len(key)<32:raise ValueError('Use a high entropy transport key')
        self.port,self.key=port,key
        controller=self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)
            def log_message(self,*args):pass
            def do_POST(self):
                body_read=False
                try:
                    if self.headers.get('Origin') is not None:
                        raise RuntimeFault('forbidden','Browser requests must use the authenticated gateway')
                    if not hmac.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+controller.key).encode()):
                        raise RuntimeFault('forbidden','Trusted transport authentication is required')
                    if self.path!='/confirmations' or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type')!='application/json':
                        raise RuntimeFault('forbidden','Unknown trusted user endpoint')
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=128*1024:raise RuntimeFault('invalid_input','Invalid trusted request size')
                    raw=self.rfile.read(size);body_read=True
                    body=json.loads(raw)
                    if not isinstance(body,dict) or set(body)-{'generation','method','reference','bindingDigest','decision','answers','eventId'}:
                        raise RuntimeFault('invalid_input','Unknown trusted request fields')
                    if body.get('generation')!=controller.port.generation:
                        raise RuntimeFault('forbidden','Old user generation')
                    method=body.get('method');context={'generation':body['generation']}
                    common={'generation','method'}
                    if method=='list' and set(body)==common:result=controller.port.list(context)
                    elif method=='get' and set(body)==common|{'reference'}:result=controller.port.get(context,body['reference'])
                    elif method=='decide' and common|{'reference','bindingDigest','decision','eventId'}<=set(body):
                        result=controller.port.decide(context,body['reference'],body['bindingDigest'],body['decision'],event_id=body['eventId'],answers=body.get('answers'))
                    else:raise RuntimeFault('invalid_input','Unknown trusted user method')
                    self.reply(200,{'data':result})
                except RuntimeFault as exc:
                    # Drain only a bounded fixed-length body, without decoding or
                    # dispatching it. Closing with unread bytes can reset TCP and
                    # hide the rejection response on Windows gateways.
                    if not body_read and not self.headers.get('Transfer-Encoding'):
                        try:
                            size=int(self.headers.get('Content-Length','0'))
                            if 0<size<=128*1024:self.rfile.read(size)
                        except (ValueError,TimeoutError,OSError):pass
                    self.reply(403 if exc.code=='forbidden' else 409,{'error':{'code':exc.code,'message':str(exc)}})
                except (ValueError,TypeError,KeyError,RecursionError):self.reply(400,{'error':{'code':'invalid_input','message':'Invalid trusted request'}})
                except (TimeoutError,OSError):self.close_connection=True
            def reply(self,status,value):
                data=json.dumps(value,ensure_ascii=True).encode()
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        self.server=ThreadingHTTPServer(('127.0.0.1',listen_port),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)

    @property
    def address(self):return 'http://127.0.0.1:'+str(self.server.server_port)+'/confirmations'

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.port.close()
        if self.thread.is_alive():self.server.shutdown();self.thread.join(timeout=5)
        self.server.server_close()
