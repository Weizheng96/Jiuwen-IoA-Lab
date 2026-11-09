"""Assistant private messaging: recipient interfaces, outbound requests, their status and closing the interaction."""
import time
from urllib.parse import quote,urlencode
import uuid

from a2c.codec import digest
import httpx
from jsonschema import Draft202012Validator,FormatChecker

from ..runtime import RuntimeFault,encode,instant,stamp


def stable(*values):return str(uuid.uuid5(uuid.NAMESPACE_URL,encode(list(values))))


class Messaging:
    """Outbound `assistant.messaging.*` for the bound assistant identity; reads the role's identity and store when called."""
    def __init__(self,role):
        self.role=role

    def interfaces(self,account):
        if not isinstance(account,str) or not 3<=len(account)<=128:raise RuntimeFault('invalid_input','Specify the exact recipient account')
        definitions=self.role.runtime.account.api('GET','/messaging/accounts/'+quote(account,safe='')+'/interfaces')
        result=[]
        for value in definitions:
            if not value.get('agentId'):continue
            reference=stable(self.role.identity.agent_id,value['agentId'],value['name'],value['revision'],digest(value))
            item={'account':account,**value,'interfaceRef':reference}
            self.role.store.checkpoint_set(self.role.namespace,'interface:'+reference,item)
            result.append(item)
        return result

    def _outbound(self,args):
        if args.get('interactionRef'):
            relation=self.role.store.interaction(interaction_id=args['interactionRef'])
            if not relation or relation['actor']!=self.role.identity.agent_id or relation['direction']!='outbound':
                raise RuntimeFault('forbidden','Use an original outbound interaction reference')
            if any(frame['message'].get('kind')=='interaction.closed' and
                   frame['message'].get('interactionId')==relation['id']
                   for frame in self.role.store.messages(self.role.identity.agent_id)):
                raise RuntimeFault('closed','The original interaction is closed; create a new request if needed')
            record=self.role.store.checkpoint(self.role.namespace,'message:'+relation['origin'])
            if not record:raise RuntimeFault('not_ready','Original interface snapshot is missing')
            interface=record['interface']
            if args.get('interfaceRef',interface['interfaceRef'])!=interface['interfaceRef']:
                raise RuntimeFault('id_conflict','Continuing an interaction cannot change its interface')
            return interface,relation
        reference=args.get('interfaceRef')
        interface=self.role.store.checkpoint(self.role.namespace,'interface:'+str(reference))
        if not interface:raise RuntimeFault('not_ready','Read the recipient interface before sending')
        return interface,None

    def send(self,operation,args):
        fingerprint=digest(args)
        with self.role.lock:
            old=self.role.store.checkpoint(self.role.namespace,'send:'+operation)
            if old:
                if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original message input changed')
                return {'status':'accepted','data':{'messageRef':old['messageId'],'interactionRef':old['envelope'].get('interactionId'),'operationRef':old['operationRef']}}
            interface,relation=self._outbound(args)
            try:problem=next(Draft202012Validator(interface['inputSchema'],format_checker=FormatChecker()).iter_errors(args['input']),None)
            except (TypeError,ValueError):raise RuntimeFault('invalid_input','Invalid message input') from None
            if problem:raise RuntimeFault('invalid_input','Input does not match the selected interface')
            if len(encode(args['input']).encode())>32768:raise RuntimeFault('invalid_input','Input exceeds 32 KiB')
            ttl=args.get('ttlSeconds',3600)
            if type(ttl) not in (int,float) or not 1<=ttl<=7*86400:raise RuntimeFault('invalid_input','Invalid message lifetime')
            now=time.time();until=now+ttl
            if relation:until=min(until,instant(relation['details']['deadline']))
            if until<=now:raise RuntimeFault('closed','Original interaction has reached its deadline')
            mid=stable('message',self.role.identity.agent_id,operation)
            envelope={'messageId':mid,'kind':'message.request','recipient':interface['agentId'],'expiresAt':stamp(until),
                      'body':{'interfaceName':interface['name'],'revision':interface['revision'],'input':args['input']}}
            if relation:envelope['interactionId']=relation['id']
            reference={'kind':'delivery','agentId':self.role.identity.agent_id,'operation':'message.send','operationId':operation}
            record={'fingerprint':fingerprint,'messageId':mid,'operationRef':reference,'interface':interface,'envelope':envelope}
            self.role.store.prepare_checkpointed(self.role.identity.agent_id,'message.send',envelope,operation,self.role.namespace,
                                            {'send:'+operation:record,'message:'+mid:record})
        self.role.wake()
        return {'status':'accepted','data':{'messageRef':mid,'interactionRef':relation['id'] if relation else None,'operationRef':reference}}

    def message_status(self,mid):
        record=self.role.store.checkpoint(self.role.namespace,'message:'+str(mid))
        if not record:raise RuntimeFault('forbidden','Message is outside this local sender')
        relation=self.role.store.interaction(origin=mid) or self.role.store.interaction(interaction_id=record['envelope'].get('interactionId'))
        response=self.role.store.response(self.role.identity.agent_id,'message.send',record['operationRef']['operationId'])
        replies=[frame['message'] for frame in self.role.store.messages(self.role.identity.agent_id)
                 if frame['message'].get('kind')=='message.result' and frame['message'].get('inReplyTo')==mid and frame['message'].get('payloadAvailable') is not False]
        body=replies[-1]['body'] if replies else None
        platform_state=None
        # A local deadline cannot prove that a remote effect never started.
        # Recover through the account-authorized original message, never re-send.
        if not body and response and response['ok'] and instant(record['envelope']['expiresAt'])<=time.time():
            remote=self.role.store.checkpoint(self.role.namespace,'terminal:'+mid)
            try:
                if remote is None:
                    remote=self.role.runtime.account.api('GET','/messages/'+quote(mid,safe='')+'?'+urlencode({'agentId':self.role.identity.agent_id}))
                    if remote['requestState'] in ('completed','rejected','failed','expired'):
                        self.role.store.checkpoint_set(self.role.namespace,'terminal:'+mid,remote)
                platform_state=remote['requestState']
                body=remote.get('result')
            except (httpx.HTTPError,RuntimeFault):platform_state='unknown'
        closed=any(frame['message'].get('kind')=='interaction.closed' and
                   (frame['message'].get('originMessageId')==mid or relation and frame['message'].get('interactionId')==relation['id'])
                   for frame in self.role.store.messages(self.role.identity.agent_id))
        state=body['outcome'] if body else 'rejected' if response and not response['ok'] else platform_state or ('closed' if closed else 'awaiting_result' if relation else 'awaiting_open')
        return {'messageRef':mid,'interactionRef':relation['id'] if relation else None,'state':state,'result':body,
                'closed':closed,'requestState':platform_state,'platformReceipt':response.get('result') if response and response['ok'] else None,
                'error':response.get('error') if response and not response['ok'] else None}

    def status(self,args):
        mid=args.get('messageRef')
        if not isinstance(mid,str) or not mid:raise RuntimeFault('invalid_input','Use the messageRef returned by this send operation','correct_input')
        wait=args.get('waitSeconds',0)
        if type(wait) not in (int,float) or not 0<=wait<=30:raise RuntimeFault('invalid_input','Status wait is limited to 30 seconds')
        until=time.monotonic()+wait
        while True:
            value=self.message_status(mid)
            if value['result'] or value['error'] or value['closed'] or value['state'] in ('expired','failed','rejected','unknown') or time.monotonic()>=until:return value
            self.role._stop.wait(.1)

    def close(self,operation,args):
        if bool(args.get('messageRef'))==bool(args.get('interactionRef')):raise RuntimeFault('invalid_input','Specify the original message or interaction')
        if args.get('messageRef'):
            self.message_status(args['messageRef'])
            payload={'originMessageId':args['messageRef']}
        else:
            relation=self.role.store.interaction(interaction_id=args['interactionRef'])
            if not relation or relation['actor']!=self.role.identity.agent_id:raise RuntimeFault('forbidden','Interaction is outside this identity')
            payload={'interactionId':relation['id']}
        reason=args.get('reason','user_finished')
        if not isinstance(reason,str) or len(reason)>200:raise RuntimeFault('invalid_input','Invalid closure reason')
        self.role.runtime.connection.submit(self.role.identity.agent_id,'interaction.close',{**payload,'reason':reason},operation)
        return {'status':'accepted','data':{'operationRef':{'kind':'delivery','agentId':self.role.identity.agent_id,'operation':'interaction.close','operationId':operation}}}
