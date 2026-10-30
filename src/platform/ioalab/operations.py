"""Application use cases: coordinate public component methods in one short transaction."""
import logging
import json
from dataclasses import dataclass
from a2c.codec import digest

from a2c.errors import A2CError
from .identity.sessions import AccountSessions
from .identity.keys import thumbprint
from .errors import LabError


log=logging.getLogger(__name__)

PAYMENT_OPERATIONS_SCHEMA='''CREATE TABLE IF NOT EXISTS delegation_payment_operations(
 owner TEXT NOT NULL,actor TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(actor,id));'''


@dataclass(frozen=True)
class PreparedExecution:
    operation:str
    fingerprint:str
    admission:str
    payment:object=None
    original:str|None=None


class PaymentOperations:
    """Application transaction for AP2, capacity and authorization budget."""
    def __init__(self,db,identities,psp,admissions,scopes):
        self.db,self.identities,self.psp,self.admissions,self.scopes=db,identities,psp,admissions,scopes
        with db.lock:db.conn.executescript(PAYMENT_OPERATIONS_SCHEMA)

    def project(self,prepared):
        if prepared.payment:self.psp.project(prepared.payment)

    def operation(self,owner,actor,operation):
        self.identities.owned(owner,actor)
        row=self.db.one('SELECT * FROM delegation_payment_operations WHERE actor=? AND id=?',actor,operation)
        if not row or row['owner']!=owner:raise LabError('not_ready','Original payment operation is unavailable',status=404,recovery='retry_original')
        return json.loads(row['result'])

    def prepare(self,owner,actor,operation,payload):
        self.identities.owned(owner,actor)
        if not isinstance(operation,str) or not 1<=len(operation)<=128 or set(payload)!={'admissionId','ap2'} or not isinstance(payload['admissionId'],str) or not isinstance(payload['ap2'],dict):
            raise LabError('invalid_input','Use the original AP2 request and admissionId')
        fingerprint=digest(payload)
        old=self.db.one('SELECT * FROM delegation_payment_operations WHERE actor=? AND id=?',actor,operation)
        if old:
            if old['owner']!=owner or old['fingerprint']!=fingerprint:raise LabError('id_conflict','Original payment operation changed',status=409)
            return PreparedExecution(operation,fingerprint,payload['admissionId'])
        try:payment=self.psp.prepare(payload['ap2'])
        except (A2CError,KeyError,TypeError,ValueError):
            raise LabError('invalid_input','AP2 request failed original protocol validation') from None
        if payment.payer!=owner:raise LabError('forbidden','Payment belongs to another account',status=403)
        return PreparedExecution(operation,fingerprint,payload['admissionId'],payment,json.dumps(payload['ap2']))

    def apply_in(self,conn,owner,actor,prepared):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Payment execution requires the owned application transaction')
        self.identities.owned(owner,actor)
        old=conn.execute('SELECT * FROM delegation_payment_operations WHERE actor=? AND id=?',(actor,prepared.operation)).fetchone()
        if old:
            if old['owner']!=owner or old['fingerprint']!=prepared.fingerprint:raise LabError('id_conflict','Original payment operation changed',status=409)
            return json.loads(old['result']),False
        payment=prepared.payment
        if payment is None:raise LabError('unknown_result','The original operation receipt needs reconciliation',status=409)
        created=self.admissions.payment_in(conn,owner,actor,prepared.admission,payment,original=json.loads(prepared.original) if prepared.original else None)
        try:reply,transferred=self.psp.commit(conn,payment)
        except A2CError as exc:
            raise LabError('forbidden','Payment changed or expired before transaction commit',status=403) from exc
        if created:self.scopes.record_payment_in(conn,owner,actor,payment.scope_digest,payment)
        if created!=transferred:raise LabError('unknown_result','Capacity and original payment facts disagree; reconcile before retrying',status=409)
        result=reply.body
        conn.execute('INSERT INTO delegation_payment_operations VALUES(?,?,?,?,?)',(owner,actor,prepared.operation,prepared.fingerprint,json.dumps(result,ensure_ascii=False)))
        return result,transferred


@dataclass(frozen=True)
class PreparedEndorsement:
    operation:str
    fingerprint:str
    payload:str
    result:str|None=None
    snapshot:str|None=None
    changes:str|None=None


class MarketOperations:
    """Publish original slot 3 only with the current service/admission transaction."""
    def __init__(self,db,identities,admissions,market,*,contracts=None,psp=None,close=None):
        self.db,self.identities,self.admissions,self.market=db,identities,admissions,market
        self.contracts,self.psp,self.close=contracts,psp,close
        with db.lock:db.conn.execute('''CREATE TABLE IF NOT EXISTS delegation_market_operations(
             owner TEXT NOT NULL,actor TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
             PRIMARY KEY(actor,id))''')

    def operation(self,owner,actor,operation):
        self.identities.owned(owner,actor)
        row=self.db.one('SELECT * FROM delegation_market_operations WHERE actor=? AND id=?',actor,operation)
        if not row or row['owner']!=owner:raise LabError('not_ready','Original market operation is unavailable',status=404,recovery='retry_original')
        return json.loads(row['result'])

    def prepare(self,owner,actor,operation,payload):
        self.identities.owned(owner,actor)
        if payload.get('op') in ('delivery.anchor','card.close'):return self.prepare_contract(owner,actor,operation,payload)
        if not isinstance(operation,str) or not 1<=len(operation)<=128 or set(payload)!={'admissionId','op','body','request'} or payload['op']!='card.endorse' or not isinstance(payload['body'],dict) or set(payload['body'])!={'card'}:
            raise LabError('invalid_input','Use original card.endorse with its admission and signed request')
        fingerprint=digest(payload)
        old=self.db.one('SELECT * FROM delegation_market_operations WHERE actor=? AND id=?',actor,operation)
        if old:
            if old['owner']!=owner or old['fingerprint']!=fingerprint:raise LabError('id_conflict','Original market operation changed',status=409)
            return PreparedEndorsement(operation,fingerprint,json.dumps(payload),old['result'])
        with self.db.tx() as conn:
            result=self.admissions.issued_offer_in(conn,owner,actor,payload['admissionId'],fingerprint)
            template=None if result else self.admissions.proposal_eligible_in(conn,owner,actor,payload['admissionId'],payload['request'],payload['body']['card'])
        if result:return PreparedEndorsement(operation,fingerprint,json.dumps(payload),json.dumps(result))
        try:
            result,snapshot=self.market.prepare_proposal(template,payload['body']['card'],self.identities.service_party(actor))
        except A2CError as exc:raise LabError('invalid_input','Original market rejected the proposal: '+exc.message) from None
        except (TypeError,KeyError,ValueError):raise LabError('invalid_input','Original market rejected malformed proposal data') from None
        return PreparedEndorsement(operation,fingerprint,json.dumps(payload),json.dumps(result),json.dumps(snapshot))

    def apply_in(self,conn,owner,actor,prepared):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Market endorsement needs the owned application transaction')
        self.identities.owned(owner,actor)
        old=conn.execute('SELECT * FROM delegation_market_operations WHERE actor=? AND id=?',(actor,prepared.operation)).fetchone()
        if old:
            if old['owner']!=owner or old['fingerprint']!=prepared.fingerprint:raise LabError('id_conflict','Original market operation changed',status=409)
            return json.loads(old['result'])
        payload=json.loads(prepared.payload)
        if payload['op']!='card.endorse':return self.apply_contract_in(conn,owner,actor,prepared,payload)
        result=self.admissions.issued_offer_in(conn,owner,actor,payload['admissionId'],prepared.fingerprint)
        if result is None:
            self.admissions.proposal_eligible_in(conn,owner,actor,payload['admissionId'],payload['request'],payload['body']['card'])
            if prepared.snapshot is None:raise LabError('unknown_result','Restore the original market snapshot',status=409)
            result=json.loads(prepared.result)
            from a2c.card import card_digest
            self.market.commit_snapshot_in(conn,card_digest(result['card']),json.loads(prepared.snapshot))
            self.admissions.record_offer_in(conn,payload['admissionId'],prepared.fingerprint,result)
        conn.execute('INSERT INTO delegation_market_operations VALUES(?,?,?,?,?)',(owner,actor,prepared.operation,prepared.fingerprint,json.dumps(result)))
        return result

    def prepare_contract(self,owner,actor,operation,payload):
        if not self.contracts or not isinstance(operation,str) or not 1<=len(operation)<=128 or set(payload)!={'admissionId','op','body'} or not isinstance(payload['body'],dict) or set(payload['body'])!={'card'}:
            raise LabError('invalid_input','Use the original contract market method and saved operation')
        fingerprint=digest(payload)
        old=self.db.one('SELECT * FROM delegation_market_operations WHERE actor=? AND id=?',actor,operation)
        if old:
            if old['owner']!=owner or old['fingerprint']!=fingerprint:raise LabError('id_conflict','Original market operation changed',status=409)
            return PreparedEndorsement(operation,fingerprint,json.dumps(payload),old['result'])
        with self.db.tx() as conn:_,template=self.contracts.market_in(conn,owner,actor,payload)
        try:result,changes=self.market.prepare_contract(template,payload['op'],payload['body']['card'])
        except (A2CError,KeyError,TypeError,ValueError) as exc:raise LabError('invalid_input','Original market rejected contract evidence: '+str(exc)) from None
        return PreparedEndorsement(operation,fingerprint,json.dumps(payload),json.dumps(result),changes=json.dumps(changes))

    def apply_contract_in(self,conn,owner,actor,prepared,payload):
        row,template=self.contracts.market_in(conn,owner,actor,payload)
        if prepared.changes is None:raise LabError('unknown_result','Restore the original contract market effect',status=409)
        if payload['op']=='card.close':
            from decimal import Decimal
            from a2c.card import card_digest
            card=payload['body']['card'];accepted=len(card['signatures']['7'])
            amounts=[('deposit',None,card['derived']['deposit']),*[('installment',i+1,period['amount']) for i,period in enumerate(card['derived']['periods'][:accepted])]]
            term=card['fulfillment'].get('termination',{})
            if term.get('quote'):amounts.append(('termination',None,max(Decimal(0),Decimal(str(term['quote']['fee']))-Decimal(str(card['derived']['deposit'])))))
            self.psp.require_payments_in(conn,ref=card_digest(card),currency=template['pricing']['currency'],payer=self.identities.agent(row['client'])['owner_id'],payee=owner,amounts=amounts)
        self.market.commit_contract_in(conn,json.loads(prepared.changes))
        if payload['op']=='card.close':
            self.admissions.finish_in(conn,row['id'])
            self.close(conn,row['interaction'])
        result=json.loads(prepared.result)
        conn.execute('INSERT INTO delegation_market_operations VALUES(?,?,?,?,?)',(owner,actor,prepared.operation,prepared.fingerprint,json.dumps(result)))
        return result


class ServiceOperations:
    """Coordinate endorsement, authoritative publication, then a rebuildable index."""
    def __init__(self,db,publications,market,discovery,payment_provider):
        import threading
        self.db,self.publications,self.market,self.discovery=db,publications,market,discovery
        self.payment_provider=payment_provider
        self.lock=threading.RLock()
        self.index_error=None

    def publish(self,owner,actor,body):
        with self.lock:
            result=self.publications.restore(owner,actor,body)
            if result is None:
                prepared=self.publications.prepare(owner,actor,body)
                import json
                original=json.loads(prepared.body)
                declaration=original['declaration']
                templates=self.market.endorse_service_templates(declaration['protocolId'],json.loads(prepared.key),
                                                                 original['templates'],self.payment_provider)
                with self.db.tx() as conn:
                    result=self.publications.commit_in(conn,owner,prepared,templates)
            # An old operation repairs the latest source, never its historical revision.
            try:self.reindex(actor)
            except Exception as exc:
                self.index_error=type(exc).__name__
                log.warning('Service publication committed; A2X projection needs recovery: %s',actor)
            return result

    def reindex(self,actor):
        with self.lock:
            declaration,card=self.publications.index_document(actor)
            self.publications.category(declaration['categoryId'])
            actual=self.discovery.register_one(actor,card,category=declaration['categoryId'])
            with self.db.tx() as conn:
                self.publications.indexed_in(conn,actor,declaration,actual)
            self.index_error=None


class AccountOperations:
    def __init__(self,db,auth,wallets,settings,emit):
        self.db,self.auth,self.wallets,self.settings,self.emit=db,auth,wallets,settings,emit
        self.sessions=AccountSessions(db,auth)

    def bind_identity(self,registry,owner,body):
        with self.db.tx() as conn:
            result=registry.bind_in(conn,owner,body)
            if result['role']=='assistant':
                self.wallets.bind_payer_in(conn,owner,thumbprint(result['keys']['payer']))
        return result

    def _project(self,user,operation,registered=False):
        try:
            if registered:
                self.emit('platform','user.register',sender=user['id'],outcome=user['kind'],
                          detail={'username':user['username'],'displayName':user['display_name']})
            self.emit('platform','wallet.adjust',sender='platform',receiver=user['id'],amount=str(self.settings.login_credit),
                      outcome='login',detail={'note':'登录赠送','operationId':operation})
        except Exception:
            log.warning('Account operation committed; projection requires recovery: %s',operation)

    def register(self,username,password,*,display_name='',invite='',ip='',operation_id=None):
        if not isinstance(username,str) or not isinstance(password,str):
            raise LabError('invalid_input','Account fields must be strings')
        username=(username or '').strip()
        operation=self.sessions.operation(operation_id)
        params=self.sessions.fingerprint({'username':username,'password':password,'display_name':display_name,'invite':invite,'audience':'browser'})
        cached=self.sessions.restore(username,'register',operation,params)
        if cached:return cached
        try:
            prepared=self.auth.prepare_registration(username,password,display_name=display_name,invite=invite,ip=ip)
        except A2CError:
            cached=self.sessions.restore(username,'register',operation,params)
            if cached:return cached
            raise
        with self.db.tx() as conn:
            cached=self.sessions.restore(username,'register',operation,params,conn)
            if cached:return cached
            user=self.auth.register_in(conn,prepared)
            user=self.auth.login_in(conn,prepared)
            self.wallets.adjust_in(conn,user['id'],self.settings.login_credit,by='platform',note='登录赠送',kind='login',ref=operation)
            result=self.sessions.save(conn,username,'register',operation,params,user,prepared['pw_hash'],'browser')
        self._project(user,operation,registered=True)
        return result

    def login(self,username,password,*,operation_id=None,audience='browser',ip=''):
        if not isinstance(username,str) or not isinstance(password,str):
            raise LabError('invalid_input','Account fields must be strings')
        if audience not in ('sdk','browser'):raise ValueError('Unknown login audience')
        username=(username or '').strip()
        operation=self.sessions.operation(operation_id)
        params=self.sessions.fingerprint({'username':username,'password':password,'audience':audience})
        cached=self.sessions.restore(username,'login',operation,params)
        if cached:return cached
        verified=self.auth.authenticate(username,password,ip=ip)
        with self.db.tx() as conn:
            cached=self.sessions.restore(username,'login',operation,params,conn)
            if cached:return cached
            user=self.auth.login_in(conn,verified)
            self.wallets.adjust_in(conn,user['id'],self.settings.login_credit,by='platform',note='登录赠送',kind='login',ref=operation)
            result=self.sessions.save(conn,username,'login',operation,params,user,verified['pw_hash'],audience)
        self._project(user,operation)
        return result
