"""Verify with the upstream SDK, then atomically commit the ledger and original reply."""
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import math
import uuid

from a2c.card import card_digest,request_digest
from a2c.common import Directory,Endpoint,Reply,now_or
from a2c.errors import BAD_SIGNATURE,CARD_INVALID,OUT_OF_ORDER,fail
from a2c.codec import digest
from a2c.objects import signed
from a2c.payment.ap2 import jwt_decode,jwt_sign
from a2c.payment import verify_voucher
from a2c.payment.psp import PaymentProvider
from a2c.payment.terms import payment_terms,termination_balance

from ..errors import LabError
from ..identity.keys import thumbprint
from .ledger import money


log=logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedPayment:
    fingerprint:str
    payer:str
    payee:str
    provider:str
    payer_key:str
    payee_key:str
    ref:str
    purpose:str
    period:int|None
    amount:Decimal
    currency:str
    payment_id:str
    reply:str
    expires:float
    deposit_paid:bool
    client:str
    scope_digest:str|None=None
    client_owner:str=''
    payer_party:str=''
    payee_party:str=''
    refund:bool=False


class SimPaymentProvider(Endpoint):
    OPS=('ap2.pay',)

    def __init__(self,signer,*,keys,ap2_key,parties,wallets,client_matches,db,clock=None,on_transfer=None):
        super().__init__()
        self.signer,self.keys,self.ap2_key=signer,keys,ap2_key
        self.parties,self.wallets,self.client_matches=parties,wallets,client_matches
        self.db,self.now,self.on_transfer=db,now_or(clock),on_transfer
        self.requires_admission=lambda provider:False

    def _party(self,identity):
        try:
            party=self.parties(identity)
            if party['protocolId']!=identity:raise ValueError()
            thumbprint(party['jwk'])
            return party
        except (LabError,KeyError,TypeError,ValueError):fail(BAD_SIGNATURE,'The original payment party is not bound')

    def _party_key(self,identity):return self._party(identity)['jwk']

    @staticmethod
    def _time(claims,now):
        issued,expires=claims.get('iat'),claims.get('exp')
        if any(not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) for value in (issued,expires)):
            fail(BAD_SIGNATURE,'AP2 artifacts require finite iat and exp')
        if not issued<expires or expires-issued>900 or issued>now+60 or expires<=now:
            fail(BAD_SIGNATURE,'AP2 artifact is expired or outside the permitted window')
        return expires

    @staticmethod
    def _stored(row,body):
        subject=body['request'] if row['purpose']=='proposal-fee' else body['card']
        _,mandate=jwt_decode(body['checkoutMandate'].rstrip('~'))
        _,checkout=jwt_decode(mandate['checkout_jwt'])
        payee=checkout['merchant']['id'];client=subject['client']
        refund=row['purpose']=='termination' and payee==client['id']
        return PreparedPayment(row['fingerprint'],row['payer_id'],row['payee_id'],row['provider'],row['payer_key'],'',
            row['ref'],row['purpose'],row['period'] or None,money(row['amount']),row['currency'],
            row['payment_id'],row['reply'],0,False,json.dumps(client),subject.get('delegationScopeDigest'),
            row['payee_id'] if refund else row['payer_id'],body['template']['provider'] if refund else client['id'],payee,refund)

    def _deposit(self,ref):
        record=self.db.one("SELECT * FROM payment_records WHERE ref=? AND purpose='deposit' AND period=0",ref)
        marker=self.db.one("SELECT 1 FROM paid WHERE ref=? AND purpose='deposit' AND period IS NULL",ref)
        if bool(record)!=bool(marker):fail(OUT_OF_ORDER,'Original deposit evidence needs reconciliation')
        return record

    def prepare(self,body,files=None):
        """No platform mutation or accounting side effects during verification."""
        if files:fail(CARD_INVALID,'Payment requests contain no files')
        fingerprint=digest(body)
        previous=self.db.one('SELECT * FROM payment_records WHERE fingerprint=?',fingerprint)
        if previous:return self._stored(previous,body)
        header,pm=jwt_decode(body['paymentMandate'].rstrip('~'))
        payer_jwk=header.get('jwk') or fail(BAD_SIGNATURE,'Payment mandate has no payer key')
        payer_key=thumbprint(payer_jwk)
        _,cm=jwt_decode(body['checkoutMandate'].rstrip('~'))
        _,checkout=jwt_decode(cm['checkout_jwt'])
        binding=checkout['a2c'];purpose,period=binding['purpose'],binding.get('period')
        subject=body['request'] if purpose=='proposal-fee' else body['card']
        ref=request_digest(subject) if purpose=='proposal-fee' else card_digest(subject)
        deposit=self._deposit(ref) if purpose!='proposal-fee' else None
        terms=payment_terms(purpose,body,period,keys=self.keys,payment_provider=self.signer.id,deposit_paid=bool(deposit))
        client=self._party(subject['client']['id'])
        if not self.client_matches(client['owner'],subject['client']):
            fail(BAD_SIGNATURE,'Protocol client identity and key do not match the original account')
        payer,payee=self._party(terms.payer),self._party(terms.payee)
        if thumbprint(payer['jwk'])!=payer_key:
            fail(BAD_SIGNATURE,'Payer does not own the original protocol payment identity')
        template,ref,amount=terms.template,terms.ref,terms.amount
        if deposit:
            provider=self._party(template['provider'])
            if ((deposit['payer_id'],deposit['payee_id'],deposit['currency'])!=(client['owner'],provider['owner'],template['pricing']['currency'])
                    or Decimal(deposit['amount'])!=Decimal(str(subject['derived']['deposit']))):
                fail(CARD_INVALID,'Actual deposit does not match the original contract parties and amount')
        verifier=PaymentProvider(self.signer,keys=self.keys,ap2_key=self.ap2_key,payee_keys=self._party_key,clock=self.now)
        if deposit:verifier.paid.add((ref,'deposit',None))
        verifier._ids=iter([uuid.uuid4().hex])
        candidate=verifier.op_ap2_pay(body,{},None)
        expires=min(self._time(claims,self.now().timestamp()) for claims in (pm,cm,checkout))
        _,receipt=jwt_decode(candidate.body['paymentReceipt'])
        return PreparedPayment(fingerprint,payer['owner'],payee['owner'],template['provider'],payer_key,
            thumbprint(payee['jwk']),ref,purpose,period,money(amount),template['pricing']['currency'],receipt['payment_id'],
            json.dumps(candidate.body,ensure_ascii=False,separators=(',',':')),expires,bool(deposit),
            json.dumps(subject['client']),subject.get('delegationScopeDigest'),client['owner'],terms.payer,terms.payee,
            purpose=='termination' and terms.payer==template['provider'])

    def commit(self,conn,prepared):
        """No network/model work; admission and scope participate in this transaction."""
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Payment commit requires the owned platform transaction')
        key=(prepared.ref,prepared.purpose,prepared.period or 0)
        old=conn.execute('SELECT * FROM payment_records WHERE ref=? AND purpose=? AND period=?',key).fetchone()
        if old:
            if old['fingerprint']!=prepared.fingerprint:
                fail(OUT_OF_ORDER,'Payment exists with another original request; retrieve its original receipt')
            return Reply(json.loads(old['reply'])),False
        historical=conn.execute('''SELECT 1 FROM transfers t WHERE t.ref=? AND t.purpose=?
            AND NOT EXISTS(SELECT 1 FROM payment_records p WHERE p.payment_id=t.payment_id) LIMIT 1''',
            (prepared.ref,prepared.purpose)).fetchone()
        marker=conn.execute('SELECT 1 FROM paid WHERE ref=? AND purpose=? AND period IS ?',
            (prepared.ref,prepared.purpose,prepared.period)).fetchone()
        if marker or historical:fail(OUT_OF_ORDER,'Historical payment has no saved original receipt; reconciliation is required')
        payer,payee=self._party(prepared.payer_party),self._party(prepared.payee_party)
        if (not self.client_matches(prepared.client_owner,json.loads(prepared.client))
                or (payer['owner'],payee['owner'])!=(prepared.payer,prepared.payee)
                or thumbprint(payer['jwk'])!=prepared.payer_key or thumbprint(payee['jwk'])!=prepared.payee_key):
            fail(BAD_SIGNATURE,'Payment identity binding changed before commit')
        if prepared.purpose=='termination' and bool(self._deposit(prepared.ref))!=prepared.deposit_paid:
            fail(OUT_OF_ORDER,'Prior payments changed; obtain the current checkout before proceeding')
        committed_at=self.now()
        if prepared.expires<=committed_at.timestamp():fail(BAD_SIGNATURE,'Payment expired before commit')
        final=json.loads(prepared.reply)
        final['voucher']=signed(final['voucher'],self.signer,committed_at)
        _,receipt=jwt_decode(final['paymentReceipt']);receipt['iat']=int(committed_at.timestamp())
        final['paymentReceipt']=jwt_sign(receipt,self.ap2_key)
        encoded_reply=json.dumps(final,ensure_ascii=False,separators=(',',':'))
        self.wallets.transfer_in(conn,prepared.payer,prepared.payee,prepared.amount,purpose=prepared.purpose,
            ref=prepared.ref,payment_id=prepared.payment_id)
        conn.execute('INSERT INTO paid(ref,purpose,period,at) VALUES(?,?,?,?)',
            (prepared.ref,prepared.purpose,prepared.period,committed_at.timestamp()))
        conn.execute('INSERT INTO payment_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (prepared.payment_id,prepared.fingerprint,prepared.payer,prepared.payee,prepared.provider,
             *key,str(prepared.amount),prepared.currency,prepared.payer_key,encoded_reply,committed_at.timestamp()))
        return Reply(final),True

    def operation_in(self,conn,actor,operation):
        """Saved application `ap2.pay` receipt: owner, fingerprint and original result, or None."""
        if conn is not self.db.conn:raise RuntimeError('Use the owned payment operation connection')
        row=conn.execute('SELECT owner,fingerprint,result FROM delegation_payment_operations WHERE actor=? AND id=?',(actor,operation)).fetchone()
        return None if row is None else {'owner':row['owner'],'fingerprint':row['fingerprint'],'result':json.loads(row['result'])}

    def record_operation_in(self,conn,owner,actor,operation,fingerprint,result):
        """Save the receipt with the payment, capacity and budget facts of the same transaction."""
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Payment operation receipt requires the owned application transaction')
        conn.execute('INSERT INTO delegation_payment_operations VALUES(?,?,?,?,?)',(owner,actor,operation,fingerprint,json.dumps(result,ensure_ascii=False)))

    def project(self,prepared):
        if self.on_transfer:
            try:self.on_transfer({'payer':prepared.payer,'payee':prepared.payee,'amount':str(prepared.amount),
                'purpose':prepared.purpose,'ref':prepared.ref,'period':prepared.period,'paymentId':prepared.payment_id})
            except Exception:log.warning('Payment committed; observation requires recovery: %s',prepared.payment_id)

    def op_ap2_pay(self,body,files,task_id):
        prepared=self.prepare(body,files)
        if self.requires_admission(prepared.provider):
            fail(OUT_OF_ORDER,'Enrolled services require the current IoA execution connection and admission context')
        with self.db.tx() as conn:reply,created=self.commit(conn,prepared)
        if created:self.project(prepared)
        return reply

    def lookup(self,owner,payment_id=None,*,ref=None,purpose=None,period=None):
        row=(self.db.one('SELECT * FROM payment_records WHERE payment_id=?',payment_id) if payment_id else
            self.db.one('SELECT * FROM payment_records WHERE ref=? AND purpose=? AND period=?',ref,purpose,period or 0))
        if not row or owner not in (row['payer_id'],row['payee_id']):raise LabError('forbidden','Payment is unavailable',status=404)
        return {'paymentId':row['payment_id'],'ref':row['ref'],'purpose':row['purpose'],'period':row['period'] or None,
            'amount':row['amount'],'currency':row['currency'],**json.loads(row['reply'])}

    def evidence_in(self,conn,ref):
        """Verified original receipts; never return payment mandate credentials."""
        if conn is not self.db.conn:raise RuntimeError('Use the owned payment evidence connection')
        rows=conn.execute('SELECT * FROM payment_records WHERE ref=? ORDER BY purpose,period',(ref,)).fetchall()
        marked={(r['purpose'],r['period'] or 0) for r in conn.execute('SELECT purpose,period FROM paid WHERE ref=?',(ref,))}
        if marked!={(r['purpose'],r['period']) for r in rows}:
            raise LabError('history_gap','Original payment markers and receipts disagree',status=409,recovery='reconcile_original')
        keys=Directory();keys.add_signer(self.signer)
        payment_provider={'id':self.signer.id,'jwksUri':self.signer.id+'/.well-known/jwks.json'}
        values=[]
        for row in rows:
            voucher=json.loads(row['reply'])['voucher'];proposal=row['purpose']=='proposal-fee'
            verify_voucher(voucher,keys,payment_provider=payment_provider,purpose=row['purpose'],amount=row['amount'],
                currency=row['currency'],card_digest=None if proposal else ref,request_digest=ref if proposal else None,period=row['period'] or None)
            values.append({'paymentId':row['payment_id'],'purpose':row['purpose'],'period':row['period'] or None,
                'amount':row['amount'],'currency':row['currency'],'payer':row['payer_id'],'payee':row['payee_id'],
                'provider':row['provider'],'voucher':voucher})
        return values

    def require_payments_in(self,conn,*,ref,currency,payer,payee,amounts):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Payment evidence needs the owned application transaction')
        for purpose,period,amount in amounts:
            if Decimal(str(amount))<=0:continue
            row=conn.execute('SELECT * FROM payment_records WHERE ref=? AND purpose=? AND period=?',(ref,purpose,period or 0)).fetchone()
            if not row or (row['payer_id'],row['payee_id'],row['currency'])!=(payer,payee,currency) or Decimal(row['amount'])!=Decimal(str(amount)):
                raise LabError('not_ready','Actual required contract payment is missing; do not release its admission',status=409,recovery='reconcile_original')

    def require_contract_payments_in(self,conn,template,card,*,keys):
        ref=card_digest(card);deposit=self._deposit(ref)
        client,provider=self._party(card['client']['id'])['owner'],self._party(template['provider'])['owner']
        termination=card['fulfillment'].get('termination')
        amounts=[('installment',i+1,value['amount']) for i,value in enumerate(card['derived']['periods'][:len(card['signatures']['7'])])]
        if not termination or deposit or card['fulfillment'].get('receipt'):
            amounts.append(('deposit',None,card['derived']['deposit']))
        self.require_payments_in(conn,ref=ref,currency=template['pricing']['currency'],payer=client,payee=provider,amounts=amounts)
        if termination and termination.get('quote'):
            balance=termination_balance(template,card,termination,keys,deposit_paid=bool(deposit))
            self.require_payments_in(conn,ref=ref,currency=template['pricing']['currency'],
                payer=client if balance>0 else provider,payee=provider if balance>0 else client,
                amounts=[('termination',None,abs(balance))])

    def termination_balance_in(self,conn,template,card,*,keys):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Settlement needs the owned application transaction')
        voucher=self.deposit_voucher_in(conn,template,card)
        return termination_balance(template,card,card['fulfillment'].get('termination'),keys,deposit_paid=bool(voucher))

    def deposit_voucher_in(self,conn,template,card):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Deposit evidence needs the owned application transaction')
        ref=card_digest(card);record=self._deposit(ref)
        if record is None:return None
        self.require_payments_in(conn,ref=ref,currency=template['pricing']['currency'],
            payer=self._party(card['client']['id'])['owner'],payee=self._party(template['provider'])['owner'],
            amounts=[('deposit',None,card['derived']['deposit'])])
        if card['paymentProvider']['id']!=self.signer.id:fail(CARD_INVALID,'Use this contract original payment provider')
        keys=Directory();keys.add_signer(self.signer)
        voucher=json.loads(record['reply'])['voucher']
        verify_voucher(voucher,keys,payment_provider=card['paymentProvider'],purpose='deposit',
            amount=card['derived']['deposit'],currency=template['pricing']['currency'],card_digest=ref)
        return voucher
