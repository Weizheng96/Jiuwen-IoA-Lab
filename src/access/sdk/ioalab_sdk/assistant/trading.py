"""Generic candidate requests, using the original Client on the resident runtime."""
import base64
import copy
import time
import uuid
from urllib.parse import quote

import httpx
from a2c.binding import A2ATransport
from a2c.codec import digest,utcnow,parse_time
from a2c.card import card_digest,request_digest,validate_card
from a2c.jws import header_of
from a2c.common import Directory,Store
from a2c.client import Client
from a2c.payment.ap2 import AP2Payment
from a2c.slots import verify_template,verify_card
from a2c.errors import A2CError
from ..adapters import ProtocolStore,SessionTransport
from ..adapters.transport import stable
from ..runtime.protocol import RuntimeFault
from ..runtime.scheduling import JobOutcome
from ..runtime.observations import model_port


class Trading:
    def __init__(self,role):
        self.role,self.runtime,self.identity=role,role.runtime,role.identity
        self.store=self.runtime.store;self.namespace='quotes:'+self.identity.agent_id
        self.public=A2ATransport(http=lambda:httpx.AsyncClient(timeout=20,trust_env=False))

    def close(self):self.public.close()

    def protocol_id(self,service):
        try:
            if service!='svc-'+str(uuid.UUID(service[4:])):raise ValueError()
        except (TypeError,ValueError,AttributeError):raise RuntimeFault('invalid_input','Use a registered stable service agentId') from None
        return self.runtime.account.base+'/a2a/agents/'+service

    def client(self,store,transport=None):
        transport=transport or self.public
        pay=self.runtime.account.config['pay']
        payment=AP2Payment(transport,psp=pay,payer=self.identity.payment,instrument={'id':'ioalab-virtual','type':'wallet'},clock=utcnow)
        def pause(seconds):
            if self.runtime.closing.wait(seconds):raise RuntimeFault('unknown_result','Runtime closed while waiting for the original protocol result','resume_original')
        return Client(self.identity.signer,keys=Directory(fetch=transport.jwks),transport=transport,fetch=self.runtime.account.fetch_package,
            store=store,payment_provider={'id':pay,'jwksUri':pay+'/.well-known/jwks.json'},payment=payment,user=self.identity.user,
            markets=(self.runtime.account.config['market'],),sleep=pause,
            model=model_port(self.runtime,self.role.host,self.identity,transport.relation['id']) if isinstance(transport,SessionTransport) else None)

    def inspect(self,service,template_digest=None):
        values=self.public.send(self.protocol_id(service),'template.get',{'templateDigest':template_digest} if template_digest else {}).body['templates']
        for value in values:verify_template(value,Directory(fetch=self.public.jwks))
        return values

    def request(self,operation,args):
        self.role.fields(args,('taskRef','service','templateDigest','spec'),('materials','confirmationRef'))
        key='quote:'+stable(self.identity.agent_id,operation)
        record=self.store.checkpoint(self.namespace,key)
        fingerprint=digest({k:v for k,v in args.items() if k!='confirmationRef'})
        if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original candidate request changed')
        if record is None:
            task=self.role.delegation.task(args['taskRef']);scope=digest(task['context'])
            if not self.role.delegation.status(args['taskRef'])['data']['authorized']:raise RuntimeFault('forbidden','Authorize the delegation before requesting candidates')
            template=next((t for t in self.inspect(args['service'],args['templateDigest']) if t['templateDigest']==args['templateDigest']),None)
            if template is None:raise RuntimeFault('not_ready','That template is not offered')
            materials={}
            if not isinstance(args.get('materials',{}),dict):raise RuntimeFault('invalid_input','Use a material map keyed by declared names')
            for name,value in args.get('materials',{}).items():
                if not isinstance(value,dict) or set(value)-{'text','bytesBase64','spec'} or ('text' in value)==('bytesBase64' in value):raise RuntimeFault('invalid_input','Provide text or bytesBase64 and an optional material spec')
                try:data=value['text'].encode('utf-8') if 'text' in value else base64.b64decode(value['bytesBase64'],validate=True)
                except (TypeError,ValueError,AttributeError):raise RuntimeFault('invalid_input','Invalid material bytes') from None
                materials[name]=(data,value.get('spec',{}))
            initial=Store();client=self.client(initial);initial.put('templates',template['templateDigest'],template)
            ref=key[len('quote:'):]
            common={'spec':args['spec'],'materials':materials,'signers':{'slot1':'user','slot7':'user'},'delegation_scope_digest':scope}
            if template['formation']=='proposal':subject=client.draft_request(template,request_id=ref,**common)
            else:subject=client.draft_card(template,card_id=ref,**common).card
            record={'fingerprint':fingerprint,'quoteRef':ref,'taskRef':args['taskRef'],'service':args['service'],'template':template,'subject':subject,
                    'initial':{name:initial.all(name) for name in ('templates','requests','cards')},'phase':'prepared'}
            self.store.checkpoint_set(self.namespace,key,record)
        if record.get('result') is not None:return {'status':record['status'],'data':record['result']}
        template=record['template'];ref=record['quoteRef']
        if template['formation']!='proposal':
            result={'quoteRef':ref,'taskRef':record['taskRef'],'service':record['service'],'formation':'countersign','card':record['subject'],
                    'notice':'依据有效模板的未签报价，尚待用户选择与服务方批准'}
            self.store.checkpoint_set(self.namespace,key,{**record,'result':result,'status':'completed'})
            return {'status':'completed','data':result}
        review=[item['name'] for item in template.get('inputs',[]) if item.get('reviewBeforeSigning') and item['name'] in record['subject']['inputs']]
        if review:
            binding={'actor':self.identity.agent_id,'operationId':operation,'subject':{'quoteRef':ref,'materials':{name:record['subject']['inputs'][name] for name in review}},
                     'stage':'delegation.materials','action':'materials.authorize','preview':{'title':'允许候选服务审查这些材料','service':record['service'],
                     'materials':{name:record['subject']['inputs'][name] for name in review}}}
            decision=self.runtime.confirmations.ensure(binding)
            if decision['status']!='approved':return {'status':'waiting' if decision['status']=='pending' else 'rejected','data':{'quoteRef':ref,'confirmationRef':decision['reference']}}
            self.runtime.confirmations.claim(decision['reference'],binding)
            record={**record,'materialApproval':{'reference':decision['reference'],'binding':binding}}
            self.store.checkpoint_set(self.namespace,key,record)
        job=self.runtime.scheduler.submit(self.identity.agent_id,ref,'assistant.quote',{'quoteRef':ref},operation_id=ref)
        return {'status':job['status'] if job['status'] in ('completed','rejected','failed','unknown') else 'accepted','data':{'quoteRef':ref,'taskRef':record['taskRef'],'job':job}}

    def status(self,reference):
        if not isinstance(reference,str):raise RuntimeFault('invalid_input','Use the original quoteRef')
        record=self.store.checkpoint(self.namespace,'quote:'+reference)
        if not record:raise RuntimeFault('forbidden','Quote belongs to another local assistant')
        if record.get('result') is not None:return {'status':record['status'],'data':record['result']}
        try:job=self.runtime.scheduler.get(self.runtime.scheduler.reference(self.identity.agent_id,'assistant.quote',reference))
        except RuntimeFault:job=None
        return {'status':job['status'] if job and job['status'] in ('completed','rejected','failed','unknown') else 'running' if job else 'waiting',
                'data':{'quoteRef':reference,'phase':record['phase'],'job':job}}

    def establish(self,record,operation,subject):
        sent=self.role.delegation.intent(operation,{'taskRef':record['taskRef'],'service':record['service'],subject:record['subject']})
        origin=sent['data']['messageRef']
        def reserved():
            if self.runtime.closing.is_set():raise RuntimeFault('unknown_result','Runtime closed while waiting for its original admission')
            relation=self.store.interaction(origin=origin)
            for frame in self.store.messages(self.identity.agent_id):
                value=frame['message']
                if value.get('kind')!='delegation.result' or value.get('inReplyTo')!=origin:continue
                body=value['body']
                if body['outcome'] in ('rejected','failed'):raise RuntimeFault(body['error'].get('code','not_ready'),'Service did not accept this intent')
                if body['outcome']=='reserved' and relation:return relation,body
            return None
        return self.runtime.connection.wait_for(reserved)

    def protocol(self,record,interaction,admission):
        transport=SessionTransport(self.runtime,self.identity,interaction,admission,self.protocol_id(record['service']),public=self.public)
        store=ProtocolStore(self.store,self.identity.agent_id,interaction)
        for table,values in record['initial'].items():
            for name,value in values.items():
                if store.get(table,name) is None:store.put(table,name,value)
        return self.client(store,transport)

    def run(self,job):
        ref=job['payload']['quoteRef'];key='quote:'+ref;record=self.store.checkpoint(self.namespace,key)
        if record.get('result') is not None:return JobOutcome('completed',record['result'])
        if record['phase']=='calling':raise RuntimeFault('unknown_result','Inspect the original signed call; never create a replacement request')
        if record.get('materialApproval'):
            approval=record['materialApproval']
            self.runtime.confirmations.claim(approval['reference'],approval['binding'])
        template=record['template']
        try:relation,receipt=self.establish(record,stable('quote-intent',ref),'request')
        except RuntimeFault as exc:
            if exc.code in ('capacity_full','expired','closed','forbidden','service_paused'):
                return self.finish(key,record,'rejected',{'quoteRef':ref,'error':{'code':exc.code,'message':str(exc)}})
            raise
        record={**record,'phase':'calling','interactionId':relation['id'],'admissionId':receipt['admissionId']}
        self.store.checkpoint_set(self.namespace,key,record)
        client=self.protocol(record,relation['id'],receipt['admissionId'])
        try:card=client.submit_request(record['subject'])
        except A2CError as exc:
            return self.finish(key,record,'rejected',{'quoteRef':ref,'error':{'code':exc.code,'message':exc.message}})
        if isinstance(card,str):return self.finish(key,record,'rejected',{'quoteRef':ref,'reason':card})
        return self.finish(key,record,'completed',{'quoteRef':ref,'taskRef':record['taskRef'],'service':record['service'],'formation':'proposal',
                'interactionId':relation['id'],'admissionId':receipt['admissionId'],'card':card,'notice':'真实签2/3提案，尚未由用户选择或签1'})

    def finish(self,key,record,status,result):
        self.store.checkpoint_set(self.namespace,key,{**record,'phase':'finished','status':status,'result':result})
        return JobOutcome(status,result)

    def recover(self):
        for key,record in self.store.checkpoints(self.namespace).items():
            if not key.startswith('quote:'):continue
            reference=self.runtime.scheduler.reference(self.identity.agent_id,'assistant.quote',record['quoteRef'])
            try:job=self.runtime.scheduler.get(reference)
            except RuntimeFault:continue
            if job['status']!='unknown':continue
            if record.get('result') is not None:
                self.runtime.scheduler.resolve(reference,JobOutcome(record['status'],record['result']));continue
            if record['phase']!='calling':continue
            transport=SessionTransport(self.runtime,self.identity,record['interactionId'],record['admissionId'],self.protocol_id(record['service']),public=self.public)
            delivery=transport.received('request.submit')
            if not delivery or delivery['result'].get('executionError'):continue
            result=delivery['result']['reply']['body']
            if 'card' not in result and 'reason' not in result and 'error' not in result:continue
            if 'card' in result:
                card=result['card'];request=delivery['call']['body']['request'];template=record['template']
                client=self.client(ProtocolStore(self.store,self.identity.agent_id,record['interactionId']),transport)
                validate_card(template,card,request=request)
                verify_card(template,card,client.keys,request_ts=parse_time(header_of(request['signature'])['a2c-ts']))
                original=client.store.get('requests',request_digest(request))
                client.store.put('cards',card_digest(card),{'card':card,'request':request,'materials':original['materials'],'state':'offered'})
                outcome=self.finish(key,record,'completed',{'quoteRef':record['quoteRef'],'taskRef':record['taskRef'],'service':record['service'],
                    'formation':'proposal','interactionId':record['interactionId'],'admissionId':record['admissionId'],'card':card,'notice':'已恢复原签名提案，尚未由用户选择或签1'})
            else:
                outcome=self.finish(key,record,'rejected',{'quoteRef':record['quoteRef'],**{k:result[k] for k in ('reason','error') if k in result}})
            self.runtime.scheduler.resolve(reference,outcome)
