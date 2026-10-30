"""Original Client lifecycle with durable operations and real stage-specific users."""
import base64
import threading
from urllib.parse import urlencode

from a2c import objects
from a2c.card import card_digest
from a2c.codec import digest,digest_bytes
from a2c.errors import A2CError
from a2c.slots import verify_card
from a2c.jws import header_of
from ..runtime.protocol import RuntimeFault
from ..runtime.scheduling import JobOutcome
from ..adapters.transport import stable
from ..adapters.reports import report


class Fulfillment:
    def __init__(self,role):
        self.role,self.runtime,self.store,self.identity=role,role.runtime,role.store,role.identity
        self.namespace='fulfillment:'+self.identity.agent_id;self.lock=threading.RLock()

    def client(self,reference):
        contract=self.role.contracts.record(reference)
        if not contract.get('result',{}).get('formed'):raise RuntimeFault('not_ready','Confirm this exact contract first')
        return self.role.trading.protocol(contract,contract['interactionId'],contract['admissionId']),contract

    def record(self,operation):
        value=self.store.checkpoint(self.namespace,operation)
        if not value:raise RuntimeFault('not_ready','Original fulfillment operation is unavailable')
        return value

    def save(self,operation,value,**updates):
        result={**value,**updates};self.store.checkpoint_set(self.namespace,operation,result);return result

    def status(self,reference,operation=None):
        client,contract=self.client(reference);record=client.store.get('cards',reference);card=record['card']
        step=None;available=len(card['signatures']['5'])
        if record.get('task'):
            try:
                task=client.transport.get_task(card['provider'],record['task'])
                if task.state=='auth-required':
                    step=(task.status or {}).get('stepRequest')
                    if step:objects.verify_signed(step,client.keys.jwk(card['provider'],header_of(step['signature']).get('kid')),signer=card['provider'],expect_kid=True)
                for item in task.artifacts:
                    if 'card' in item:
                        actual=item['card']
                        if card_digest(actual)!=reference:raise RuntimeFault('protocol_error','Progress belongs to another card')
                        verify_card(contract['template'],actual,client.keys);available=max(available,len(actual['signatures']['5']))
            except RuntimeFault as exc:
                if exc.code!='unknown_result':raise
        paid={value.get('period') for value in record.get('vouchers',[]) if value.get('purpose')=='installment'}
        required={i+1 for i,value in enumerate(card['derived']['periods']) if value['amount']>0}
        payment='paid' if required<=paid and len(card['signatures']['7'])==len(card['derived']['periods']) else 'partial' if paid or record.get('depositVoucher') else 'unpaid'
        result={**contract['result'],'card':card,'started':record.get('state')=='started','stepRequest':step,'availablePeriods':available,
                'acceptedPeriods':len(card['signatures']['7']),'vouchers':record.get('vouchers',[]),'depositVoucher':record.get('depositVoucher'),'payment':payment,
                'closed':any(frame['message'].get('kind')=='interaction.closed' and frame['message'].get('interactionId')==contract['interactionId'] for frame in self.store.messages(self.identity.agent_id))}
        if operation:
            saved=self.record(operation)
            if saved['args']['contractRef']!=reference:raise RuntimeFault('forbidden','Operation belongs to another contract')
            result['operation']=self.operation_status(operation,saved)
        return {'status':'completed','data':result}

    def operation_status(self,operation,record):
        if record.get('result') is not None:return {'status':record['status'],'data':record['result']}
        try:return self.runtime.scheduler.get(self.runtime.scheduler.reference(self.identity.agent_id,'assistant.fulfillment',operation))
        except RuntimeFault:return {'status':'waiting','data':{'operationId':operation,'phase':record['phase']}}

    def call(self,name,operation,args):
        required=('contractRef','stepRequestDigest') if name=='answer_step' else ('contractRef','period') if name in ('fetch','verify','accept','pay') else ('contractRef',)
        self.role.fields(args,required,('confirmationRef',))
        if 'period' in args and (type(args['period']) is not int or args['period']<1):raise RuntimeFault('invalid_input','Use a positive original period')
        with self.lock:
            value={key:item for key,item in args.items() if key!='confirmationRef'};fingerprint=digest({'name':name,'args':value})
            record=self.store.checkpoint(self.namespace,operation)
            if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original fulfillment operation changed')
            client,contract=self.client(args['contractRef'])
            if not record:
                record={'name':name,'args':value,'fingerprint':fingerprint,'phase':'prepared'}
                if name=='answer_step':
                    client.poll(args['contractRef']);source=client.store.get('cards',args['contractRef'])
                    pending=[item for item in source.get('steps',[]) if item['answer'] is None]
                    if not pending or objects.object_digest(pending[-1]['request'])!=args['stepRequestDigest']:raise RuntimeFault('forbidden','Use the actual current original step request')
                    record['preview']={'title':'确认本次合同环节','stepRequest':pending[-1]['request'],'contractRef':args['contractRef']}
                elif name=='accept':
                    result=client.verify(args['contractRef'],args['period'])
                    record['preview']={'title':'确认本期验收及相应付款','period':args['period'],'card':client.card(args['contractRef']),'report':report(result),
                        'amount':client.card(args['contractRef'])['derived']['periods'][args['period']-1]['amount']}
                    if result.status!='pass':return {'status':'rejected','data':{'operationId':operation,'report':report(result)}}
                elif name=='start':
                    if client.card(args['contractRef'])['inputs'] and 'materials' not in contract['binding']['preview']:
                        raise RuntimeFault('forbidden','Confirm all material sending stages before starting this older contract')
                self.store.checkpoint_set(self.namespace,operation,record)
            if record.get('result') is not None:return {'status':record['status'],'data':record['result']}
            if 'preview' in record and record['phase']=='prepared':
                binding={'actor':self.identity.agent_id,'operationId':operation,'subject':value,'stage':'delegation.'+name,'action':name,'preview':record['preview']}
                question=[{'question':'是否授权本次合同环节？','options':[{'value':'allow','label':'同意'},{'value':'deny','label':'拒绝'}]}]
                decision=self.runtime.confirmations.ensure(binding,kind='question' if name=='answer_step' else 'approval',questions=question if name=='answer_step' else None)
                if args.get('confirmationRef',decision['reference'])!=decision['reference']:raise RuntimeFault('id_conflict','Use this exact stage confirmation')
                if name=='answer_step':
                    answer=self.runtime.confirmations.native_answer(decision['reference'])
                    if answer is None:return {'status':'waiting','data':{'operationId':operation,'confirmationRef':decision['reference']}}
                    if len(answer)!=1 or answer[0].get('custom_input') or answer[0].get('selected_options') not in (['allow'],['deny']):raise RuntimeFault('forbidden','Choose the actual allow or deny option')
                    record['granted']=answer[0]['selected_options']==['allow']
                else:
                    if decision['status']!='approved':return {'status':'waiting' if decision['status']=='pending' else 'rejected','data':{'operationId':operation,'confirmationRef':decision['reference']}}
                    self.runtime.confirmations.claim(decision['reference'],binding)
                record=self.save(operation,record,phase='approved',approval=decision['reference'],binding=binding)
            job=self.runtime.scheduler.submit(self.identity.agent_id,contract['interactionId'],'assistant.fulfillment',{'operationId':operation},operation_id=operation,admitted=True)
            return {'status':job['status'] if job['status'] in ('completed','rejected','failed','unknown') else 'accepted','data':{'operationId':operation,'contractRef':args['contractRef'],'job':job}}

    def finish(self,operation,record,result):
        result={'operationId':operation,'contractRef':record['args']['contractRef'],**result}
        self.save(operation,record,phase='finished',status='completed',result=result)
        return JobOutcome('completed',result)

    def run(self,job):
        operation=job['payload']['operationId'];record=self.record(operation);args=record['args'];ref=args['contractRef'];client,_=self.client(ref)
        if record.get('result') is not None:return JobOutcome(record['status'],record['result'])
        if record['phase']=='executing':raise RuntimeFault('unknown_result','Reconcile original effects before continuing')
        if record.get('approval'):
            if record['name']=='answer_step':
                actual=self.runtime.confirmations.native_answer(record['approval'])
                if not actual or (actual[0]['selected_options']==['allow'])!=record['granted']:raise RuntimeFault('forbidden','The original user answer is unavailable')
            else:self.runtime.confirmations.claim(record['approval'],record['binding'])
        record=self.save(operation,record,phase='executing')
        self.runtime.require_execution()
        with client.transport.operation(operation):
            name=record['name']
            if name=='start':
                original=client.store.get('cards',ref)
                result={'receipt':original['card']['fulfillment']['receipt']} if original.get('state')=='started' else client.start(client.card(ref))
            elif name=='answer_step':result={'stepAuthorization':client.answer_step(ref,record['granted'])}
            elif name=='fetch':
                cached=client.store.get('cards',ref).get('deliveries',{}).get(str(args['period']))
                files={name:base64.b64decode(data) for name,data in cached.items()} if cached is not None else client.fetch_delivery(ref,args['period'])
                result={'period':args['period'],'files':{name:{'digest':digest_bytes(data),'size':len(data)} for name,data in files.items()}}
            elif name=='verify':result={'period':args['period'],'report':report(client.verify(ref,args['period']))}
            elif name=='accept':
                accepted=client.accept(ref,args['period']);result={'period':args['period'],'acceptance':accepted.status,'report':report(accepted.result),'card':accepted.card,'voucher':accepted.voucher,'payment':'paid' if accepted.voucher else 'unpaid'}
            elif name=='pay':result=self.payment(client,ref,args['period'])
            else:raise RuntimeFault('not_ready','Unknown fulfillment method')
        return self.finish(operation,record,result)

    def payment(self,client,ref,period):
        rec=client.store.get('cards',ref)
        if len(rec['card']['signatures']['7'])<period:raise RuntimeFault('forbidden','Payment requires the original accepted period')
        voucher=next((item for item in rec.get('vouchers',[]) if item.get('purpose')=='installment' and item.get('period')==period),None)
        if voucher:return {'period':period,'voucher':voucher,'payment':'paid'}
        # If the prior payer committed but its result was lost, read its actual
        # voucher. No second mandate or acceptance is created here.
        response=self.runtime.account.api('GET','/payments?'+urlencode({'ref':ref,'purpose':'installment','period':period}))
        voucher=response['voucher'];client.transport.send(rec['card']['provider'],'payment.notify',{'voucher':voucher})
        client.store.put('cards',ref,{**rec,'vouchers':[*rec.get('vouchers',[]),voucher]})
        return {'period':period,'voucher':voucher,'payment':'paid'}

    def recover(self):
        with self.lock:self._recover()

    def _recover(self):
        for operation,record in self.store.checkpoints(self.namespace).items():
            reference=self.runtime.scheduler.reference(self.identity.agent_id,'assistant.fulfillment',operation)
            try:job=self.runtime.scheduler.get(reference)
            except RuntimeFault:continue
            if job['status']!='unknown':continue
            if record.get('result') is not None:
                self.runtime.scheduler.resolve(reference,JobOutcome(record['status'],record['result']));continue
            client,_=self.client(record['args']['contractRef']);ref=record['args']['contractRef'];original=client.store.get('cards',ref)
            with client.transport.operation(operation):
                if record['name']=='start' and original.get('state')!='started':
                    received=client.transport.received('work.start')
                    body=received['result']['reply']['body'] if received and not received['result'].get('executionError') else {}
                    if body.get('receipt'):
                        card={**original['card'],'fulfillment':{**original['card']['fulfillment'],'receipt':body['receipt']}}
                        verify_card(client.template(card['templateDigest']),card,client.keys)
                        original={**original,'card':card,'state':'started','task':received['result']['reply']['taskId'],'steps':original.get('steps',[]),'notices':original.get('notices',[])}
                        client.store.put('cards',ref,original)
                if record['name']=='answer_step':
                    received=client.transport.received('step.authorize')
                    if received and received['result']['reply']['body'].get('ok') is True:
                        answer=received['call']['body']['stepAuthorization'];user=original['card']['user']
                        objects.verify_signed(answer,user['jwk'],signer=user['id'])
                        if answer['stepRequestDigest']!=record['args']['stepRequestDigest'] or answer['granted']!=record['granted']:raise RuntimeFault('protocol_error','Original answer receipt changed its real user decision')
                        for step in original['steps']:
                            if objects.object_digest(step['request'])==answer['stepRequestDigest']:step['answer']=answer
                        client.store.put('cards',ref,original)
                        self.runtime.scheduler.resolve(reference,self.finish(operation,record,{'stepAuthorization':answer}))
                        continue
                if record['name']=='fetch':
                    received=client.transport.received('acceptance.notify');cached=original.get('deliveries',{}).get(str(record['args']['period']))
                    if received and received['result']['reply']['body'].get('ok') is True and cached is not None:
                        self.runtime.scheduler.resolve(reference,self.finish(operation,record,{'period':record['args']['period'],'files':{name:{'digest':digest_bytes(base64.b64decode(data)),'size':len(base64.b64decode(data))} for name,data in cached.items()}}))
                        continue
            if record['name']=='start' and original.get('state')=='started':
                self.runtime.scheduler.resolve(reference,self.finish(operation,record,{'receipt':original['card']['fulfillment']['receipt']}))
            elif record['name']=='accept' and len(original['card']['signatures']['7'])>=record['args']['period']:
                voucher=next((item for item in original.get('vouchers',[]) if item.get('period')==record['args']['period'] and item.get('purpose')=='installment'),None)
                if voucher:self.runtime.scheduler.resolve(reference,self.finish(operation,record,{'period':record['args']['period'],'acceptance':'pass','card':original['card'],'voucher':voucher,'payment':'paid'}))
                else:
                    with client.transport.operation(operation):paid=client.transport.control_receipt('ap2.pay')
                    if paid and paid['result'].get('voucher'):
                        recovery=stable('receipt',operation)
                        helper=self.runtime.scheduler.submit(self.identity.agent_id,job['interactionId'],'assistant.receipt',{'operationId':operation},operation_id=recovery,admitted=True)
                        if helper['status']=='unknown':
                            with client.transport.operation(operation):notified=client.transport.received('payment.notify')
                            closed=any(frame['message'].get('kind')=='interaction.closed' and frame['message'].get('interactionId')==job['interactionId'] for frame in self.store.messages(self.identity.agent_id))
                            if closed or notified and notified['result']['reply']['body'].get('ok') is True:
                                outcome=self.reconcile_receipt({'payload':{'operationId':operation}})
                                self.runtime.scheduler.resolve(helper['reference'],outcome)

    def reconcile_receipt(self,job):
        operation=job['payload']['operationId'];record=self.record(operation);ref=record['args']['contractRef'];client,contract=self.client(ref)
        with client.transport.operation(operation):
            receipt=client.transport.control_receipt('ap2.pay')
            if not receipt or not receipt['result'].get('voucher'):raise RuntimeFault('unknown_result','Original actual payment receipt is missing')
            voucher=receipt['result']['voucher'];card=client.card(ref)
            from a2c.payment import verify_voucher
            verify_voucher(voucher,client.keys,payment_provider=card['paymentProvider'],purpose='installment',amount=card['derived']['periods'][record['args']['period']-1]['amount'],currency=contract['template']['pricing']['currency'],card_digest=ref,period=record['args']['period'])
            closed=any(frame['message'].get('kind')=='interaction.closed' and frame['message'].get('interactionId')==contract['interactionId'] for frame in self.store.messages(self.identity.agent_id))
            notified=client.transport.received('payment.notify')
            if not closed and not (notified and notified['result']['reply']['body'].get('ok') is True):client.transport.send(card['provider'],'payment.notify',{'voucher':voucher})
            original=client.store.get('cards',ref)
            if voucher not in original.get('vouchers',[]):client.store.put('cards',ref,{**original,'vouchers':[*original.get('vouchers',[]),voucher]})
        original_job=self.runtime.scheduler.reference(self.identity.agent_id,'assistant.fulfillment',operation)
        # The scanner and the short receipt job consume the same actual facts.
        # Keep their local finalization under one owner; an already-finalized job
        # must not turn the other recovery job into unknown and block later reads.
        with self.lock:
            if self.runtime.scheduler.get(original_job)['status']=='unknown':
                self.runtime.scheduler.resolve(original_job,self.finish(operation,record,{'period':record['args']['period'],'acceptance':'pass','card':client.card(ref),'voucher':voucher,'payment':'paid'}))
        return JobOutcome('completed',{'operationId':operation,'recoveredActualPayment':True})
