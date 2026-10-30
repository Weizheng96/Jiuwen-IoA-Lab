"""Account access shared by both roles. Credentials never enter model-facing results."""
from pathlib import Path
from urllib.parse import urlsplit
import os
import time
import uuid

import httpx
from a2c.codec import digest_bytes
from ..store import LocalStore
from .protocol import RuntimeFault,instant


class AccountSession:
    def __init__(self,base,username,password,home,*,legacy=None,connect=True):
        parsed=urlsplit(base)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('A platform HTTP(S) address without embedded credentials is required')
        self.base,self.username,self._password=base.rstrip('/'),username,password
        self.home=Path(home)
        self.home.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.cache=LocalStore(self.home/'account.sqlite')
        if os.name!='nt':os.chmod(self.cache.path,0o600)
        self.http=httpx.Client(timeout=120,trust_env=False)
        self.account={}
        try:
            binding={'platform':self.base,'username':username}
            with self.cache.transaction():
                old=self.cache.get('meta','binding')
                if old and old!=binding:
                    raise RuntimeFault('identity_conflict','Account directory belongs to another platform or user')
                self.cache.put('meta','binding',binding)
                if legacy and not self.cache.get('session','current'):
                    self.cache.put('session','current',legacy)
            self.config=self.http.get(self.base+'/api/config').raise_for_status().json()
            if connect:self._connect()
            else:self.account=self.cache.get('session','current') or {}
        except BaseException:
            self.close()
            raise

    @property
    def token(self):
        return self.account.get('token','')

    def _connect(self):
        with self.cache.transaction():
            cached=self.cache.get('session','current')
            pending=self.cache.get('session','pending')
            if cached and pending and cached.get('operationId')==pending['operationId']:
                self.cache.delete('session','pending')
                pending=None
            if cached and not pending:self.account=cached
        if self.token:
            self._check_account()
        else:
            self.login()

    def _check_account(self):
        try:
            account=self.http.get(self.base+'/api/v1/me',headers={'Authorization':'Bearer '+self.token}).raise_for_status().json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code!=401:raise
            raise RuntimeFault('authenticate','Saved credentials are invalid; explicitly log in again','authenticate') from None
        if account.get('username')!=self.username:
            raise RuntimeFault('identity_conflict','Saved credentials belong to another account')
        self.account.update({key:account[key] for key in ('id','username','kind','display_name')})

    def login(self,*,new_operation=False):
        with self.cache.transaction():
            cached=self.cache.get('session','current')
            pending=self.cache.get('session','pending')
            if new_operation:
                if pending:
                    raise RuntimeFault('result_unknown','Recover the pending login before starting another','recover_operation')
                pending={'operationId':str(uuid.uuid4())}
            elif cached and not pending:
                self.account=cached
            if not self.token or new_operation or pending:
                pending=pending or {'operationId':str(uuid.uuid4())}
                self.cache.put('session','pending',pending)
        if pending is None:
            self._check_account()
            return self.status()
        response=self.http.post(self.base+'/api/v1/token',json={'username':self.username,'password':self._password,**pending})
        response.raise_for_status()
        account=response.json()
        if account.get('username')!=self.username or not account.get('token'):
            raise RuntimeFault('protocol_error','Login did not return the configured account')
        with self.cache.transaction():
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
        self.account.update(renewed)
        self.cache.put('session','current',self.account)
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
        blob=self.http.get(self.base+'/api/v1/packages/sha256:'+digest,
                           headers={'Authorization':'Bearer '+self.bearer()}).raise_for_status().content
        if digest_bytes(blob)!='sha256:'+digest:raise ValueError('Verifier package digest mismatch')
        return blob

    def status(self):
        expires=self.account.get('expiresAt')
        return {'id':self.account.get('id'),'username':self.username,'platform':self.base,
                'authenticated':bool(self.token) and (not expires or instant(expires)>time.time()),'expiresAt':expires}

    def close(self):
        self.http.close()
        self.cache.close()
