"""Stage with the upstream verifier, then commit the ledger and original receipt together."""
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import math
import uuid

from a2c.common import Reply
from a2c.errors import BAD_SIGNATURE, CARD_INVALID, OUT_OF_ORDER, fail
from a2c.codec import digest
from a2c.objects import signed
from a2c.payment.ap2 import jwt_decode, jwt_sign
from a2c.payment.psp import PaymentProvider

from ..errors import LabError
from ..identity.keys import thumbprint
from .ledger import money


log = logging.getLogger(__name__)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS payment_records(
 payment_id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,
 payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,provider TEXT NOT NULL,
 ref TEXT NOT NULL,purpose TEXT NOT NULL,period INTEGER NOT NULL,
 amount TEXT NOT NULL,currency TEXT NOT NULL,payer_key TEXT NOT NULL,
 reply TEXT NOT NULL,created REAL NOT NULL,UNIQUE(ref,purpose,period));
'''


@dataclass(frozen=True)
class PreparedPayment:
    fingerprint: str
    payer: str
    payee: str
    provider: str
    payer_key: str
    payee_key: str
    ref: str
    purpose: str
    period: int | None
    amount: Decimal
    currency: str
    payment_id: str
    reply: str
    expires: float
    deposit_paid: bool
    client: str
    scope_digest: str|None=None


class _PaidView:
    """Read-only bridge needed by upstream termination amount calculation."""
    def __init__(self,db):
        self.db = db

    def __contains__(self,key):
        ref,purpose,period = key
        return bool(self.db.one('SELECT 1 FROM paid WHERE ref=? AND purpose=? AND period IS ?',ref,purpose,period))


class SimPaymentProvider(PaymentProvider):
    def __init__(self,signer,*,keys,ap2_key,payee_keys,wallets,payee_of,client_matches,db,clock=None,on_transfer=None):
        super().__init__(signer,keys=keys,ap2_key=ap2_key,payee_keys=payee_keys,clock=clock)
        # payee_keys/payee_of are local identity-directory reads, with no network I/O.
        self.db,self.wallets,self.payee_of,self.on_transfer = db,wallets,payee_of,on_transfer
        self.client_matches = client_matches
        self.requires_admission=lambda provider:False
        self.paid = _PaidView(db)
        with db.lock:
            db.conn.executescript(SCHEMA)

    @staticmethod
    def _time(claims,now):
        issued,expires = claims.get('iat'),claims.get('exp')
        if any(not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value)
               for value in (issued,expires)):
            fail(BAD_SIGNATURE,'AP2 artifacts require finite iat and exp')
        if not issued<expires or expires-issued>900 or issued>now+60 or expires<=now:
            fail(BAD_SIGNATURE,'AP2 artifact is expired or outside the permitted window')
        return expires

    @staticmethod
    def _stored(row):
        return PreparedPayment(row['fingerprint'],row['payer_id'],row['payee_id'],row['provider'],row['payer_key'],'',
                               row['ref'],row['purpose'],row['period'] or None,money(row['amount']),row['currency'],
                               row['payment_id'],row['reply'],0,False,'')

    def prepare(self,body,files=None):
        """No platform mutation if protocol validation or the later transaction fails."""
        fingerprint = digest(body)
        previous = self.db.one('SELECT * FROM payment_records WHERE fingerprint=?',fingerprint)
        if previous:
            return self._stored(previous)
        pm_jwt,cm_jwt = body['paymentMandate'].rstrip('~'),body['checkoutMandate'].rstrip('~')
        header,pm = jwt_decode(pm_jwt)
        payer_jwk = header.get('jwk') or fail(BAD_SIGNATURE,'Payment mandate has no payer key')
        payer_key = thumbprint(payer_jwk)
        payer = self.wallets.by_thumbprint(payer_key) or fail(BAD_SIGNATURE,'Payment key is not registered')
        _,cm = jwt_decode(cm_jwt)
        _,checkout = jwt_decode(cm['checkout_jwt'])
        binding = checkout['a2c']
        purpose,period = binding['purpose'],binding.get('period')
        if purpose not in ('proposal-fee','deposit','installment','termination'):
            fail(CARD_INVALID,'Unsupported payment purpose')
        if (purpose=='installment' and (not isinstance(period,int) or isinstance(period,bool) or period<1)) or (purpose!='installment' and period is not None):
            fail(CARD_INVALID,'Invalid payment period')
        template,ref,amount = self._amount(purpose,body,period)
        subject = body['request'] if purpose=='proposal-fee' else body['card']
        if not self.client_matches(payer,subject['client']):
            fail(BAD_SIGNATURE,'Payer does not own the protocol client identity and key')
        provider = template['provider']
        payee = self.payee_of(provider) or fail(CARD_INVALID,'Payment recipient is not registered')
        reference = 'requestDigest' if purpose=='proposal-fee' else 'cardDigest'
        if binding.get(reference)!=ref or binding.get('currency')!=template['pricing']['currency'] or checkout.get('currency')!=template['pricing']['currency']:
            fail(CARD_INVALID,'Checkout is not bound to the actual protocol object and currency')
        payee_jwk = self.payee_keys(provider)
        verifier = PaymentProvider(self.signer,keys=self.keys,ap2_key=self.ap2_key,
                                   payee_keys=lambda actor:payee_jwk if actor==provider else fail(BAD_SIGNATURE,'Unknown payee'),clock=self.now)
        # Only this temporary reference instance receives candidate paid/ledger changes.
        deposit_paid = (ref,'deposit',None) in self.paid
        if deposit_paid and purpose=='termination':
            verifier.paid.add((ref,'deposit',None))
        verifier._ids = iter([uuid.uuid4().hex])
        candidate = verifier.op_ap2_pay(body,files or {},None)
        current = self.now().timestamp()
        expires = min(self._time(claims,current) for claims in (pm,cm,checkout))
        _,receipt = jwt_decode(candidate.body['paymentReceipt'])
        return PreparedPayment(fingerprint,payer,payee,provider,payer_key,thumbprint(payee_jwk),ref,purpose,period,
                               money(amount),template['pricing']['currency'],receipt['payment_id'],
                               json.dumps(candidate.body,ensure_ascii=False,separators=(',',':')),expires,deposit_paid,json.dumps(subject['client']),subject.get('delegationScopeDigest'))

    def commit(self,conn,prepared):
        """No network or model work; admission can participate in this same transaction."""
        if conn is not self.db.conn or not conn.in_transaction:
            raise RuntimeError('Payment commit requires the owned platform transaction')
        key = (prepared.ref,prepared.purpose,prepared.period or 0)
        old = conn.execute('SELECT * FROM payment_records WHERE ref=? AND purpose=? AND period=?',key).fetchone()
        if old:
            if old['fingerprint']!=prepared.fingerprint:
                fail(OUT_OF_ORDER,'Payment exists with another original request; retrieve its original receipt')
            return Reply(json.loads(old['reply'])),False
        historical = conn.execute('''SELECT 1 FROM transfers t WHERE t.ref=? AND t.purpose=?
            AND NOT EXISTS(SELECT 1 FROM payment_records p WHERE p.payment_id=t.payment_id) LIMIT 1''',
                                  (prepared.ref,prepared.purpose)).fetchone()
        if (prepared.ref,prepared.purpose,prepared.period) in self.paid or historical:
            fail(OUT_OF_ORDER,'Historical payment has no saved original receipt; reconciliation is required')
        if not self.client_matches(prepared.payer,json.loads(prepared.client)) or self.wallets.by_thumbprint(prepared.payer_key)!=prepared.payer or self.payee_of(prepared.provider)!=prepared.payee or thumbprint(self.payee_keys(prepared.provider))!=prepared.payee_key:
            fail(BAD_SIGNATURE,'Payment identity binding changed before commit')
        if prepared.purpose=='termination' and ((prepared.ref,'deposit',None) in self.paid)!=prepared.deposit_paid:
            fail(OUT_OF_ORDER,'Prior payments changed; obtain the current checkout before proceeding')
        # Candidate signatures are private validation results. Published evidence uses
        # the real commit time, with the original protocol helpers and unchanged claims.
        committed_at = self.now()
        if prepared.expires<=committed_at.timestamp():
            fail(BAD_SIGNATURE,'Payment expired before commit')
        final = json.loads(prepared.reply)
        final['voucher'] = signed(final['voucher'],self.signer,committed_at)
        _,receipt = jwt_decode(final['paymentReceipt'])
        receipt['iat'] = int(committed_at.timestamp())
        final['paymentReceipt'] = jwt_sign(receipt,self.ap2_key)
        encoded_reply = json.dumps(final,ensure_ascii=False,separators=(',',':'))
        self.wallets.transfer_in(conn,prepared.payer,prepared.payee,prepared.amount,purpose=prepared.purpose,
                                 ref=prepared.ref,payment_id=prepared.payment_id)
        conn.execute('INSERT INTO paid(ref,purpose,period,at) VALUES(?,?,?,?)',
                     (prepared.ref,prepared.purpose,prepared.period,committed_at.timestamp()))
        conn.execute('INSERT INTO payment_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (prepared.payment_id,prepared.fingerprint,prepared.payer,prepared.payee,prepared.provider,
                      *key,str(prepared.amount),prepared.currency,prepared.payer_key,encoded_reply,committed_at.timestamp()))
        return Reply(final),True

    def project(self,prepared):
        if self.on_transfer:
            try:
                self.on_transfer({'payer':prepared.payer,'payee':prepared.payee,'amount':str(prepared.amount),
                                  'purpose':prepared.purpose,'ref':prepared.ref,'period':prepared.period,'paymentId':prepared.payment_id})
            except Exception:
                log.warning('Payment committed; observation requires recovery: %s',prepared.payment_id)

    def op_ap2_pay(self,body,files,task_id):
        prepared = self.prepare(body,files)
        if self.requires_admission(prepared.provider):
            fail(OUT_OF_ORDER,'Enrolled services require the current IoA execution connection and admission context')
        with self.db.tx() as conn:
            reply,created = self.commit(conn,prepared)
        if created:
            self.project(prepared)
        return reply

    def lookup(self,owner,payment_id=None,*,ref=None,purpose=None,period=None):
        row = (self.db.one('SELECT * FROM payment_records WHERE payment_id=?',payment_id) if payment_id else
               self.db.one('SELECT * FROM payment_records WHERE ref=? AND purpose=? AND period=?',ref,purpose,period or 0))
        if not row or owner not in (row['payer_id'],row['payee_id']):
            raise LabError('forbidden','Payment is unavailable',status=404)
        return {'paymentId':row['payment_id'],'ref':row['ref'],'purpose':row['purpose'],'period':row['period'] or None,
                'amount':row['amount'],'currency':row['currency'],**json.loads(row['reply'])}

    def require_payments_in(self,conn,*,ref,currency,payer,payee,amounts):
        """Closing consumes actual payment records, never the observation projection."""
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Payment evidence needs the owned application transaction')
        for purpose,period,amount in amounts:
            if Decimal(str(amount))<=0:continue
            row=conn.execute('SELECT * FROM payment_records WHERE ref=? AND purpose=? AND period=?',(ref,purpose,period or 0)).fetchone()
            if not row or (row['payer_id'],row['payee_id'],row['currency'])!=(payer,payee,currency) or Decimal(row['amount'])!=Decimal(str(amount)):
                raise LabError('not_ready','Actual required contract payment is missing; do not release its admission',status=409,recovery='reconcile_original')
