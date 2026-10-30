"""User-authorized delegation setup. Contract execution remains in A2C adapters."""
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import re
import threading
import time
from urllib.parse import quote
import uuid

from a2c.codec import digest,fmt_time,parse_time
from a2c.jws import sign
from a2c.card import request_digest,card_digest
from ..runtime.protocol import stamp
import httpx

from ..runtime.protocol import RuntimeFault,encode


def money(value):
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError):raise RuntimeFault('invalid_input','Provide a nonnegative virtual currency amount') from None
    if not result.is_finite() or result<0 or result>Decimal('9999999999999.99') or result!=result.quantize(Decimal('.01')):
        raise RuntimeFault('invalid_input','Use a finite amount with at most two decimal places')
    return format(result,'.2f')


class DelegationTasks:
    def __init__(self,runtime,identity):
        self.runtime,self.identity=runtime,identity
        self.store,self.account=runtime.store,runtime.account
        self.namespace='delegation:'+identity.agent_id
        self.lock=threading.RLock()

    @staticmethod
    def _days(value):
        if type(value) not in (int,float) or not 1/86400<=value<=7:raise RuntimeFault('invalid_input','Authorization validity must be between one second and seven days')
        return value

    def prepare(self,operation,args):
        purpose=args['purpose'];categories=args.get('categoryIds')
        if not isinstance(purpose,str) or not 1<=len(purpose)<=2000:raise RuntimeFault('invalid_input','Provide the delegation purpose')
        if categories is not None and (not isinstance(categories,list) or len(categories)>32 or any(not isinstance(item,str) or not 1<=len(item)<=128 for item in categories) or len(set(categories))!=len(categories)):
            raise RuntimeFault('invalid_input','Use exact category IDs, null or an empty list')
        budget,fees=money(args['budget']),money(args['proposalFeeLimit'])
        if Decimal(fees)>Decimal(budget):raise RuntimeFault('invalid_input','Proposal fees are part of the total budget')
        days=self._days(args.get('validDays',7))
        fingerprint=digest(args)
        reference=str(uuid.uuid5(uuid.NAMESPACE_URL,encode(['ioa-delegation',self.identity.agent_id,operation])))
        key='task:'+reference
        with self.lock:
            old=self.store.checkpoint(self.namespace,key)
            if old and old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original delegation task changed')
            if not old:
                context={'schema':'ioalab.scope/1','scopeId':reference,'platform':self.runtime.config.platform,
                         'actor':self.identity.agent_id,'user':{'id':self.identity.user.id,'jwk':self.identity.user.jwk},
                         'purpose':purpose,'categoryIds':categories,'currency':'CNY','budget':budget,'proposalFeeLimit':fees}
                self.store.checkpoint_set(self.namespace,key,{'fingerprint':fingerprint,'context':context,'validDays':days})
        return {'status':'completed','data':{'taskRef':reference,'scopeDigest':digest(self.task(reference)['context']),'authorized':False}}

    def task(self,reference):
        if not isinstance(reference,str):raise RuntimeFault('invalid_input','Use the original taskRef')
        value=self.store.checkpoint(self.namespace,'task:'+reference)
        if not value:raise RuntimeFault('forbidden','Delegation task is outside this local assistant')
        return value

    def _status(self,scope):
        return self.account.api('GET','/scopes/'+quote(scope,safe='')+'?actor='+quote(self.identity.agent_id,safe=''))

    def status(self,reference):
        task=self.task(reference);context=task['context'];scope=digest(context)
        try:remote=self._status(scope)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code!=404:raise
            remote=None
        return {'status':'completed','data':{'taskRef':reference,'scopeDigest':scope,'purpose':context['purpose'],
                                            'budget':context['budget'],'proposalFeeLimit':context['proposalFeeLimit'],
                                            'authorization':remote,'authorized':bool(remote and remote['state']=='active')}}

    def authorize(self,operation,args,*,renew=False):
        reference=args['taskRef'];task=self.task(reference);context=task['context'];scope=digest(context)
        fingerprint=digest({'arguments':{key:value for key,value in args.items() if key!='confirmationRef'},'renew':renew})
        key='authorization:'+operation
        with self.lock:
            record=self.store.checkpoint(self.namespace,key)
            if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original authorization parameters changed')
        if record and record.get('payload'):return self._submit(operation,reference,record)
        if not record:
            days=self._days(args.get('validDays',task['validDays']))
            if renew:
                current=self._status(scope)
                revision=current['revision']+1
            else:revision=1
            issued=datetime.fromtimestamp(time.time(),timezone.utc).replace(microsecond=0)
            grant={'revision':revision,'issuedAt':fmt_time(issued),'expiresAt':fmt_time(datetime.fromtimestamp(issued.timestamp()+days*86400,timezone.utc))}
            candidate={'fingerprint':fingerprint,'context':context,'grant':grant,'renew':renew,
                       'previousState':current['state'] if renew else None}
            with self.lock:
                record=self.store.checkpoint(self.namespace,key)
                if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original authorization parameters changed')
                if not record:
                    record=candidate
                    self.store.checkpoint_set(self.namespace,key,record)
        grant=record['grant']
        binding={'actor':self.identity.agent_id,'operationId':operation,
                 'subject':{'taskRef':reference,'scopeDigest':scope,'grant':grant},
                 'stage':'delegation.renew' if renew else 'delegation.authorize','action':'scope.register',
                 'preview':{'title':'重新授权原委托' if record.get('previousState')=='closed' else '续期委托授权' if renew else '授权发起委托','purpose':context['purpose'],
                            'budget':context['budget'],'proposalFeeLimit':context['proposalFeeLimit'],
                            'categoryIds':context['categoryIds'],'currency':'虚拟币（CNY记账）',
                            'expiresAt':grant['expiresAt'],'revision':grant['revision'],
                            'contractConfirmation':'具体合同、合同要求的环节及验收仍须分别确认'}}
        confirmation=self.runtime.confirmations.ensure(binding)
        if confirmation['status']!='approved':
            return {'status':'waiting' if confirmation['status']=='pending' else 'rejected',
                    'data':{'taskRef':reference,'confirmationRef':confirmation['reference'],'preview':binding['preview']}}
        if args.get('confirmationRef',confirmation['reference'])!=confirmation['reference']:raise RuntimeFault('id_conflict','Use the original confirmation reference')
        self.runtime.confirmations.claim(confirmation['reference'],binding)
        payload={'context':context,'grant':grant}
        payload['proof']=sign(digest(payload),self.identity.user,'ioa-scope',parse_time(grant['issuedAt']))
        with self.lock:
            record={**record,'payload':payload}
            self.store.checkpoint_set(self.namespace,key,record)
        return self._submit(operation,reference,record)

    def _submit(self,operation,reference,record):
        receipt=record.get('receipt')
        if receipt is None:
            try:
                receipt=self.account.api('GET','/operations/'+quote(operation,safe='')+'?agentId='+quote(self.identity.agent_id,safe='')+'&operation=scope.register')
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code!=404:raise
                receipt=self.account.api('POST','/scopes',{'operationId':operation,**record['payload']})
            if receipt.get('scopeDigest')!=digest(record['context']) or receipt.get('acceptedRevision')!=record['grant']['revision']:
                raise RuntimeFault('protocol_error','Authorization result differs from the original signed context')
            self.store.checkpoint_set(self.namespace,'authorization:'+operation,{**record,'receipt':receipt})
        return {'status':'completed','data':{'taskRef':reference,'scopeDigest':receipt['scopeDigest'],
                                          'authorizationReceipt':receipt,'authorization':self._status(receipt['scopeDigest'])}}

    def close(self,operation,args):
        context=self.task(args['taskRef'])['context'];scope=digest(context)
        receipt=self.account.api('POST','/scopes/'+quote(scope,safe='')+'/close',{'actor':self.identity.agent_id,'operationId':operation})
        return {'status':'completed','data':{'taskRef':args['taskRef'],'authorizationReceipt':receipt,'authorization':self._status(scope)}}

    def intent(self,operation,args):
        task=self.task(args['taskRef']);scope=digest(task['context'])
        reference=str(uuid.uuid5(uuid.NAMESPACE_URL,'ioa-intent:'+self.identity.agent_id+':'+operation))
        fingerprint=digest(args)
        with self.lock:
            record=self.store.checkpoint(self.namespace,'intent:'+reference)
            if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original intent parameters changed')
            if not record:
                if ('request' in args)==('card' in args):raise RuntimeFault('invalid_input','Provide exactly one unsigned request or card')
                subject=args.get('request',args.get('card'))
                if not isinstance(subject,dict) or subject.get('delegationScopeDigest')!=scope or subject.get('client',{}).get('id')!=self.identity.agent_id:
                    raise RuntimeFault('forbidden','Intent must use this task and assistant identity')
                seconds=args.get('ttlSeconds',3600)
                if type(seconds) is not int or not 1<=seconds<=86400*7:raise RuntimeFault('invalid_input','Intent TTL must be between one second and seven days')
                name='request' if 'request' in args else 'card'
                body={'formation':'proposal' if name=='request' else 'countersign','templateDigest':subject.get('templateDigest'),
                      'scopeDigest':scope,'bindingDigest':request_digest(subject) if name=='request' else card_digest(subject),name:subject}
                payload={'messageId':reference,'kind':'delegation.intent','recipient':args['service'],'expiresAt':stamp(time.time()+seconds),'body':body}
                record={'fingerprint':fingerprint,'payload':payload,'taskRef':args['taskRef'],'operationId':operation}
                self.store.checkpoint_set(self.namespace,'intent:'+reference,record)
        accepted=self.runtime.connection.call(self.identity.agent_id,'message.send',record['payload'],operation_id=record['operationId'])
        return {'status':'completed','data':{'messageRef':reference,'taskRef':record['taskRef'],'deliveryReceipt':accepted}}

    def intent_status(self,reference):
        record=self.store.checkpoint(self.namespace,'intent:'+reference)
        if not record:raise RuntimeFault('not_ready','This instance has no original delegation intent')
        relation=self.runtime.connection.recover_interaction(self.identity.agent_id,reference)
        message=self.account.api('GET','/messages/'+quote(reference,safe='')+'?agentId='+quote(self.identity.agent_id,safe=''))
        return {'status':'completed','data':{'messageRef':reference,'taskRef':record['taskRef'],'interaction':relation,'result':message['result'],'requestState':message['requestState']}}
