"""Read-only original A2A protocol over the resident synchronous HTTP pool."""
import uuid
from google.protobuf import json_format
from a2a.types import Message, Role, SendMessageRequest, SendMessageResponse
from a2c.binding import A2C_EXTENSION,CARD_PATH,card_url,to_parts,from_parts
from a2c.common import Reply
from a2c.errors import A2CError,INVALID_OBJECT
from ..runtime.protocol import RuntimeFault


class PublicTransport:
    def __init__(self,network):self.network=network

    def _read(self,url):
        response=self.network.http.get(self.network.public_url(url),timeout=20)
        response.raise_for_status()
        return response.json()

    def jwks(self,party):
        if party.endswith('.json') and not party.endswith(CARD_PATH):return self._read(party)
        card=self._read(card_url(party))
        for ext in card.get('capabilities',{}).get('extensions',[]):
            if ext.get('uri')==A2C_EXTENSION:return self._read(ext['params']['jwksUri'])
        raise A2CError(INVALID_OBJECT,'Agent Card has no A2C key declaration')

    def send(self,to,op,body,files=None,task_id=None):
        if op!='template.get' or 'op' in body or files or task_id:
            raise RuntimeFault('forbidden','Business protocol operations require the resident WebSocket')
        card=self._read(card_url(to))
        interfaces=card.get('supportedInterfaces',[])
        interface=next((i for i in interfaces if i.get('protocolBinding')=='JSONRPC'),None)
        if interface is None:raise A2CError(INVALID_OBJECT,'Agent Card has no JSONRPC interface')
        url=self.network.public_url(interface['url'])
        message=Message(role=Role.ROLE_USER,message_id=str(uuid.uuid4()),parts=to_parts({'op':op,**body}),extensions=[A2C_EXTENSION])
        request=SendMessageRequest(message=message)
        request_id=str(uuid.uuid4())
        response=self.network.http.post(url,timeout=20,headers={'A2A-Extensions':A2C_EXTENSION,'A2A-Version':interface['protocolVersion']},
            json={'jsonrpc':'2.0','id':request_id,'method':'SendMessage','params':json_format.MessageToDict(request)})
        response.raise_for_status();rpc=response.json()
        if rpc.get('id')!=request_id or rpc.get('jsonrpc')!='2.0' or rpc.get('error'):
            raise A2CError(INVALID_OBJECT,'Invalid public A2A response')
        result=json_format.ParseDict(rpc['result'],SendMessageResponse())
        if not result.HasField('message'):raise A2CError(INVALID_OBJECT,'Template response is not an A2A message')
        out,returned_files=from_parts(result.message.parts)
        if 'error' in out:raise A2CError(out['error']['code'],out['error'].get('message',''))
        return Reply(out,returned_files)
