"""Application use cases: coordinate public component methods in one short transaction."""
import logging
import json
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime, timezone
from a2c.codec import digest

from a2c.errors import A2CError
from .identity import AccountSessions
from .identity import thumbprint
from .errors import LabError
from .payments import money


log=logging.getLogger(__name__)


class LedgerOperations:
    """Explicit administrator adjustments and payer-initiated plain transfers."""
    def __init__(self,wallets,emit):
        self.wallets,self.emit=wallets,emit

    def adjust(self,user_id,delta,*,by,note,kind='grant'):
        balance=self.wallets.adjust(user_id,money(delta),by=by,note=note,kind=kind)
        self.emit('platform','wallet.adjust',sender=by,receiver=user_id,amount=str(delta),outcome=kind,detail={'note':note})
        return str(balance)

    def transfer(self,payer_id,payee_id,amount,*,purpose,ref):
        from a2c.errors import CARD_INVALID,fail
        if purpose.split(':')[0] not in ('ruling','refund','fee'):
            fail(CARD_INVALID,'普通转账的用途只能是 ruling / refund / fee')
        self.wallets.transfer(payer_id,payee_id,money(amount),purpose=purpose,ref=ref,by=payer_id)
        self.emit('platform','transfer.plain',sender=payer_id,receiver=payee_id,amount=str(money(amount)),
                  outcome=purpose,detail={'ref':ref})


class DiscoveryOperations:
    """Read the directory and public availability; audit without reserving work."""
    def __init__(self,discovery,services,emit,market_id):
        self.discovery,self.services,self.emit,self.market_id=discovery,services,emit,market_id

    def search(self,query,top_k,*,by=''):
        hits=self.discovery.search(query,top_k)
        observed=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        states={row['id']:row for row in self.services.list()}
        hits=[{**hit,'availability':({'observedAt':observed,**{key:states[hit['id']][key] for key in
              ('status','activeDelegations','maxActiveDelegations')}} if hit['id'] in states else None)} for hit in hits]
        self.emit('platform','discovery.search',sender=by,receiver=self.market_id,outcome=self.discovery.last_mode,
                  detail={'query':query[:200],'hits':[hit['name'] for hit in hits],'ids':[hit['id'] for hit in hits]})
        return hits



@dataclass(frozen=True)
class PreparedExecution:
    operation:str
    fingerprint:str
    admission:str
    payment:object=None
    original:str|None=None


class PaymentOperations:
    """Application transaction for AP2, capacity and authorization budget."""
    def __init__(self,db,identities,psp,admissions,scopes,*,record=None):
        self.db,self.identities,self.psp,self.admissions,self.scopes=db,identities,psp,admissions,scopes
        self.record=record
        self.arbitrations=None

    def project(self,prepared):
        if prepared.payment:self.psp.project(prepared.payment)

    def operation(self,owner,actor,operation):
        self.identities.owned(owner,actor)
        with self.db.lock:row=self.psp.operation_in(self.db.conn,actor,operation)
        if not row or row['owner']!=owner:raise LabError('not_ready','Original payment operation is unavailable',status=404,recovery='retry_original')
        return row['result']

    def prepare(self,owner,actor,operation,payload):
        self.identities.owned(owner,actor)
        if not isinstance(operation,str) or not 1<=len(operation)<=128 or set(payload)!={'admissionId','ap2'} or not isinstance(payload['admissionId'],str) or not isinstance(payload['ap2'],dict):
            raise LabError('invalid_input','Use the original AP2 request and admissionId')
        fingerprint=digest(payload)
        with self.db.lock:old=self.psp.operation_in(self.db.conn,actor,operation)
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
        old=self.psp.operation_in(conn,actor,prepared.operation)
        if old:
            if old['owner']!=owner or old['fingerprint']!=prepared.fingerprint:raise LabError('id_conflict','Original payment operation changed',status=409)
            return old['result'],False
        payment=prepared.payment
        if payment is None:raise LabError('unknown_result','The original operation receipt needs reconciliation',status=409)
        if self.arbitrations:self.arbitrations.payment_in(conn,prepared.admission,payment,json.loads(prepared.original) if prepared.original else None)
        created=self.admissions.payment_in(conn,owner,actor,prepared.admission,payment,original=json.loads(prepared.original) if prepared.original else None)
        try:reply,transferred=self.psp.commit(conn,payment)
        except A2CError as exc:
            raise LabError('payment_rejected',str(exc),status=403) from exc
        if created and not payment.refund:self.scopes.record_payment_in(conn,owner,actor,payment.scope_digest,payment)
        if self.record and prepared.original:
            card=json.loads(prepared.original).get('card')
            if card:self.record(conn,prepared.admission,card)
        if created!=transferred:raise LabError('unknown_result','Capacity and original payment facts disagree; reconcile before retrying',status=409)
        result=reply.body
        self.psp.record_operation_in(conn,owner,actor,prepared.operation,prepared.fingerprint,result)
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
    def __init__(self,db,identities,admissions,market,*,contracts=None,psp=None,close=None,record=None):
        self.db,self.identities,self.admissions,self.market=db,identities,admissions,market
        self.contracts,self.psp,self.close,self.record=contracts,psp,close,record
        self.arbitrations=None

    def operation(self,owner,actor,operation):
        self.identities.owned(owner,actor)
        with self.db.lock:row=self.market.operation_in(self.db.conn,actor,operation)
        if not row or row['owner']!=owner:raise LabError('not_ready','Original market operation is unavailable',status=404,recovery='retry_original')
        return row['result']

    def prepare(self,owner,actor,operation,payload):
        self.identities.owned(owner,actor)
        if payload.get('op') in ('delivery.anchor','card.close'):return self.prepare_contract(owner,actor,operation,payload)
        if not isinstance(operation,str) or not 1<=len(operation)<=128 or set(payload)!={'admissionId','op','body','request'} or payload['op']!='card.endorse' or not isinstance(payload['body'],dict) or set(payload['body'])!={'card'}:
            raise LabError('invalid_input','Use original card.endorse with its admission and signed request')
        fingerprint=digest(payload)
        with self.db.lock:old=self.market.operation_in(self.db.conn,actor,operation)
        if old:
            if old['owner']!=owner or old['fingerprint']!=fingerprint:raise LabError('id_conflict','Original market operation changed',status=409)
            return PreparedEndorsement(operation,fingerprint,json.dumps(payload),json.dumps(old['result']))
        with self.db.tx() as conn:
            if self.arbitrations:self.arbitrations.guard_in(conn,payload['admissionId'],payload['op'],payload['body'])
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
        old=self.market.operation_in(conn,actor,prepared.operation)
        if old:
            if old['owner']!=owner or old['fingerprint']!=prepared.fingerprint:raise LabError('id_conflict','Original market operation changed',status=409)
            return old['result'],False
        payload=json.loads(prepared.payload)
        if self.arbitrations:self.arbitrations.guard_in(conn,payload['admissionId'],payload['op'],payload['body'])
        if payload['op']!='card.endorse':return self.apply_contract_in(conn,owner,actor,prepared,payload)
        result=self.admissions.issued_offer_in(conn,owner,actor,payload['admissionId'],prepared.fingerprint)
        if result is None:
            self.admissions.proposal_eligible_in(conn,owner,actor,payload['admissionId'],payload['request'],payload['body']['card'])
            if prepared.snapshot is None:raise LabError('unknown_result','Restore the original market snapshot',status=409)
            result=json.loads(prepared.result)
            from a2c.card import card_digest
            self.market.commit_snapshot_in(conn,card_digest(result['card']),json.loads(prepared.snapshot))
            self.admissions.record_offer_in(conn,payload['admissionId'],prepared.fingerprint,result)
        if self.record:self.record(conn,payload['admissionId'],result['card'])
        self.market.record_operation_in(conn,owner,actor,prepared.operation,prepared.fingerprint,result)
        return result,True

    def prepare_contract(self,owner,actor,operation,payload):
        if not self.contracts or not isinstance(operation,str) or not 1<=len(operation)<=128 or set(payload)!={'admissionId','op','body'} or not isinstance(payload['body'],dict) or set(payload['body'])!={'card'}:
            raise LabError('invalid_input','Use the original contract market method and saved operation')
        fingerprint=digest(payload)
        with self.db.lock:old=self.market.operation_in(self.db.conn,actor,operation)
        if old:
            if old['owner']!=owner or old['fingerprint']!=fingerprint:raise LabError('id_conflict','Original market operation changed',status=409)
            return PreparedEndorsement(operation,fingerprint,json.dumps(payload),json.dumps(old['result']))
        with self.db.tx() as conn:
            if self.arbitrations:self.arbitrations.guard_in(conn,payload['admissionId'],payload['op'],payload['body'])
            _,template=self.contracts.market_in(conn,owner,actor,payload)
        try:result,changes=self.market.prepare_contract(template,payload['op'],payload['body']['card'])
        except (A2CError,KeyError,TypeError,ValueError) as exc:raise LabError('invalid_input','Original market rejected contract evidence: '+str(exc)) from None
        return PreparedEndorsement(operation,fingerprint,json.dumps(payload),json.dumps(result),changes=json.dumps(changes))

    def apply_contract_in(self,conn,owner,actor,prepared,payload):
        row,template=self.contracts.market_in(conn,owner,actor,payload)
        if prepared.changes is None:raise LabError('unknown_result','Restore the original contract market effect',status=409)
        if payload['op']=='card.close':
            self.psp.require_contract_payments_in(conn,template,payload['body']['card'],keys=self.contracts.keys(row))
        self.market.commit_contract_in(conn,json.loads(prepared.changes))
        result_card=json.loads(prepared.result).get('card') or payload['body']['card']
        self.contracts.advance_in(conn,row,result_card)
        if self.record:
            result_card=json.loads(prepared.result).get('card') or payload['body']['card']
            self.record(conn,row['id'],result_card)
        if payload['op']=='card.close':
            self.admissions.finish_in(conn,row['id'])
            self.close(conn,row['interaction'])
        result=json.loads(prepared.result)
        self.market.record_operation_in(conn,owner,actor,prepared.operation,prepared.fingerprint,result)
        return result,True


@dataclass(frozen=True)
class Control:
    """One owner's control kind: optional preparation outside the transaction, commit inside it, original-result query.

    apply_in(conn,owner,actor,kind,operationId,payload,prepared) returns
    (result, activity facts or None, after-commit callable or None).
    """
    apply_in:object=None
    prepare:object=None
    operation:object=None


class ControlOperations:
    """Participant control frames: delivery frames, leases and records activity; each kind's owner prepares and commits it."""
    def __init__(self,db,identities,delivery):
        self.db,self.identities,self.delivery=db,identities,delivery
        self.kinds={}

    def register(self,kind,control):
        """`kind` is exact, or a family prefix ending in '.'; delivery keeps its own kinds."""
        if kind in self.kinds or kind in self.delivery.CONTROLS or kind=='operation.get':raise ValueError('Control kind already has an owner: '+kind)
        self.kinds[kind]=control

    def _control(self,kind):
        return self.kinds.get(kind) or (self.kinds.get(kind.split('.',1)[0]+'.') if '.' in kind else None)

    def operation(self,owner,actor,kind,operation):
        self.identities.owned(owner,actor)
        if not isinstance(kind,str) or not isinstance(operation,str):
            raise LabError('invalid_input','Operation type and identity must be strings')
        control=self._control(kind)
        if control and control.operation:return control.operation(owner,actor,kind,operation)
        return self.delivery.operation(owner,actor,kind,operation)

    def control(self,owner,connection,generations,frame):
        actor,kind,payload=self.delivery.control_frame(generations,frame)
        operation,generation=frame.get('operationId'),generations[actor]
        control=None if kind in self.delivery.CONTROLS else self._control(kind)
        prepared=None
        if control and control.prepare and control.apply_in:
            # Signature/card/key preparation can fetch public material; never do it
            # inside the delivery lease/operation transaction. The owner and lease
            # are rechecked below before committing the prepared immutable action.
            with self.db.lock:self.delivery.lease(self.db.conn,owner,actor,connection,generation)
            prepared=control.prepare(owner,actor,kind,operation,payload)
        self.delivery.cleanup()
        after=None
        with self.db.tx() as conn:
            self.delivery.lease(conn,owner,actor,connection,generation)
            if kind=='operation.get':
                result,facts=self.operation(owner,actor,payload.get('operation'),payload.get('operationId')),None
            elif kind in self.delivery.CONTROLS:
                result,facts=self.delivery.control_in(conn,actor,kind,operation,payload,generation)
            elif control and control.apply_in:
                result,facts,after=control.apply_in(conn,owner,actor,kind,operation,payload,prepared)
            else:raise LabError('protocol_error','Unsupported control operation')
            recorded=[] if facts is None else self.delivery.activity_in(conn,frame,result,**facts)
            self.delivery.renew_in(conn,actor)
        self.delivery.notify()
        if after:after()
        self.delivery.publish_activity(recorded)
        return result

    def register_platform(self,*,scopes,admissions,arbitrations,payments,market,publications):
        """The control kinds owned outside delivery, each committed by its owning module.

        Adapters resolve the owner's methods per call so a replaced method (fault seam) takes effect."""
        def scope_in(conn,owner,actor,kind,operation,payload,prepared):
            return scopes.apply_in(conn,owner,operation,prepared),None,None
        def admission_in(conn,owner,actor,kind,operation,payload,prepared):
            return admissions.apply_in(conn,owner,actor,kind,operation,payload),None,None
        def arbitration_in(conn,owner,actor,kind,operation,payload,prepared):
            return arbitrations.apply_in(conn,owner,actor,kind,prepared),None,None
        def pay_in(conn,owner,actor,kind,operation,payload,prepared):
            result,transferred=payments.apply_in(conn,owner,actor,prepared)
            return result,None,(lambda:payments.project(prepared)) if transferred else None
        def market_in(conn,owner,actor,kind,operation,payload,prepared):
            result,fresh=market.apply_in(conn,owner,actor,prepared)
            return result,({'fresh':True} if fresh else None),None
        self.register('scope.',Control(scope_in,lambda owner,actor,kind,operation,payload:scopes.prepare(owner,actor,kind,payload),
                                      lambda owner,actor,kind,operation:scopes.operation(owner,actor,kind,operation)))
        self.register('admission.',Control(admission_in,None,lambda owner,actor,kind,operation:admissions.operation(owner,actor,kind,operation)))
        self.register('arbitration.',Control(arbitration_in,lambda owner,actor,kind,operation,payload:arbitrations.prepare(owner,actor,kind,operation,payload),
                                            lambda owner,actor,kind,operation:arbitrations.operation(owner,actor,kind,operation)))
        self.register('ap2.pay',Control(pay_in,lambda owner,actor,kind,operation,payload:payments.prepare(owner,actor,operation,payload),
                                        lambda owner,actor,kind,operation:payments.operation(owner,actor,operation)))
        self.register('a2c.market',Control(market_in,lambda owner,actor,kind,operation,payload:market.prepare(owner,actor,operation,payload),
                                           lambda owner,actor,kind,operation:market.operation(owner,actor,operation)))
        self.register('service.publish',Control(operation=lambda owner,actor,kind,operation:publications.operation(owner,actor,operation)))


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

    def create_account(self,username,password='',*,kind='external',display_name='',note='',invite='',ip=''):
        """Provision a seed/admin account; ordinary browser registration uses register."""
        policy=self.settings.policy()
        prepared=self.auth.prepare_registration(username,password,kind=kind,display_name=display_name,note=note,invite=invite,ip=ip)
        with self.db.tx() as conn:
            user=self.auth.register_in(conn,prepared)
            self.wallets.initial_credit_in(conn,user['id'],policy['credits']['initial'])
        self.emit('platform','user.register',sender=user['id'],outcome=kind,
                  detail={'username':username,'displayName':user['display_name']})
        return user

    def bind_identity(self,registry,owner,body):
        with self.db.tx() as conn:
            result=registry.bind_in(conn,owner,body)
            if result['role']=='assistant':
                self.wallets.bind_payer_in(conn,owner,thumbprint(result['keys']['payer']))
        return result

    def _project(self,user,operation,credits,registered=False):
        try:
            if registered:
                self.emit('platform','user.register',sender=user['id'],outcome=user['kind'],
                          detail={'username':user['username'],'displayName':user['display_name']})
            for kind,note in (('initial','账号初始发放'),('login','登录赠送')):
                amount=credits.get(kind,'0.00')
                if Decimal(amount)>0:
                    self.emit('platform','wallet.adjust',sender='platform',receiver=user['id'],amount=amount,
                              outcome=kind,detail={'note':note,'operationId':operation})
        except Exception:
            log.warning('Account operation committed; projection requires recovery: %s',operation)

    def register(self,username,password,*,display_name='',invite='',ip='',operation_id=None):
        if not isinstance(username,str) or not isinstance(password,str):
            raise LabError('invalid_input','Account fields must be strings')
        username=(username or '').strip()
        operation=self.sessions.operation(operation_id)
        policy=self.settings.policy()
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
            initial=self.wallets.initial_credit_in(conn,user['id'],policy['credits']['initial'])
            user=self.auth.login_in(conn,prepared)
            credits=self.wallets.login_credit_in(conn,user['id'],operation,policy['credits'])
            credits['initial']=initial
            result=self.sessions.save(conn,username,'register',operation,params,user,prepared['pw_hash'],'browser',credits=credits)
        self._project(user,operation,credits,registered=True)
        return result

    def login(self,username,password,*,operation_id=None,audience='browser',ip=''):
        if not isinstance(username,str) or not isinstance(password,str):
            raise LabError('invalid_input','Account fields must be strings')
        if audience not in ('sdk','browser'):raise ValueError('Unknown login audience')
        username=(username or '').strip()
        operation=self.sessions.operation(operation_id)
        policy=self.settings.policy()
        params=self.sessions.fingerprint({'username':username,'password':password,'audience':audience})
        cached=self.sessions.restore(username,'login',operation,params)
        if cached:return cached
        verified=self.auth.authenticate(username,password,ip=ip,policy=policy)
        with self.db.tx() as conn:
            cached=self.sessions.restore(username,'login',operation,params,conn)
            if cached:return cached
            user=self.auth.login_in(conn,verified)
            credits=self.wallets.login_credit_in(conn,user['id'],operation,policy['credits'])
            credits['initial']='0.00'
            result=self.sessions.save(conn,username,'login',operation,params,user,verified['pw_hash'],audience,credits=credits)
        self._project(user,operation,credits)
        return result
