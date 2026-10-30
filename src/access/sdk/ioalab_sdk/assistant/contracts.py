"""Exact user selection before original A2C slot 1; recover signed facts only."""
import copy
import time
import threading

from a2c.card import card_digest,validate_card
from a2c.codec import digest,utcnow,parse_time,parse_duration
from a2c.errors import A2CError
from a2c.common import Directory
from a2c.jws import sign
from a2c.slots import verify_card
from ..runtime.protocol import RuntimeFault
from ..runtime.scheduling import JobOutcome
from ..adapters.transport import stable


class Contracts:
    def __init__(self,trading):
        self.trading,self.role,self.runtime,self.identity=trading,trading.role,trading.runtime,trading.identity
        self.store=trading.store;self.namespace='contracts:'+self.identity.agent_id
        self.lock=threading.RLock()

    def record(self,reference):
        if not isinstance(reference,str):raise RuntimeFault('invalid_input','Use an original contractRef')
        value=self.store.checkpoint(self.namespace,reference)
        if not value:raise RuntimeFault('forbidden','Contract belongs to another local assistant')
        return value

    def confirm(self,operation,args):
        with self.lock:return self._confirm(operation,args)

    def _confirm(self,operation,args):
        self.role.fields(args,('quoteRef',),('confirmationRef',))
        if not isinstance(args['quoteRef'],str):raise RuntimeFault('invalid_input','Use an original quoteRef')
        quote=self.store.checkpoint(self.trading.namespace,'quote:'+args['quoteRef'])
        if not quote or quote.get('status')!='completed' or 'card' not in quote.get('result',{}):raise RuntimeFault('not_ready','Use an actual completed candidate quote')
        card=quote['result']['card'];ref=card_digest(card)
        record=self.store.checkpoint(self.namespace,ref)
        if record and record['quoteRef']!=args['quoteRef']:raise RuntimeFault('id_conflict','Recover the original candidate selection')
        if not record:
            validate_card(quote['template'],card)
            chain=verify_card(quote['template'],card,Directory(fetch=self.trading.public.jwks))
            until=parse_time(quote['template']['validUntil']).timestamp()
            if quote['template']['formation']=='proposal':until=min(until,chain.ts[3].timestamp()+parse_duration(quote['template']['deadlines']['respondWithin']).total_seconds())
            if until<=time.time():raise RuntimeFault('expired','This candidate can no longer form a new contract')
            binding={'actor':self.identity.agent_id,'operationId':operation,'subject':{'quoteRef':args['quoteRef'],'cardDigest':ref,'scopeDigest':card['delegationScopeDigest']},
                     'stage':'delegation.contract','action':'card.confirm','preview':{'title':'确认具体委托合同','service':quote['service'],
                        'formation':quote['template']['formation'],'card':card,'materialsForReview':{name:card['inputs'][name] for name in card['inputs']
                        if any(item['name']==name and item.get('reviewBeforeSigning') for item in quote['template'].get('inputs',[]))},
                        'materials':{name:{**value,'sendAt':'before-signing' if any(item['name']==name and item.get('reviewBeforeSigning') for item in quote['template'].get('inputs',[])) else 'work.start'} for name,value in card['inputs'].items()},
                        'notice':'本次确认允许签署本合同及按所示阶段发送这些材料；实名环节和验收另行确认'}}
            record={'quoteRef':args['quoteRef'],'taskRef':quote['taskRef'],'service':quote['service'],'template':quote['template'],'subject':copy.deepcopy(card),
                    'initial':quote['initial'],'binding':binding,'ttl':min(900,until-time.time()),'phase':'prepared','jobInteraction':quote['result'].get('interactionId',ref),
                    **{key:quote['result'][key] for key in ('interactionId','admissionId') if key in quote['result']}}
            self.store.checkpoint_set(self.namespace,ref,record)
        if record.get('result') is not None:return {'status':record['status'],'data':record['result']}
        if record['phase']=='approved':return self.schedule(ref,record)
        if record['phase']!='prepared':return self.status(ref)
        decision=self.runtime.confirmations.ensure(record['binding'],ttl=record['ttl'])
        if args.get('confirmationRef',decision['reference'])!=decision['reference']:raise RuntimeFault('id_conflict','Use this exact contract confirmation')
        if decision['status']!='approved':return {'status':'waiting' if decision['status']=='pending' else 'rejected','data':{'contractRef':ref,'quoteRef':record['quoteRef'],'confirmationRef':decision['reference']}}
        self.runtime.confirmations.claim(decision['reference'],record['binding'])
        record={**record,'approval':decision['reference'],'phase':'approved'};self.store.checkpoint_set(self.namespace,ref,record)
        return self.schedule(ref,record)

    def schedule(self,reference,record):
        job=self.runtime.scheduler.submit(self.identity.agent_id,record['jobInteraction'],'assistant.contract',{'contractRef':reference},operation_id=reference)
        return {'status':job['status'] if job['status'] in ('completed','rejected','failed','unknown') else 'accepted','data':{'contractRef':reference,'quoteRef':record['quoteRef'],'job':job}}

    def status(self,reference):
        record=self.record(reference)
        if record.get('result') is not None:return {'status':record['status'],'data':record['result']}
        try:job=self.runtime.scheduler.get(self.runtime.scheduler.reference(self.identity.agent_id,'assistant.contract',reference))
        except RuntimeFault:job=None
        return {'status':job['status'] if job and job['status'] in ('rejected','failed','unknown') else 'running' if job else 'waiting',
                'data':{'contractRef':reference,'quoteRef':record['quoteRef'],'phase':record['phase'],'job':job}}

    def save(self,reference,record,**updates):
        record={**record,**updates};self.store.checkpoint_set(self.namespace,reference,record);return record

    def finish(self,reference,record,status,**result):
        result={'contractRef':reference,'quoteRef':record['quoteRef'],'taskRef':record['taskRef'],**{key:record[key] for key in ('interactionId','admissionId') if key in record},**result}
        self.save(reference,record,phase='finished',status=status,result=result)
        return JobOutcome(status,result)

    def run(self,job):
        ref=job['payload']['contractRef'];record=self.record(ref)
        if record.get('result') is not None:return JobOutcome(record['status'],record['result'])
        if record['phase']!='approved':raise RuntimeFault('unknown_result','Reconcile the original selection and signatures')
        try:
            self.runtime.confirmations.claim(record['approval'],record['binding'])
            if 'selection' not in record:
                payload={'scopeDigest':record['subject']['delegationScopeDigest'],'card':record['subject']}
                payload['proof']=sign(digest({'scopeDigest':payload['scopeDigest'],'cardDigest':ref}),self.identity.user,'ioa-contract-selection',utcnow())
                record=self.save(ref,record,selection=payload)
            receipt=self.runtime.connection.call(self.identity.agent_id,'scope.select',record['selection'],operation_id=stable('select',self.identity.agent_id,ref))
            if (receipt.get('scopeDigest'),receipt.get('selectedCardDigest'))!=(record['subject']['delegationScopeDigest'],ref):raise RuntimeFault('protocol_error','Selection receipt differs from this contract')
            record=self.save(ref,record,selectionReceipt=receipt,phase='selected')
            if record['template']['formation']=='countersign':
                relation,admission=self.trading.establish(record,stable('contract-intent',ref),'card')
                record=self.save(ref,record,interactionId=relation['id'],admissionId=admission['admissionId'])
            client=self.trading.protocol(record,record['interactionId'],record['admissionId'])
            if record['template']['formation']=='proposal':
                chain=verify_card(record['template'],record['subject'],client.keys)
                if time.time()>min(parse_time(record['template']['validUntil']).timestamp(),chain.ts[3].timestamp()+parse_duration(record['template']['deadlines']['respondWithin']).total_seconds()):
                    raise RuntimeFault('expired','Proposal window expired before creating slot 1')
            self.runtime.require_execution()
            record=self.save(ref,record,phase='signing')
            card=client.adopt(record['subject']) if record['template']['formation']=='proposal' else client.sign(record['subject'])
            record=self.save(ref,record,signed=card,phase='signed')
            if record['template']['formation']=='countersign':card=client.countersign(card)
            if isinstance(card,str):return self.finish(ref,record,'rejected',reason=card,formed=False)
            return self.formed(ref,record,client,card)
        except A2CError as exc:return self.finish(ref,record,'rejected',error={'code':exc.code,'message':exc.message},formed=False)
        except RuntimeFault as exc:
            if exc.code in ('capacity_full','expired','closed','forbidden','quota_exceeded','id_conflict','service_paused'):
                return self.finish(ref,record,'rejected',error={'code':exc.code,'message':str(exc)},formed=False)
            raise

    def formed(self,reference,record,client,card):
        validate_card(record['template'],card)
        if card_digest(card)!=reference or verify_card(record['template'],card,client.keys).effective is None:raise RuntimeFault('protocol_error','Original contract is not effective')
        original=client.store.get('cards',reference)
        if not original:raise RuntimeFault('unknown_result','Restore the original client protocol record')
        client.store.put('cards',reference,{**original,'card':card,**({'state':'formed'} if record['template']['formation']=='countersign' else {})})
        return self.finish(reference,record,'completed',card=card,formed=True,started=False)

    def recover(self):
        for ref,record in self.store.checkpoints(self.namespace).items():
            reference=self.runtime.scheduler.reference(self.identity.agent_id,'assistant.contract',ref)
            try:job=self.runtime.scheduler.get(reference)
            except RuntimeFault:continue
            if job['status']!='unknown':continue
            if record.get('result') is not None:
                self.runtime.scheduler.resolve(reference,JobOutcome(record['status'],record['result']));continue
            if not record.get('selectionReceipt') or not record.get('interactionId'):continue
            client=self.trading.protocol(record,record['interactionId'],record['admissionId'])
            original=client.store.get('cards',ref);card=original['card'] if original else None
            if not card or not card['signatures']['1']:continue
            if record['template']['formation']=='countersign':
                received=client.transport.received('card.countersign')
                if not received or received['result'].get('executionError'):continue
                response=received['result']['reply']['body']
                if 'reason' in response or 'error' in response:
                    outcome=self.finish(ref,record,'rejected',**{key:response[key] for key in ('reason','error') if key in response},formed=False)
                    self.runtime.scheduler.resolve(reference,outcome);continue
                signed=response.get('card')
                if not signed:continue
                if signed['signatures']['1']!=card['signatures']['1']:raise RuntimeFault('protocol_error','Recovered response changed the original user signature')
                card=signed
            outcome=self.formed(ref,record,client,card)
            self.runtime.scheduler.resolve(reference,outcome)
