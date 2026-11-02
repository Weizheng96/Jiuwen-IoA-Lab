"""Owned AP2 attempt evidence; actual payments are queried through public APIs."""
import copy
from urllib.parse import quote, urlencode

import httpx
from a2c.card import card_digest, request_digest
from a2c.codec import digest
from a2c.common import Directory, Reply
from a2c.payment import PaymentRejected, PaymentReconciliation, verify_voucher
from a2c.payment.ap2 import jwt_decode, jwt_verify

from ..runtime.protocol import RuntimeFault


class PaymentRecovery:
    def __init__(self, transport):
        self.transport=transport
        self.runtime,self.store=transport.runtime,transport.runtime.store
        self.actor=transport.identity.agent_id

    def binding(self, body):
        """Locate a payment key; this never authorizes a new payment."""
        try:
            _,mandate=jwt_decode(body['checkoutMandate'].rstrip('~'))
            _,checkout=jwt_decode(mandate['checkout_jwt'])
            value=checkout['a2c'];purpose=value['purpose'];period=value.get('period')
            subject=body['request'] if purpose=='proposal-fee' else body['card']
            ref=request_digest(subject) if purpose=='proposal-fee' else card_digest(subject)
            field='requestDigest' if purpose=='proposal-fee' else 'cardDigest'
            if value.get(field)!=ref:return None
            return {'ref':ref,'purpose':purpose,'period':period}
        except (KeyError,TypeError,ValueError,AttributeError):return None

    def controls(self):
        from .transport import stable
        found={}
        for key,record in self.store.checkpoints(self.transport.namespace).items():
            if not key.startswith('control:') or not isinstance(record,dict):continue
            payload=record.get('payload')
            if not isinstance(payload,dict) or set(payload)!={'admissionId','ap2'}:continue
            if payload['admissionId']!=self.transport.admission:continue
            if record.get('fingerprint')!=digest({'payload':payload}):
                raise RuntimeFault('unknown_result','Original payment checkpoint is inconsistent','reconcile_original')
            if 'operationId' in record:
                operation=record['operationId'];parent=record.get('parentOperationId')
            elif key=='control:ap2.pay' or key.endswith(':ap2.pay'):
                parent=None if key=='control:ap2.pay' else key[len('control:'):-len(':ap2.pay')]
                values=[self.actor,self.transport.relation['id']]
                if parent is not None:values.append(parent)
                operation=stable(*values,'ap2.pay')
            else:continue
            attempt={'operationId':operation,'body':copy.deepcopy(payload['ap2']),
                     'bodyDigest':digest(payload['ap2']),'payload':copy.deepcopy(payload),
                     'parentOperationId':parent}
            if operation in found and found[operation]['payload']!=payload:
                raise RuntimeFault('unknown_result','Original payment operation has conflicting bodies','reconcile_original')
            found[operation]=attempt
        return list(found.values())

    def attempts(self, ref, purpose='installment', period=None):
        expected={'ref':ref,'purpose':purpose,'period':period}
        found={value['operationId']:value for value in self.controls() if self.binding(value['body'])==expected}
        # A pending outbox row without its expected checkpoint must also block
        # a new authorization; losing a local link is not proof of nonpayment.
        for row in self.store.pending():
            payload=row['payload']
            if row['actor']!=self.actor or row['kind']!='ap2.pay' or payload.get('admissionId')!=self.transport.admission:continue
            body=payload.get('ap2')
            if self.binding(body)==expected and row['id'] not in found:
                found[row['id']]={'operationId':row['id'],'body':copy.deepcopy(body),'bodyDigest':digest(body),
                                  'payload':copy.deepcopy(payload),'checkpointMissing':True}
        for value in found.values():
            row=self.store.operation(self.actor,'ap2.pay',value['operationId'])
            if row and row['payload']!=value['payload']:
                raise RuntimeFault('unknown_result','Original payment outbox differs from its frozen body','reconcile_original')
            value['response']=row['response'] if row else None
            value['outboxPresent']=row is not None
        return list(found.values())

    def get(self, path):
        try:return self.runtime.account.api('GET',path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code==404:return None
            raise RuntimeFault('unknown_result','Original payment evidence is unavailable','reconcile_original') from None
        except httpx.TransportError:
            raise RuntimeFault('unknown_result','Original payment evidence is unavailable','reconcile_original') from None

    def verified_response(self, response, ref, purpose, period, attempts):
        from .store import ProtocolStore
        protocol=ProtocolStore(self.store,self.actor,self.transport.relation['id'])
        original=protocol.get('cards',ref)
        body=attempts[0]['body'] if attempts else {}
        card=original['card'] if original else body.get('card')
        template=protocol.get('templates',card['templateDigest']) if card else None
        template=template or body.get('template')
        if not card or not template or card_digest(card)!=ref or card['client']['id']!=self.transport.identity.signer.id:
            raise RuntimeFault('unknown_result','Restore the owned original payment card and template','reconcile_original')
        amount=card['derived']['periods'][period-1]['amount']
        keys=Directory(fetch=self.transport.jwks)
        verify_voucher(response['voucher'],keys,payment_provider=card['paymentProvider'],purpose=purpose,
                       amount=amount,currency=template['pricing']['currency'],card_digest=ref,period=period)
        header,_=jwt_decode(response['paymentReceipt'])
        receipt=jwt_verify(response['paymentReceipt'],keys.jwk(card['paymentProvider']['id'],header.get('kid')))
        if (receipt.get('iss')!=card['paymentProvider']['id'] or receipt.get('status')!='Success'
                or not receipt.get('payment_id') or response.get('paymentId',receipt['payment_id'])!=receipt['payment_id']):
            raise RuntimeFault('protocol_error','Actual payment receipt does not match its payment provider')
        return copy.deepcopy(response)

    def rejected(self, attempt):
        frame=attempt.get('response')
        if (not attempt.get('outboxPresent') or attempt.get('checkpointMissing') or not isinstance(frame,dict)
                or frame.get('agentId')!=self.actor or frame.get('operation')!='ap2.pay'
                or frame.get('operationId')!=attempt['operationId'] or frame.get('ok') is not False):return None
        error=frame.get('error') or {}
        if (error.get('code')!='payment_rejected' and not (error.get('code')=='forbidden'
                and error.get('message')=='Payment changed or expired before transaction commit')):return None
        if frame.get('outcome') not in (None,'rejected'):return None
        return {'reference':attempt['operationId'],'bodyDigest':attempt['bodyDigest'],
                'code':error['code'],'message':error.get('message') or 'Original payment submission was rejected'}

    def recovery(self, ref, period):
        purpose='installment';attempts=self.attempts(ref,purpose,period)
        state={'attempts':[],'rejection':None,'response':None,'noPending':False}
        actual=self.get('/payments?'+urlencode({'ref':ref,'purpose':purpose,'period':period}))
        if actual is not None:
            state['response']=self.verified_response(actual,ref,purpose,period,attempts)
        for attempt in attempts:
            item={'operationId':attempt['operationId'],'bodyDigest':attempt['bodyDigest'],'status':'unknown'}
            result=self.get('/operations/'+quote(attempt['operationId'],safe='')+'?'+urlencode({'agentId':self.actor,'operation':'ap2.pay'}))
            if result is not None:
                state['response']=self.verified_response(result,ref,purpose,period,attempts)
                item['status']='paid'
            elif state['response'] is None:
                rejection=self.rejected(attempt)
                if rejection:
                    item.update(status='rejected',error={'code':rejection['code'],'message':rejection['message']})
                    state['rejection']=rejection
            state['attempts'].append(item)
        state['noPending']=bool(state['response'] is None and state['attempts']
                                and all(item['status']=='rejected' for item in state['attempts']))
        # One known rejection cannot stand for a different unknown attempt.
        if not state['noPending']:state['rejection']=None
        return state

    def known_rejection(self, attempt):
        binding=self.binding(attempt['body'])
        if not binding or binding['purpose']!='installment':return None
        state=self.recovery(binding['ref'],binding['period'])
        if not state['noPending']:return None
        actual=next((value for value in self.attempts(binding['ref'],binding['purpose'],binding['period'])
                     if value['operationId']==attempt['operationId'] and value['bodyDigest']==attempt['bodyDigest']),None)
        rejected=self.rejected(actual) if actual else None
        return PaymentRejected(payment_provider=self.runtime.account.config['pay'],body_digest=rejected['bodyDigest'],
                               reference=rejected['reference'],code=rejected['code'],message=rejected['message']) if rejected else None

    def reconcile(self, value):
        ref=value.get('ref',{});period=value.get('period');psp=self.runtime.account.config['pay']
        expected=digest({'psp':psp,'payer':self.transport.identity.payment.jwk,'ref':ref,
                         'purpose':value.get('purpose'),'period':period})
        if (value.get('paymentProvider')!=psp or value.get('key')!=expected
                or value.get('purpose')!='installment' or set(ref)!={'cardDigest'}):
            raise RuntimeFault('unknown_result','Payment reconciliation does not belong to this original key','reconcile_original')
        attempts=self.attempts(ref['cardDigest'],'installment',period)
        for original in value.get('attempts',[]):
            if not any(item['body']==original.get('body') and item['operationId']==original.get('rejection',{}).get('reference')
                       and item['bodyDigest']==original.get('rejection',{}).get('bodyDigest') for item in attempts):
                raise RuntimeFault('unknown_result','Restore every original payment attempt before retrying','reconcile_original')
        state=self.recovery(ref['cardDigest'],period)
        return PaymentReconciliation(value['key'],response=state['response'],no_pending=state['noPending'])

    def send(self, payload):
        from .transport import stable
        body=payload['ap2'];body_digest=digest(body);parent=self.transport._operation.get()
        prior=next((item for item in self.controls() if item['payload']==payload),None)
        binding=self.binding(body)
        if prior is None and binding and binding['purpose']=='installment' and self.attempts(**binding):
            state=self.recovery(binding['ref'],binding['period'])
            if state['response'] is not None:return Reply(state['response'])
            if not state['noPending']:
                raise RuntimeFault('unknown_result','Recover the original payment body and control identity','reconcile_original')
        key=self.transport._key('control','ap2.pay')
        old=self.store.checkpoint(self.transport.namespace,key)
        operation=prior['operationId'] if prior else self.transport._identity('ap2.pay')
        if old and old['payload']!=payload:
            key+=':'+body_digest
            if prior is None:operation=stable(self.actor,self.transport.relation['id'],parent,'ap2.pay',body_digest)
        record={'fingerprint':digest({'payload':payload}),'payload':copy.deepcopy(payload),
                'operationId':operation,'parentOperationId':prior.get('parentOperationId') if prior else parent}
        existing=self.store.checkpoint(self.transport.namespace,key)
        if existing and existing['payload']!=payload:
            raise RuntimeFault('id_conflict','Original payment attempt changed its frozen body')
        if existing is None:self.store.checkpoint_set(self.transport.namespace,key,record)
        self.store.checkpoint_set(self.transport.namespace,self.transport._key('payment-last','ap2.pay'),
                                  {'operationId':operation,'bodyDigest':body_digest})
        attempt=next(item for item in self.controls() if item['operationId']==operation)
        if binding and binding['purpose']=='installment':
            recovered=self.recovery(binding['ref'],binding['period'])
            if recovered['response'] is not None:return Reply(recovered['response'])
        rejection=self.known_rejection(attempt)
        if rejection:raise rejection
        try:result=self.runtime.connection.call(self.actor,'ap2.pay',payload,operation_id=operation)
        except RuntimeFault:
            # A first submission has no saved frame before call(). Only the
            # actual authenticated reply plus current public evidence can turn
            # its failure into the upstream's trusted local rejection type.
            rejection=self.known_rejection(attempt)
            if rejection:raise rejection
            raise
        if 'error' in result:raise RuntimeFault('unknown_result','Original payment result needs reconciliation','reconcile_original')
        return Reply(result)

    def receipt(self):
        pointer=self.store.checkpoint(self.transport.namespace,self.transport._key('payment-last','ap2.pay'))
        controls=self.controls()
        if pointer:
            attempt=next((item for item in controls if item['operationId']==pointer['operationId']),None)
        else:
            record=self.store.checkpoint(self.transport.namespace,self.transport._key('control','ap2.pay'))
            operation=record.get('operationId',self.transport._identity('ap2.pay')) if record else None
            attempt=next((item for item in controls if item['operationId']==operation),None)
        if not attempt:return None
        frame=self.store.response(self.actor,'ap2.pay',attempt['operationId'])
        return {'payload':attempt['payload'],'result':frame['result']} if frame and frame.get('ok') is True else None
