"""Immutable delegation contexts, renewable user grants and one contract budget."""
from dataclasses import dataclass
from decimal import Decimal
import json
import re
import time
import uuid
from datetime import datetime,timezone

from a2c.card import card_digest,validate_card
from a2c.codec import digest,parse_time,parse_duration,fmt_time
from a2c.errors import A2CError
from a2c.jws import verify
from a2c.slots import verify_card,verify_template

from ..errors import LabError


SCHEMA='''
CREATE TABLE IF NOT EXISTS delegation_scopes(
 digest TEXT PRIMARY KEY,scope_id TEXT UNIQUE NOT NULL,owner TEXT NOT NULL,actor TEXT NOT NULL,
 context TEXT NOT NULL,revision INTEGER NOT NULL,issued REAL NOT NULL,expires REAL NOT NULL,closed INTEGER NOT NULL DEFAULT 0,
 fees TEXT NOT NULL DEFAULT '0.00',contract TEXT,total TEXT NOT NULL DEFAULT '0.00',paid TEXT NOT NULL DEFAULT '0.00');
CREATE TABLE IF NOT EXISTS delegation_grants(
 scope TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(scope,revision));
CREATE TABLE IF NOT EXISTS delegation_scope_operations(
 owner TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS delegation_scope_payments(
 payment_id TEXT PRIMARY KEY,scope TEXT NOT NULL,fingerprint TEXT NOT NULL,ref TEXT NOT NULL,purpose TEXT NOT NULL,
 amount TEXT NOT NULL,period INTEGER NOT NULL,UNIQUE(scope,ref,purpose,period));
'''


def invalid(message):raise LabError('invalid_input',message)


def encode(value):
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError,RecursionError):invalid('Authorization must contain JSON values')


def amount(value):
    if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9][0-9]{0,12})\.[0-9]{2}',value):
        invalid('Use a nonnegative amount string with exactly two decimal places')
    return Decimal(value)


@dataclass(frozen=True)
class PreparedScope:
    kind: str
    actor: str
    fingerprint: str
    payload: str
    context_digest: str
    issued: float=0
    expires: float=0
    card_digest: str|None=None
    total: str|None=None
    provider: str|None=None


class ScopeLedger:
    def __init__(self,db,identities,platform,*,keys,template,category,clock=time.time):
        self.db,self.identities,self.platform=db,identities,platform.rstrip('/')
        self.keys,self.template,self.category,self.clock=keys,template,category,clock
        with db.lock:db.conn.executescript(SCHEMA)

    @staticmethod
    def _scope_digest(value):
        if not isinstance(value,str) or not re.fullmatch('sha256:[0-9a-f]{64}',value):invalid('Use an original scopeDigest')
        return value

    @staticmethod
    def _context(value):
        fields={'schema','scopeId','platform','actor','user','purpose','categoryIds','currency','budget','proposalFeeLimit'}
        if not isinstance(value,dict) or set(value)!=fields or value['schema']!='ioalab.scope/1':invalid('Use the complete delegation context')
        try:
            if str(uuid.UUID(value['scopeId']))!=value['scopeId']:raise ValueError()
        except (ValueError,AttributeError,TypeError):invalid('scopeId must be a canonical UUID')
        if any(not isinstance(value[key],str) or not 1<=len(value[key])<=2048 for key in ('platform','actor','purpose')):invalid('Provide bounded context fields')
        user=value['user']
        if not isinstance(user,dict) or set(user)!={'id','jwk'} or not isinstance(user['id'],str) or not 1<=len(user['id'])<=256:invalid('Provide the original user signing identity')
        if not isinstance(user['jwk'],dict) or set(user['jwk'])-{'kid','kty','crv','x'} or user['jwk'].get('kty')!='OKP' or user['jwk'].get('crv')!='Ed25519':invalid('Only the bound public Ed25519 user key is accepted')
        categories=value['categoryIds']
        if categories is not None and (not isinstance(categories,list) or len(categories)>32 or any(not isinstance(item,str) or not 1<=len(item)<=128 for item in categories) or len(set(categories))!=len(categories)):
            invalid('categoryIds must be distinct exact category IDs, null or an empty list')
        if value['currency']!='CNY':invalid('The current virtual ledger uses CNY units')
        if amount(value['proposalFeeLimit'])>amount(value['budget']):invalid('Proposal fees are part of the total budget')

    def prepare(self,owner,actor,kind,payload):
        if not isinstance(actor,str) or not isinstance(payload,dict) or len(encode(payload).encode())>131072:invalid('Invalid scope control payload')
        try:
            if kind=='scope.register':
                if set(payload)!={'context','grant','proof'}:invalid('Use context, grant and proof')
                context,grant=payload['context'],payload['grant']
                self._context(context)
                if context['actor']!=actor or context['platform']!=self.platform:raise LabError('forbidden','Authorization belongs to another actor or platform',status=403)
                if not isinstance(grant,dict) or set(grant)!={'revision','issuedAt','expiresAt'} or type(grant['revision']) is not int or grant['revision']<1:invalid('Use an explicit grant revision and validity interval')
                issued,expires=(parse_time(grant[key]).timestamp() for key in ('issuedAt','expiresAt'))
                if not 0<expires-issued<=7*86400:invalid('A user grant may last at most seven days')
                header=verify(payload['proof'],digest({'context':context,'grant':grant}),context['user']['jwk'],slot='ioa-scope',signer=context['user']['id'])
                if parse_time(header['a2c-ts']).timestamp()!=issued:invalid('Proof signing time must match grant issuedAt')
                return PreparedScope(kind,actor,digest(payload),encode(payload),digest(context),issued,expires)
            if kind in ('scope.status','scope.close'):
                if set(payload)!={'scopeDigest'}:invalid('Use only the original scopeDigest')
                return PreparedScope(kind,actor,digest(payload),encode(payload),self._scope_digest(payload['scopeDigest']))
            if kind!='scope.select' or set(payload)!={'scopeDigest','card','proof'}:invalid('Unsupported scope operation or fields')
            scope=self._get(owner,actor,self._scope_digest(payload['scopeDigest']))
            context=json.loads(scope['context'])
            card=payload['card'];template=self.template(card['templateDigest'])
            verify_template(template,self.keys);validate_card(template,card);chain=verify_card(template,card,self.keys)
            if template['formation'] not in ('proposal','countersign'):invalid('Ordinary services use proposal or countersign formation')
            if template['pricing']['currency']!=context['currency']:raise LabError('forbidden','Contract currency is outside the user authorization',status=403)
            if card['delegationScopeDigest']!=scope['digest'] or not self.identities.matches_party(owner,card['client']) or card['client']['id']!=actor:
                raise LabError('forbidden','The card does not belong to the original scope and actor',status=403)
            if card.get('user') and card['user']!=context['user']:raise LabError('forbidden','Card user differs from the scope owner',status=403)
            if not scope['contract'] and chain.ts.get(1) is not None:invalid('Reserve the complete contract value before creating slot 1')
            if template['formation']=='proposal':
                if not chain.ts.get(2) or not chain.ts.get(3):invalid('Only a certified signed proposal is selectable')
                if not scope['contract'] and self.clock()>chain.ts[3].timestamp()+parse_duration(template['deadlines']['respondWithin']).total_seconds():
                    raise LabError('expired','Proposal response window has ended',status=409)
            cd=card_digest(card)
            header=verify(payload['proof'],digest({'scopeDigest':scope['digest'],'cardDigest':cd}),context['user']['jwk'],slot='ioa-contract-selection',signer=context['user']['id'])
            issued=parse_time(header['a2c-ts']).timestamp()
            return PreparedScope(kind,actor,digest(payload),encode(payload),scope['digest'],issued,card_digest=cd,total=f"{Decimal(str(card['derived']['total'])):.2f}",provider=card['provider'])
        except (A2CError,KeyError,TypeError,ValueError,AttributeError):
            raise LabError('invalid_input','Scope or selection proof failed protocol validation') from None

    def _get(self,owner,actor,scope,conn=None):
        row=(conn.execute('SELECT * FROM delegation_scopes WHERE digest=?',(scope,)).fetchone() if conn is not None else self.db.one('SELECT * FROM delegation_scopes WHERE digest=?',scope))
        if not row or (row['owner'],row['actor'])!=(owner,actor):raise LabError('forbidden','Scope is unavailable',status=404)
        return row

    def _binding(self,owner,actor,context):
        if self.identities.user_key(owner,actor)!=context['user']['jwk']:
            raise LabError('forbidden','Authorization user key differs from the bound identity',status=403)

    def _live(self,row):
        if row['closed']:raise LabError('closed','The original delegation scope is closed',status=409)
        if row['expires']<=self.clock():raise LabError('expired','Explicit user renewal is required',status=409,recovery='wait_for_user')
        self._binding(row['owner'],row['actor'],json.loads(row['context']))

    def _public(self,row):
        context=json.loads(row['context'])
        fees,paid,total=map(Decimal,(row['fees'],row['paid'],row['total']))
        return {'scopeDigest':row['digest'],'scopeId':row['scope_id'],'actor':row['actor'],
                'revision':row['revision'],'expiresAt':fmt_time(datetime.fromtimestamp(row['expires'],timezone.utc)),'state':'closed' if row['closed'] else 'expired' if row['expires']<=self.clock() else 'active',
                'budget':context['budget'],'proposalFeeLimit':context['proposalFeeLimit'],'proposalFeesPaid':str(fees),
                'selectedCardDigest':row['contract'],'contractTotal':str(total),'contractPaid':str(paid),
                'reservedRemaining':str(total-paid),'spent':str(fees+paid)}

    def operation(self,owner,actor,kind,operation,conn=None):
        row=(conn.execute('SELECT * FROM delegation_scope_operations WHERE actor=? AND kind=? AND id=?',(actor,kind,operation)).fetchone() if conn is not None else self.db.one('SELECT * FROM delegation_scope_operations WHERE actor=? AND kind=? AND id=?',actor,kind,operation))
        if not row or row['owner']!=owner:raise LabError('not_ready','Original scope operation is unavailable',status=404,recovery='retry_original')
        return json.loads(row['result'])

    def apply_in(self,conn,owner,operation,prepared):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Scope changes require the owned application transaction')
        if not isinstance(operation,str) or not 1<=len(operation)<=128:invalid('Use an original operationId')
        actor,kind=prepared.actor,prepared.kind
        old=conn.execute('SELECT * FROM delegation_scope_operations WHERE actor=? AND kind=? AND id=?',(actor,kind,operation)).fetchone()
        if old and kind!='scope.status':
            if old['owner']!=owner or old['fingerprint']!=prepared.fingerprint:raise LabError('id_conflict','Original scope operation changed',status=409)
            return json.loads(old['result'])
        payload=json.loads(prepared.payload)
        if kind=='scope.register':result=self._register(conn,owner,prepared,payload)
        else:
            row=self._get(owner,actor,prepared.context_digest,conn)
            if kind=='scope.select':result=self._select(conn,row,prepared)
            elif kind=='scope.close':
                conn.execute('UPDATE delegation_scopes SET closed=1,revision=revision+1 WHERE digest=?',(row['digest'],))
                result=self._public(self._get(owner,actor,row['digest'],conn))
            else:return self._public(row)
        conn.execute('INSERT INTO delegation_scope_operations VALUES(?,?,?,?,?,?)',(owner,actor,kind,operation,prepared.fingerprint,encode(result)))
        return result

    def _register(self,conn,owner,prepared,payload):
        context,grant=payload['context'],payload['grant'];scope=prepared.context_digest
        previous=conn.execute('SELECT * FROM delegation_grants WHERE scope=? AND revision=?',(scope,grant['revision'])).fetchone()
        row=conn.execute('SELECT * FROM delegation_scopes WHERE scope_id=?',(context['scopeId'],)).fetchone()
        if row and (row['owner'],row['actor'],row['digest'])!=(owner,prepared.actor,scope):raise LabError('id_conflict','The original scope context cannot change',status=409)
        if previous:
            if previous['fingerprint']!=prepared.fingerprint:raise LabError('id_conflict','The original grant revision cannot change',status=409)
            return json.loads(previous['result'])
        self._binding(owner,prepared.actor,context)
        now=self.clock()
        if prepared.issued>now+60 or prepared.expires<=now:raise LabError('expired','User grant is expired or its signing time is in the future',status=409,recovery='wait_for_user')
        if row:
            if grant['revision']!=row['revision']+1 or prepared.issued<row['issued']:raise LabError('revision_conflict','Renew the latest original grant revision',status=409)
            conn.execute('UPDATE delegation_scopes SET revision=?,issued=?,expires=?,closed=0 WHERE digest=?',(grant['revision'],prepared.issued,prepared.expires,scope))
        else:
            if grant['revision']!=1:raise LabError('revision_conflict','The initial grant revision is one',status=409)
            conn.execute('INSERT INTO delegation_scopes(digest,scope_id,owner,actor,context,revision,issued,expires) VALUES(?,?,?,?,?,?,?,?)',
                         (scope,context['scopeId'],owner,prepared.actor,encode(context),1,prepared.issued,prepared.expires))
        result={**self._public(self._get(owner,prepared.actor,scope,conn)),'acceptedRevision':grant['revision']}
        conn.execute('INSERT INTO delegation_grants VALUES(?,?,?,?,?)',(scope,grant['revision'],prepared.payload,prepared.fingerprint,encode(result)))
        return result

    def _select(self,conn,row,prepared):
        self._live(row)
        if not self.clock()-900<=prepared.issued<=self.clock()+60 or not row['issued']<=prepared.issued<row['expires']:
            raise LabError('expired','A current user contract-selection proof is required',status=409)
        if row['contract'] and row['contract']!=prepared.card_digest:raise LabError('id_conflict','This scope already selected another contract',status=409)
        context=json.loads(row['context'])
        category=self.category(conn,prepared.provider)
        if context['categoryIds'] is not None and category not in context['categoryIds']:raise LabError('forbidden','Service category is outside the user authorization',status=403)
        total=amount(prepared.total)
        if Decimal(row['fees'])+total>amount(context['budget']):raise LabError('quota_exceeded','Proposal fees and the complete contract exceed the authorized budget',status=409)
        self._live(row)
        conn.execute('UPDATE delegation_scopes SET contract=?,total=? WHERE digest=?',(prepared.card_digest,prepared.total,row['digest']))
        return self._public(self._get(row['owner'],row['actor'],row['digest'],conn))

    def record_payment_in(self,conn,owner,actor,scope,payment):
        """Application calls inside the SAME transaction as verified AP2/capacity commit."""
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Scope payment requires the owned application transaction')
        row=self._get(owner,actor,scope,conn)
        old=conn.execute('SELECT * FROM delegation_scope_payments WHERE payment_id=?',(payment.payment_id,)).fetchone()
        if old:
            if old['scope']!=scope or old['fingerprint']!=payment.fingerprint:raise LabError('id_conflict','Original scope payment changed',status=409)
            return self._public(row)
        self._live(row);context=json.loads(row['context'])
        if payment.payer!=owner or payment.currency!=context['currency'] or payment.scope_digest!=scope:
            raise LabError('forbidden','Payment differs from the authorized actor, scope or currency',status=403)
        client=json.loads(payment.client)
        if client.get('id')!=actor or not self.identities.matches_party(owner,client):raise LabError('forbidden','Payment client is outside this scope',status=403)
        paid=payment.amount
        if paid<0 or not paid.is_finite() or paid!=paid.quantize(Decimal('0.01')):invalid('Invalid verified payment amount')
        fees,contract_paid=Decimal(row['fees']),Decimal(row['paid'])
        if payment.purpose=='proposal-fee':
            if row['contract']:raise LabError('closed','A selected scope no longer collects paid proposals',status=409)
            category=self.category(conn,payment.provider)
            if context['categoryIds'] is not None and category not in context['categoryIds']:raise LabError('forbidden','Service category is outside the user authorization',status=403)
            fees+=paid
            if fees>amount(context['proposalFeeLimit']) or fees>amount(context['budget']):raise LabError('quota_exceeded','Proposal fees exceed the original authorized limit',status=409)
        else:
            if row['contract']!=payment.ref:raise LabError('forbidden','Only the selected contract can consume its reserved budget',status=403)
            contract_paid+=paid
            if contract_paid>Decimal(row['total']):raise LabError('quota_exceeded','Payment exceeds the reserved complete contract value',status=409)
        self._live(row)
        conn.execute('UPDATE delegation_scopes SET fees=?,paid=? WHERE digest=?',(str(fees),str(contract_paid),scope))
        conn.execute('INSERT INTO delegation_scope_payments VALUES(?,?,?,?,?,?,?)',(payment.payment_id,scope,payment.fingerprint,payment.ref,payment.purpose,str(paid),payment.period or 0))
        return self._public(self._get(owner,actor,scope,conn))
