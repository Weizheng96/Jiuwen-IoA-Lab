"""Original A2C termination decisions and the same-contract AP2 refund receiver."""
from decimal import Decimal
from urllib.parse import urlencode
import httpx

from a2c import objects
from a2c.codec import digest,parse_duration,parse_time
from a2c.common import Endpoint,Reply
from a2c.payment import verify_voucher
from a2c.payment.terms import termination_balance,verify_quote

from ..adapters.endpoint import EndpointSession
from ..adapters.transport import stable
from ..runtime.protocol import RuntimeFault


class RefundReceiver(Endpoint):
    OPS=('ap2.checkout',)

    def __init__(self,client):
        super().__init__()
        self.client,self.store=client,client.store

    def op_ap2_checkout(self,body,files,task_id):
        if set(body)!={'purpose','cardDigest'} or body['purpose']!='termination' or files or task_id:
            raise RuntimeFault('forbidden','Only the original agreed termination refund is receivable')
        return Reply({'op':'ap2.checkout','checkoutJwt':self.client.refund_checkout(body['cardDigest'])})


class Termination:
    def __init__(self,fulfillment):
        self.fulfillment=fulfillment
        self.role,self.runtime,self.store,self.identity=fulfillment.role,fulfillment.runtime,fulfillment.store,fulfillment.identity
        self.receivers={}

    def call(self,name,operation,args):
        f=self.fulfillment
        if name=='terminate':
            if args['mode'] not in ('agreed','unilateral') or ('terminationQuoteRef' in args)!=(args['mode']=='agreed'):
                raise RuntimeFault('invalid_input','Agreed termination requires its original quote; unilateral termination does not')
            if not isinstance(args.get('reason',''),str) or len(args.get('reason',''))>1000:
                raise RuntimeFault('invalid_input','Use a bounded local termination reason')
        with f.lock:
            value={key:item for key,item in args.items() if key!='confirmationRef'}
            fingerprint=digest({'name':name,'args':value})
            record=self.store.checkpoint(f.namespace,operation)
            if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original termination decision changed')
            client,contract=f.client(args['contractRef'])
            if not record:
                record={'name':name,'args':value,'fingerprint':fingerprint,'phase':'prepared','handler':'assistant.termination'}
                if name=='terminate':
                    original=client.store.get('cards',args['contractRef'])
                    quote=None
                    if args['mode']=='agreed':
                        source=f.record(args['terminationQuoteRef'])
                        if source['name']!='termination_quote' or source['args']['contractRef']!=args['contractRef'] or source.get('status')!='completed':
                            raise RuntimeFault('forbidden','Use a completed quote for this original contract')
                        quote=source['result']['quote']
                        verify_quote(contract['template'],original['card'],quote,client.keys)
                    # A preview is not a signed notice. Verify the quote, then
                    # display its fee less the actual paid deposit without signing.
                    balance=Decimal(str(quote['fee']))-(Decimal(str(original['card']['derived']['deposit'])) if original.get('depositVoucher') else Decimal(0)) if quote is not None else None
                    record.update(quote=quote,preview={'title':'确认解除本合同','contractRef':args['contractRef'],
                        'mode':args['mode'],'quote':quote,'card':original['card'],'depositVoucher':original.get('depositVoucher'),
                        'balance':str(balance) if balance is not None else None,'reason':args.get('reason',''),
                        'notice':'解除、通知送达、补款或退款、执行收尾及结清分别核实；单方解除不形成协商结算依据'})
                f.save(operation,record)
            if record.get('result') is not None:return {'status':record['status'],'data':record['result']}
            if name=='terminate' and record['phase']=='prepared':
                binding,decision=f.confirmation(operation,record)
                if args.get('confirmationRef',decision['reference'])!=decision['reference']:raise RuntimeFault('id_conflict','Use this exact termination confirmation')
                data={'operationId':operation,'contractRef':args['contractRef'],'confirmationRef':decision['reference']}
                if decision['status']!='approved':
                    if decision['status']!='pending':f.save(operation,record,phase='finished',status='rejected',result=data)
                    return {'status':'waiting' if decision['status']=='pending' else 'rejected','data':data}
                self.runtime.confirmations.claim(decision['reference'],binding)
                record=f.save(operation,record,phase='approved',approval=decision['reference'],binding=binding)
            return f.schedule(operation,record,contract)

    def quote_result(self,operation,record,quote,client,contract):
        header=verify_quote(contract['template'],client.card(record['args']['contractRef']),quote,client.keys)
        return self.fulfillment.finish(operation,record,{'terminationQuoteRef':operation,'quote':quote,
            'quoteDigest':objects.object_digest(quote),'respondUntil':(parse_time(header['a2c-ts'])+
                parse_duration(contract['template']['deadlines']['respondWithin'])).isoformat()})

    def facts(self,client,contract):
        original=client.store.get('cards',contract['result']['contractRef'])
        pending=original.get('terminationPending',{})
        term=original['card']['fulfillment'].get('termination')
        closed=any(frame['message'].get('kind')=='interaction.closed' and
            frame['message'].get('interactionId')==contract['interactionId'] for frame in self.store.messages(self.identity.agent_id))
        balance=termination_balance(contract['template'],original['card'],term,client.keys,
            deposit_paid=bool(original.get('depositVoucher'))) if term and term.get('quote') else None
        state='unagreed' if balance is None else 'not_required' if balance==0 else 'paid' if balance>0 and original.get('terminationVoucher') else 'refund_pending' if balance<0 else 'payment_pending'
        voucher=original.get('terminationVoucher')
        if balance is not None and balance<0:
            try:
                paid=self.runtime.account.api('GET','/payments?'+urlencode({'ref':contract['result']['contractRef'],'purpose':'termination'}))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code!=404:raise
            else:
                voucher=paid['voucher']
                verify_voucher(voucher,client.keys,payment_provider=original['card']['paymentProvider'],purpose='termination',
                    amount=-balance,currency=contract['template']['pricing']['currency'],card_digest=contract['result']['contractRef'])
                state='refunded'
        return {'terminated':bool(term),'termination':term,'notification':'acknowledged' if pending.get('notified') else 'pending',
            'settlement':state,'balance':str(balance) if balance is not None else None,
            'voucher':voucher,'closed':closed}

    def run(self,job):
        f=self.fulfillment;operation=job['payload']['operationId'];record=f.record(operation)
        if record.get('result') is not None:
            from ..runtime.scheduling import JobOutcome
            return JobOutcome(record['status'],record['result'])
        client,contract=f.client(record['args']['contractRef']);ref=record['args']['contractRef']
        self.runtime.require_execution()
        with client.transport.operation(operation):
            if record['name']=='termination_quote':
                return self.quote_result(operation,record,client.termination_quote(ref),client,contract)
            original=client.store.get('cards',ref)
            if not original.get('terminationPending'):
                if original['card']!=record['preview']['card'] or original.get('depositVoucher')!=record['preview']['depositVoucher']:
                    return f.finish(operation,record,{'reason':'preview_changed','nextAction':'preview_termination_again'},state='rejected')
                self.runtime.confirmations.claim(record['approval'],record['binding'])
            client.terminate(ref,quote=record['quote'])
        return f.finish(operation,record,self.facts(client,contract))

    def recover(self):
        f=self.fulfillment
        with f.lock:
            for operation,record in self.store.checkpoints(f.namespace).items():
                if record['name'] not in ('termination_quote','terminate'):continue
                reference=self.runtime.scheduler.reference(self.identity.agent_id,'assistant.termination',operation)
                try:job=self.runtime.scheduler.get(reference)
                except RuntimeFault:continue
                if job['status']!='unknown' or self.runtime.scheduler.is_executing(reference):continue
                from ..runtime.scheduling import JobOutcome
                if record.get('result') is not None:
                    self.runtime.scheduler.resolve(reference,JobOutcome(record['status'],record['result']));continue
                client,contract=f.client(record['args']['contractRef']);ref=record['args']['contractRef']
                with client.transport.operation(operation):
                    if record['name']=='termination_quote':
                        received=client.transport.received('termination.quote')
                        if received and not received['result'].get('executionError'):
                            quote=received['result']['reply']['body'].get('terminationQuote')
                            if quote:self.runtime.scheduler.resolve(reference,self.quote_result(operation,record,quote,client,contract))
                        continue
                    original=client.store.get('cards',ref);pending=original.get('terminationPending')
                    if not pending or pending['quote']!=record['quote']:continue
                    # Original signed notices/payment parameters stay frozen. A
                    # resume is bounded by new original evidence, not a new user
                    # decision, expiry extension or a generic force-rerun path.
                    proof={'pending':pending,'notice':client.transport.received('termination.notify'),
                        'payment':client.transport.control_receipt('ap2.pay'),'paymentNotice':client.transport.received('payment.notify')}
                    self.runtime.scheduler.requeue(reference,stable('resume-termination',operation,digest(proof)),{'originalTermination':digest(pending['termination'])})

    def receiver(self,client,contract):
        endpoint=self.receivers.get(contract['interactionId'])
        if endpoint is None:
            endpoint=EndpointSession(self.runtime,self.identity,contract['interactionId'],RefundReceiver(client))
            self.receivers[contract['interactionId']]=endpoint
        return endpoint

    def scan(self):
        """Deterministic original-peer receipts work without a host/model."""
        for frame in self.store.messages(self.identity.agent_id):
            message=frame['message']
            if message.get('kind')!='a2c.request' or message.get('payloadAvailable') is False:continue
            relation=self.store.interaction(interaction_id=message.get('interactionId'))
            if not relation or relation['actor']!=self.identity.agent_id or relation['direction']!='outbound' or relation['name']!='delegation' or relation['peer']!=message['sender']:
                raise RuntimeFault('forbidden','Refund request changed its original delegation relationship')
            ref=message['body'].get('body',{}).get('cardDigest')
            client,contract=self.fulfillment.client(ref)
            if contract['interactionId']!=relation['id'] or contract['admissionId']!=message['body']['admissionId']:
                raise RuntimeFault('forbidden','Refund request changed its original contract or admission')
            handler='assistant.refund';reference=self.runtime.scheduler.reference(self.identity.agent_id,handler,message['messageId'])
            try:
                job=self.runtime.scheduler.get(reference)
                if job['status']=='unknown' and not self.runtime.scheduler.is_executing(reference):
                    result=self.receiver(client,contract).recover(message)
                    if result:
                        from ..runtime.scheduling import JobOutcome
                        self.runtime.scheduler.resolve(reference,JobOutcome('completed',result))
            except RuntimeFault as exc:
                if exc.code!='not_ready':raise
                self.runtime.scheduler.submit(self.identity.agent_id,relation['id'],handler,{'messageId':message['messageId'],'contractRef':ref},operation_id=message['messageId'],admitted=True)

    def receive(self,job):
        from ..runtime.scheduling import JobOutcome
        ref=job['payload']['contractRef'];client,contract=self.fulfillment.client(ref)
        endpoint=self.receiver(client,contract)
        message=next(frame['message'] for frame in self.store.messages(self.identity.agent_id)
            if frame['message']['messageId']==job['payload']['messageId'])
        return JobOutcome('completed',endpoint.run(message))
