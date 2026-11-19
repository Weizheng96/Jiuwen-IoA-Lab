"""Account access shared by both roles. Credentials never enter model-facing results."""
from pathlib import Path
from urllib.parse import urlsplit
import os
import hashlib
import hmac
import json
import secrets
import time
import uuid

import httpx
from a2c.codec import digest_bytes
from .network import Network
from .credentials import CredentialVault
from .account_store import LocalStore
from .protocol import RuntimeFault,instant


class AccountSession:
    def __init__(self,base,username,password,home,*,connect=True,allow_offline=False,registration_operation=None):
        parsed=urlsplit(base)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('A platform HTTP(S) address without embedded credentials is required')
        self.base,self.username,self._password=base.rstrip('/'),username,password
        self.home=Path(home)
        self.home.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.cache=LocalStore(self.home/'account.sqlite')
        self.cache.db.execute('PRAGMA secure_delete=ON')
        if os.name!='nt':os.chmod(self.cache.path,0o600)
        try:self.network=Network(self.base)
        except BaseException:
            self.cache.close()
            raise
        self.http=self.network.http
        self.account={}
        self.vault=CredentialVault(self.home, {'platform':self.base,'username':username})
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
            with self.vault.lock():
                secrets_record=self.vault.read()
                self.vault.write(secrets_record)
                self.account=secrets_record.get('current') or {}
                device=self.cache.get('meta','device_id')
                if not device:
                    device=registration_operation or str(uuid.uuid4());self.cache.put('meta','device_id',device)
                self.device_id=device
            if connect:self._connect()
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
            cached=self.account
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
        with self.vault.lock():
            self._secrets=self.vault.read()
            self.account=self._secrets.get('current') or {}
            if not self._password:self._password=(self._secrets.get('loginInput') or {}).get('password','')
            return self._login(new_operation=new_operation)

    def _login(self,*,new_operation=False):
        with self.cache.transaction():
            cached=self.account
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
                if not self._password:
                    raise RuntimeFault('authenticate','Open the controlled account page to log in again','account.setup')
                pending=pending or {'operationId':str(uuid.uuid4()),'parameters':self._login_parameters(create=True),'phase':'pending'}
                if not hmac.compare_digest(pending['parameters'],self._login_parameters()):
                    raise RuntimeFault('result_unknown','Original pending login input changed; preserve the original operation','inspect_original_login',operation_id=pending['operationId'])
                self.cache.put('session','pending',pending)
        if pending is None:
            expires=self.account.get('expiresAt')
            if self._secrets.get('refreshPending') or not expires or instant(expires)<=time.time()+60:
                self._refresh_locked()
            self._check_account()
            return self.status()
        self._secrets['loginInput']={'password':self._password}
        self.vault.write(self._secrets)
        response=self.http.post(self.base+'/api/v1/token',json={'username':self.username,'password':self._password,
            'operationId':pending['operationId'],'deviceId':self.device_id,'deviceName':'IoA client'})
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
            self._persist(account)
            if self.cache.get('session','pending')==pending:self.cache.delete('session','pending')
        self.account=account
        self._password=''
        self._check_account()
        return self.status()

    def _validate_receipt(self, account):
        fields=('id','token','refreshToken','sessionId','deviceId','expiresAt','sessionExpiresAt')
        if (not isinstance(account,dict) or any(not isinstance(account.get(key),str) or not 1<=len(account[key])<=8192 for key in fields)
                or account['deviceId']!=self.device_id):
            raise RuntimeFault('protocol_error','Device authentication receipt is incomplete')
        try:
            if instant(account['expiresAt'])>instant(account['sessionExpiresAt']):raise ValueError()
        except (ValueError,TypeError,OverflowError):
            raise RuntimeFault('protocol_error','Device authentication expiry is invalid') from None

    def _persist(self, account):
        self._validate_receipt(account)
        self._secrets['current']=account
        self._secrets.pop('loginInput',None)
        self._secrets.pop('refreshPending',None)
        self.vault.write(self._secrets)
        self.cache.put('session','current',{k:v for k,v in account.items() if k not in ('token','refreshToken')})

    def _refresh_locked(self):
        pending=self._secrets.get('refreshPending')
        if not pending:
            if not self.account.get('refreshToken'):
                raise RuntimeFault('authenticate','Open the controlled account page to log in again','account.setup')
            pending={'refreshToken':self.account['refreshToken'],'operationId':str(uuid.uuid4())}
            self._secrets['refreshPending']=pending
            self.vault.write(self._secrets)
        response=self.http.post(self.base+'/api/v1/token/refresh',json=pending)
        if response.status_code==401:
            self.verified=False
            raise RuntimeFault('authenticate','Device session is expired or revoked; explicitly log in again','account.setup')
        response.raise_for_status()
        renewed=response.json()
        self._validate_receipt(renewed)
        if (renewed.get('id')!=self.account.get('id') or renewed.get('credited') is not False
                or renewed.get('sessionId')!=self.account.get('sessionId') or renewed.get('deviceId')!=self.device_id):
            raise RuntimeFault('protocol_error','Credential renewal returned a conflicting device account')
        with self.cache.transaction():
            self._bind_owner(renewed['id'])
            account={**self.account,**renewed}
            self._persist(account)
        self.account=account
        self.verified=True
        return self.status()

    def refresh(self):
        with self.vault.lock():
            self._secrets=self.vault.read()
            self.account=self._secrets.get('current') or {}
            return self._refresh_locked()

    def accept_registration(self, receipt, operation):
        """Persist the original registration device session without another login award."""
        if (not isinstance(receipt,dict) or receipt.get('username')!=self.username
                or receipt.get('operationId')!=operation):
            raise RuntimeFault('result_unknown','Registration receipt differs from the original account operation')
        with self.vault.lock():
            self._secrets=self.vault.read()
            self._validate_receipt(receipt)
            if self.cache.get('session','pending'):
                raise RuntimeFault('result_unknown','Recover the original pending login before accepting registration')
            current=self._secrets.get('current')
            if current:
                if current.get('operationId')!=operation or current.get('sessionId')!=receipt.get('sessionId'):
                    raise RuntimeFault('identity_conflict','Preserve the original installed device session')
                # A resumed setup must not roll back a refresh already persisted by this device.
                self.account=current
                if self._secrets.get('refreshPending') or instant(current['expiresAt'])<=time.time()+60:
                    self._refresh_locked()
                self._check_account()
            else:
                self.account=dict(receipt)
                self._check_account()
                with self.cache.transaction():self._persist(self.account)
            self._password=''
            return self.status()

    def bearer(self):
        expires=self.account.get('expiresAt')
        if not expires or instant(expires)<=time.time()+60:
            self.refresh()
        return self.token

    def sessions(self):
        return self.api('GET','/sessions')

    def revoke(self, session=None):
        # Persist the exact authorization before sending; never refresh a revoked session
        # merely to retrieve the receipt for a lost logout response.
        authorization=None
        pending=self.vault.read().get('revokePending')
        if (not pending and session) or (pending and self.token and pending['sessionId']!=self.account.get('sessionId')):
            authorization=self.bearer()
        with self.vault.lock():
            records=self.vault.read()
            pending=records.get('revokePending')
            if pending and session and pending['sessionId']!=session:
                raise RuntimeFault('result_unknown','Recover the original device revocation first','resume_revocation')
            if pending and authorization:
                pending['token']=authorization
                records['revokePending']=pending
                self.vault.write(records)
            if not pending:
                if not session:
                    raise RuntimeFault('not_ready','No device revocation is pending')
                pending={'sessionId':session,'token':authorization}
                records=self.vault.read()
                records['revokePending']=pending
                self.vault.write(records)
            response=self.http.post(self.base+'/api/v1/sessions/'+pending['sessionId']+'/revoke',json={},
                                    headers={'Authorization':'Bearer '+pending['token']})
            if response.status_code==401:
                # A fresh explicit login can authorize recovery of the original target.
                if self.token and self.token!=pending['token']:
                    pending['token']=self.token
                    records['revokePending']=pending
                    self.vault.write(records)
                raise RuntimeFault('authenticate','Log in again, then resume the original revocation','account.setup')
            response.raise_for_status()
            result=response.json()
            if result!={'sessionId':pending['sessionId'],'status':'revoked'}:
                raise RuntimeFault('result_unknown','Device revocation receipt needs recovery','resume_revocation')
            if pending['sessionId']==self.account.get('sessionId'):
                for key in ('current','refreshPending','loginInput'):records.pop(key,None)
                self.cache.delete('session','current')
                self.account={};self.verified=False;self._password=''
            records.pop('revokePending',None)
            self.vault.write(records)
            return result

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
                'verifiedOnline':self.verified,'expiresAt':expires,'operationId':self.account.get('operationId'),
                'sessionId':self.account.get('sessionId'),'deviceId':self.device_id,
                'sessionExpiresAt':self.account.get('sessionExpiresAt'),'credentialRef':self.vault.reference}

    def close(self):
        self.network.close()
        self.cache.close()
