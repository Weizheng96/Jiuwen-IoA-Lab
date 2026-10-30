"""Application use cases: coordinate public component methods in one short transaction."""
import logging

from a2c.errors import A2CError
from .identity.sessions import AccountSessions
from .identity.keys import thumbprint
from .errors import LabError


log=logging.getLogger(__name__)


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
