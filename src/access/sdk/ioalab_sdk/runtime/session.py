"""Account access shared by both roles. Credentials never enter model-facing results."""
from pathlib import Path
from urllib.parse import urlsplit
import os
import hashlib
import hmac
import json
import secrets
import ssl
import time
import uuid

import httpx
from a2c.codec import digest_bytes
from .account_store import LocalStore
from .protocol import RuntimeFault,instant


class AccountSession:
    def __init__(self,base,username,password,home,*,connect=True,allow_offline=False):
        parsed=urlsplit(base)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('A platform HTTP(S) address without embedded credentials is required')
        self.base,self.username,self._password=base.rstrip('/'),username,password
        self.home=Path(home)
        self.home.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.cache=LocalStore(self.home/'account.sqlite')
        if os.name!='nt':os.chmod(self.cache.path,0o600)
        self.http=httpx.Client(timeout=120,trust_env=False,verify=ssl.create_default_context())
        self.account={}
        self.allow_offline,self.verified=allow_offline,False
        try:
            binding={'platform':self.base,'username':username}
            with self.cache.transaction():
                old=self.cache.get('meta','binding')
                if old and old!=binding:
                    raise RuntimeFault('identity_conflict','Account directory belongs to another platform or user')
                self.cache.put('meta','binding',binding)
            try:
                self.config=self.http.get(self.base+'/api/config',timeout=10).raise_for_status().json()
                self.cache.put('meta','public_config',self.config)
            except (httpx.TransportError,httpx.HTTPStatusError) as exc:
                if isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code<500:raise
                self.config=self.cache.get('meta','public_config') if allow_offline else None
                if not self.config:raise
            if connect:self._connect()
            else:self.account=self.cache.get('session','current') or {}
        except BaseException:
            self.close()
            raise

    @property
    def token(self):
        return self.account.get('token','')

    def _connect(self):
        self.login()

    def _bind_owner(self,identifier):
        if not isinstance(identifier,str) or not identifier:
            raise RuntimeFault('protocol_error','Account response has no immutable owner identifier')
        owner=self.cache.get('meta','owner_id')
        cached=self.cache.get('session','current') or {}
        known={value for value in (owner,cached.get('id'),self.account.get('id')) if value is not None}
        if known and known!={identifier}:
            raise RuntimeFault('identity_conflict','Account response differs from the immutable local owner')
        if owner is None:self.cache.put('meta','owner_id',identifier)

    def _check_account(self):
        try:
            account=self.http.get(self.base+'/api/v1/me',headers={'Authorization':'Bearer '+self.token},timeout=10).raise_for_status().json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code>=500 and self._offline():return
            if exc.response.status_code!=401:raise
            raise RuntimeFault('authenticate','Saved credentials are invalid; explicitly log in again','authenticate') from None
        except httpx.TransportError:
            if self._offline():return
            raise
        if (account.get('username')!=self.username or not account.get('id')
                or self.account.get('id') and account['id']!=self.account['id']):
            raise RuntimeFault('identity_conflict','Saved credentials belong to another account')
        with self.cache.transaction():self._bind_owner(account['id'])
        self.account.update({key:account[key] for key in ('id','username','kind','display_name')})
        self.verified=True

    def _offline(self):
        self.verified=False
        expires=self.account.get('expiresAt')
        return bool(self.allow_offline and self.token and expires and instant(expires)>time.time())

    def _login_parameters(self,*,create=False):
        key=self.cache.get('meta','login_input_key')
        if key is None and create:
            key=secrets.token_hex(32);self.cache.put('meta','login_input_key',key)
        if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
            raise RuntimeFault('result_unknown','Original login input binding is unavailable; preserve the account state','inspect_original_login')
        body=json.dumps({'username':self.username,'password':self._password},sort_keys=True,separators=(',',':'))
        return hmac.new(bytes.fromhex(key),b'ioa-login-input\0'+body.encode(),hashlib.sha256).hexdigest()

    @staticmethod
    def _pending_login(value):
        if value is None:return None
        if (not isinstance(value,dict) or set(value)!={'operationId','parameters','phase'}
                or not isinstance(value['operationId'],str) or not 1<=len(value['operationId'])<=128
                or value['phase'] not in ('pending','rejected')
                or not isinstance(value['parameters'],str) or len(value['parameters'])!=64):
            raise RuntimeFault('result_unknown','Original pending login cannot be verified; preserve it','inspect_original_login')
        return value

    def _retire_rejection(self,pending):
        self.cache.put('login-attempts',pending['operationId'],pending)
        self.cache.delete('session','pending')

    def authenticate(self):
        """Explicit login only; valid credentials and unknown requests are reused."""
        with self.cache.transaction():
            cached=self.cache.get('session','current')
            pending=self._pending_login(self.cache.get('session','pending'))
            references={record['operationId'] for record in (cached,pending) if record and record.get('operationId')}
            if pending and pending['phase']=='rejected':
                self._retire_rejection(pending);pending=None
            may_reauthenticate=bool(cached and cached.get('token') and pending is None)
        try:result=self.login()
        except RuntimeFault as exc:
            if exc.code!='authenticate' or not may_reauthenticate:raise
            result=self.login(new_operation=True)
        return {**result,'newLogin':self.account.get('operationId') not in references}

    def login(self,*,new_operation=False):
        with self.cache.transaction():
            cached=self.cache.get('session','current')
            pending=self.cache.get('session','pending')
            if cached and isinstance(pending,dict) and cached.get('operationId')==pending.get('operationId'):
                self.cache.delete('session','pending');pending=None
            pending=self._pending_login(pending)
            if new_operation:
                if pending and pending['phase']=='rejected':
                    self._retire_rejection(pending);pending=None
                if pending:
                    raise RuntimeFault('result_unknown','Recover the pending login before starting another','recover_operation',operation_id=pending['operationId'])
                pending={'operationId':str(uuid.uuid4()),'parameters':self._login_parameters(create=True),'phase':'pending'}
            elif cached and not pending:self.account=cached
            if pending and pending['phase']=='rejected':
                raise RuntimeFault('authenticate','Credentials were rejected; correct local configuration and explicitly log in again','account.login',operation_id=pending['operationId'])
            if not self.token or new_operation or pending:
                pending=pending or {'operationId':str(uuid.uuid4()),'parameters':self._login_parameters(create=True),'phase':'pending'}
                if not hmac.compare_digest(pending['parameters'],self._login_parameters()):
                    raise RuntimeFault('result_unknown','Original pending login input changed; preserve the original operation','inspect_original_login',operation_id=pending['operationId'])
                self.cache.put('session','pending',pending)
        if pending is None:
            self._check_account()
            return self.status()
        response=self.http.post(self.base+'/api/v1/token',json={'username':self.username,'password':self._password,'operationId':pending['operationId']})
        try:response.raise_for_status()
        except httpx.HTTPStatusError:
            try:error=response.json().get('error',{})
            except (ValueError,AttributeError):error={}
            if response.status_code==429 and isinstance(error,dict) and error.get('code')=='rate_limited':
                raise RuntimeFault('rate_limited','Login rate limit reached; wait before resuming the original operation',
                                   'wait_rate_limit',operation_id=pending['operationId']) from None
            known=(response.status_code==401 and isinstance(error,dict) and (error.get('code')=='authenticate' or error.get('code')=='forbidden' and error.get('recovery')=='authenticate'))
            if known:
                with self.cache.transaction():
                    if self.cache.get('session','pending')==pending:
                        self.cache.put('session','pending',{**pending,'phase':'rejected'})
                raise RuntimeFault('authenticate','Credentials were rejected; correct local configuration and explicitly log in again','account.login',operation_id=pending['operationId']) from None
            raise
        account=response.json()
        if not isinstance(account,dict) or account.get('username')!=self.username or not account.get('token') or not account.get('id'):
            raise RuntimeFault('protocol_error','Login did not return the configured account')
        with self.cache.transaction():
            self._bind_owner(account['id'])
            self.cache.put('session','current',account)
            if self.cache.get('session','pending')==pending:self.cache.delete('session','pending')
        self.account=account
        self._check_account()
        return self.status()

    def refresh(self):
        response=self.http.post(self.base+'/api/v1/token/refresh',headers={'Authorization':'Bearer '+self.token})
        response.raise_for_status()
        renewed=response.json()
        if renewed.get('id')!=self.account.get('id') or renewed.get('credited') is not False:
            raise RuntimeFault('protocol_error','Credential renewal returned a conflicting account result')
        with self.cache.transaction():
            self._bind_owner(renewed['id'])
            self.account.update(renewed)
            self.cache.put('session','current',self.account)
        self.verified=True
        return self.status()

    def bearer(self):
        expires=self.account.get('expiresAt')
        if expires and time.time()<instant(expires)<time.time()+86400:
            self.refresh()
        return self.token

    def api(self,method,path,body=None):
        response=self.http.request(method,self.base+'/api/v1'+path,json=body,
                                   headers={'Authorization':'Bearer '+self.bearer()})
        response.raise_for_status()
        return response.json()

    def fetch_package(self,uri):
        prefix='ioalab://pkg/sha256:'
        if not isinstance(uri,str) or not uri.startswith(prefix):
            raise ValueError('Expected an IoA Lab package reference')
        digest=uri[len(prefix):]
        if len(digest)!=64 or any(char not in '0123456789abcdef' for char in digest):
            raise ValueError('Invalid package digest')
        headers={'Authorization':'Bearer '+self.bearer()}
        # Only this immutable GET is repeated; no model or business action is replayed.
        for attempt in range(3):
            try:
                blob=self.http.get(self.base+'/api/v1/packages/sha256:'+digest,
                                   headers=headers).raise_for_status().content
                break
            except (httpx.ReadError,httpx.RemoteProtocolError):
                if attempt==2:raise
                time.sleep(.15*(attempt+1))
        if digest_bytes(blob)!='sha256:'+digest:raise ValueError('Verifier package digest mismatch')
        return blob

    def status(self):
        expires=self.account.get('expiresAt')
        return {'id':self.account.get('id'),'username':self.username,'platform':self.base,
                'authenticated':bool(self.token) and (not expires or instant(expires)>time.time()),
                'verifiedOnline':self.verified,'expiresAt':expires,'operationId':self.account.get('operationId')}

    def close(self):
        self.http.close()
        self.cache.close()
