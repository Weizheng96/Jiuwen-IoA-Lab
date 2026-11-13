"""Private, resumable account setup, independent of MCP, roles and the resident."""
import hashlib
from pathlib import Path
import secrets
import uuid
import subprocess
import sys

import httpx

from .config import RuntimeConfig, DEFAULT_PLATFORM_URL, data_directory, read_configuration
from .credentials import CredentialVault
from .authentication import migrate_configuration
from .files import private_json, read_json, restrict, prepare_home
from .lease import Lease
from .network import Network
from .protocol import RuntimeFault, encode
from .session import AccountSession


class Onboarding:
    def __init__(self, config_path):
        self.cancelled = False
        self.path = Path(config_path).expanduser().resolve()
        self.home = self.path.parent / ('.ioa-setup-' + hashlib.sha256(str(self.path).encode()).hexdigest()[:16])

    def _vault(self):
        return CredentialVault(self.home, {'configuration':str(self.path)})

    def _read(self, *, migrate=False):
        path = self.home / 'pending.json'
        if not path.exists():
            document=read_configuration(self.path) if self.path.exists() else {'data_dir':str(self.path.with_suffix('.data'))}
            legacy=data_directory(self.path,{'data_dir':str(self.path.with_suffix('.data')),**document})/'account-setup.json'
            if not legacy.exists():return None
            old=read_json(legacy)
            if old.get('migrated'):return None
            original=old.get('configuration',{})
            body=old.get('body',{})
            if (document.get('platform_url',old.get('platform'))!=old.get('platform')
                    or document.get('username',body.get('username'))!=body.get('username')):
                raise RuntimeFault('identity_conflict','Resume account setup at its original platform and account')
            config={**original,'username':body['username'],'password':body['password']}
            config.pop('password_env',None)
            RuntimeConfig.from_mapping(self.path,config)
            public={'phase':'unknown','mode':'register' if old['mode']=='register' else 'login',
                    'inputDigest':'legacy-resume-only','config':config,'registration':body,'registrationEndpoint':'/api/v1/accounts','legacyJournalPending':True}
            if old.get('registration'):public['registered']=old['registration']
            if migrate:
                if old['mode']=='register':self._vault().update(generatedPassword=body['password'])
                self._save(public)
                self._finish_legacy_migration(public)
            return public
        public = read_json(path)
        if 'config' in public or 'registration' in public:
            # Explicit setup recovery migrates its original pending inputs before reuse.
            if migrate:self._save(public)
            return public
        record=self._vault().read().get('pending')
        if migrate and record and record.get('legacyJournalPending'):self._finish_legacy_migration(record)
        return record

    def _finish_legacy_migration(self, record):
        legacy=data_directory(self.path,record['config'])/'account-setup.json'
        old=read_json(legacy)
        if not old.get('migrated'):
            if old.get('body')!=record['registration'] or old.get('platform')!=record['config']['platform_url']:
                raise RuntimeFault('identity_conflict','Original registration journal changed; preserve both records')
            private_json(legacy,{'migrated':True,'configuration':str(self.path)})
        record.pop('legacyJournalPending',None)
        self._save(record)

    def _save(self, record):
        self._vault().update(pending=record)
        private_json(self.home/'pending.json', {key:record[key] for key in ('phase','account','error') if key in record})

    def migrate_legacy_record(self, account):
        record=self._read()
        if not record or not record.get('config'):return
        legacy=record['config']
        registered=(record.get('registered') or {}).get('id')
        if (legacy.get('username')!=account.username or legacy.get('platform_url','').rstrip('/')!=account.base
                or registered and registered!=account.account.get('id')):
            raise RuntimeFault('identity_conflict','Original setup belongs to another account; preserve its private records')
        with Lease(self.home/'account.lock',timeout=0):
            record=self._read(migrate=True)
            if record.get('mode')=='register' and legacy.get('password'):
                self._vault().update(generatedPassword=legacy['password'])
            self._save({'phase':'ready','account':account.status()})

    def status(self):
        record = self._read()
        if record:
            return {key: record[key] for key in ('phase', 'account', 'error') if key in record}
        if self.path.exists():
            config = RuntimeConfig.load(self.path)
            return {'phase': 'configured', 'account': {'username': config.username, 'platform': config.platform}}
        options=read_json(self.home/'options.json') if (self.home/'options.json').exists() else {}
        return {'phase': 'input', 'defaults': {'platform':options.get('platform_url',DEFAULT_PLATFORM_URL),'role':options.get('role','assistant')}}

    def configuration(self):
        from .configuration import inspect_config
        from .client import ClientOperations
        if not self.path.exists(): return None
        return {'configuration': inspect_config(self.path), 'runtime': ClientOperations(self.path).call('runtime.status')}

    def credentials(self, values):
        """One-time generated-password recovery, exclusively through the trusted page."""
        if values not in ({},{'action':'discard'}):raise RuntimeFault('invalid_input','Take or discard the generated password')
        vault=self._vault()
        with vault.lock():
            records=vault.read()
            password=records.pop('generatedPassword',None)
            if values:
                vault.write(records)
                return {'cleared':True}
            if not password:raise RuntimeFault('not_ready','No generated password remains; use your saved password')
            vault.write(records)
        config=RuntimeConfig.load(self.path)
        return {'username':config.username,'password':password,'platform':config.platform}

    def devices(self, values):
        try:return self._devices(values)
        except (httpx.HTTPError,ValueError):
            raise RuntimeFault('result_unknown','Device action is unverified; preserve and resume its original request','resume_original') from None

    def _devices(self, values):
        action=values.get('action')
        allowed={'list':{'action'},'revoke':{'action','sessionId'},'login':{'action','password'},'resume':{'action'}}
        if action not in allowed or set(values)!=allowed[action]:
            raise RuntimeFault('invalid_input','Use the controlled account actions')
        config=RuntimeConfig.load(self.path)
        prepare_home(config.home)
        try:guard=Lease(config.home/'client.lock',timeout=0)
        except RuntimeError:raise RuntimeFault('not_ready','Safely stop the current client before managing device sessions','runtime.stop') from None
        with guard:
            password=values.get('password','')
            if not isinstance(password,str) or len(password)>8192:raise RuntimeFault('invalid_input','Use a bounded password')
            account=AccountSession(config.platform,config.username,password,config.home,connect=False)
            try:
                if action=='login':
                    result=account.authenticate()
                    migrate_configuration(config,account)
                    self._save({'phase':'ready','account':result})
                    return result
                if action=='list':
                    account.login()
                    return account.sessions()
                session=values.get('sessionId')
                if action=='resume':session=None
                if action!='resume' and (not isinstance(session,str) or not session or len(session)>128):
                    raise RuntimeFault('invalid_input','Select a device session')
                result=account.revoke(session)
                if not account.token:
                    self._save({'phase':'configured','account':{'username':config.username,'platform':config.platform}})
                return result
            finally:account.close()

    def control(self, values):
        """Restricted browser controls, sharing the existing CLI's config validation and lease."""
        action = values.get('action')
        if action == 'stop' and set(values) == {'action', 'bootId'}:
            from .client import ClientOperations
            return ClientOperations(self.path).call('runtime.stop', {'bootId': values['bootId']})
        if action != 'configure' or set(values) != {'action', 'expectedDigest', 'changes'}:
            raise RuntimeFault('invalid_input', 'Use configuration changes or stop the inspected instance')
        self.home.mkdir(parents=True, mode=0o700, exist_ok=True)
        try: guard = Lease(self.home / 'configuration.lock', timeout=0)
        except RuntimeError: raise RuntimeFault('not_ready', 'Another configuration action is still running') from None
        with guard:
            path = self.home / 'configuration-changes.json'
            private_json(path, {key: values[key] for key in ('expectedDigest', 'changes')})
            try:
                result = subprocess.run([sys.executable, '-m', 'ioalab_sdk.access', 'configure', '--config', str(self.path),
                                         '--args-file', str(path)], capture_output=True, text=True, timeout=20)
                return json_result(result.stdout)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeFault('result_unknown', 'Configuration result is unverified; inspect the current summary') from None

    def submit(self, values):
        """Trusted-input API. Never expose this method as an ordinary MCP tool."""
        if values == {'mode':'cancel'}:
            self.cancelled=True
            return {'cancelled':True}
        if not isinstance(values, dict) or set(values) - {'mode', 'platform', 'username', 'password', 'invite', 'role', 'name'}:
            raise RuntimeFault('invalid_input', 'Use the account page fields')
        self.home.mkdir(parents=True, mode=0o700, exist_ok=True)
        restrict(self.home, directory=True)
        try: guard = Lease(self.home / 'account.lock', timeout=0)
        except RuntimeError: raise RuntimeFault('not_ready', 'Original account setup is still running', 'account.setup.status') from None
        with guard:
            record = self._read(migrate=True)
            options=read_json(self.home/'options.json') if (self.home/'options.json').exists() else {}
            if values and options:
                if values.get('role',options['role'])!=options['role'] or values.get('platform',options['platform_url']).rstrip('/')!=options['platform_url']:
                    raise RuntimeFault('identity_conflict','Reuse the platform and role selected by the original setup')
                values={**values,'role':options['role'],'platform':options['platform_url']}
            fingerprint = hashlib.sha256(encode(values).encode()).hexdigest()
            if record and record['phase']=='ready':
                current=RuntimeConfig.load(self.path)
                saved=record.get('account',{})
                if saved.get('username')!=current.username or saved.get('platform',current.platform)!=current.platform:
                    raise RuntimeFault('identity_conflict','Reuse the original platform and account')
                if values:raise RuntimeFault('id_conflict','An existing account must be reused')
                return self.status()
            if record and record['phase']=='configured':record=None
            if record and values and fingerprint != record['inputDigest']:
                if record['phase'] != 'rejected':
                    raise RuntimeFault('id_conflict', 'Recover the original account setup before changing input')
                record = None
            if record is None:
                if self.path.exists():
                    if values: raise RuntimeFault('id_conflict', 'An existing configuration must be reused, not replaced')
                    config = RuntimeConfig.load(self.path)
                    # Existing configuration uses the original explicit login and its lease.
                    from .authentication import login
                    return {'phase': 'ready', 'account': login(self.path)['data']}
                if not values: raise RuntimeFault('invalid_input', 'Enter an account or choose registration')
                mode = values.get('mode')
                role = values.get('role', 'assistant')
                if any(not isinstance(value, str) or len(value) > 8192 for value in values.values()):
                    raise RuntimeFault('invalid_input', 'Account fields must be bounded strings')
                if mode not in ('login', 'register') or role not in ('assistant', 'service'):
                    raise RuntimeFault('invalid_input', 'Choose login or registration and a supported identity')
                username = values.get('username', '').strip() or ('ioa_' + secrets.token_hex(8) if mode == 'register' else '')
                password = values.get('password') or (secrets.token_urlsafe(32) if mode == 'register' else '')
                platform = values.get('platform', '').rstrip('/')
                identity = hashlib.sha256(encode([platform, username]).encode()).hexdigest()[:24]
                document = {'platform_url': platform, 'username': username, 'password': password,
                            'data_dir': str(self.path.parent / 'clients' / identity),
                            'identities': [{'key': 'personal' if role == 'assistant' else 'service',
                                            'role': role, 'name': values.get('name') or 'IoA ' + role}]}
                RuntimeConfig.from_mapping(self.path, document)
                if mode=='register' and not values.get('password'):
                    self._vault().update(generatedPassword=password)
                record = {'phase': 'pending', 'mode': mode, 'inputDigest': fingerprint, 'config': document,
                          'registrationEndpoint':'/api/v1/accounts',
                          'registration': {'operationId': str(uuid.uuid4()), 'username': username, 'password': password,
                                           'display_name': values.get('name') or username, 'invite': values.get('invite', '')}}
                self._save(record)
            try:
                if record['mode'] == 'register' and (not record.get('registered') or record.get('registrationEndpoint')=='/api/v1/accounts' and not record['registered'].get('sessionId')):
                    with Network(record['config']['platform_url']) as network:
                        response = network.http.post(network.base + record.get('registrationEndpoint','/api/auth/register'), json=record['registration'], timeout=15)
                        response.raise_for_status()
                        receipt = response.json()
                    if (receipt.get('operationId') != record['registration']['operationId']
                            or receipt.get('username') != record['config']['username'] or not receipt.get('id')):
                        raise RuntimeFault('result_unknown', 'Original registration receipt needs recovery')
                    record['registered'] = receipt
                    self._save(record)
                config = RuntimeConfig.from_mapping(self.path, record['config'])
                prepare_home(config.home)
                try: account_guard = Lease(config.home / 'client.lock', timeout=0)
                except RuntimeError: raise RuntimeFault('not_ready', 'The existing client owns this account; inspect its status') from None
                with account_guard:
                    receipt=record.get('registered') or {}
                    sdk_registration=record['mode']=='register' and record.get('registrationEndpoint')=='/api/v1/accounts'
                    account = AccountSession(config.platform, config.username, config.password, config.home, connect=False,
                        registration_operation=record['registration']['operationId'] if sdk_registration else None)
                    try:
                        if sdk_registration:
                            summary=account.accept_registration(receipt,record['registration']['operationId'])
                        else:summary=account.authenticate()
                    finally: account.close()
                    document={k:v for k,v in record['config'].items() if k not in ('password','password_env')}
                    document['credential_ref']=account.vault.reference
                    if self.path.exists() and read_json(self.path) not in (record['config'],document):
                        raise RuntimeFault('id_conflict', 'Configuration appeared during setup; preserve both original records')
                    private_json(self.path,document)
                record={'phase':'ready','account':summary}
                record.pop('error', None)
            except (httpx.HTTPError, ValueError, RuntimeFault) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                rejected = status in (400,422)
                if rejected:
                    try:rejected=exc.response.json().get('error',{}).get('code')=='invalid_input'
                    except (ValueError,AttributeError):rejected=False
                rejected = rejected or isinstance(exc, RuntimeFault) and exc.code == 'authenticate'
                # Registration already succeeded: a later login failure cannot authorize a new account.
                rejected = rejected and not record.get('registered')
                record['phase'] = 'rejected' if rejected else 'unknown'
                # Neither arbitrary platform text nor exception URLs may contain secrets in outputs.
                record['error'] = {'code': 'account_rejected' if rejected else 'result_unknown',
                                   'httpStatus': status, 'recovery': 'correct_account_input' if rejected else 'resume_original',
                                   'message': '账号请求被拒绝，请检查注册政策、邀请码或凭据。' if rejected else '原账号操作尚未核实，请恢复原请求。'}
                if isinstance(exc, httpx.HTTPStatusError):
                    # Only fixed, documented policy messages; never arbitrary server text.
                    reasons = {'用户名已被占用': '账号已被占用，请登录原账号或填写另一个名称。',
                               '当前不开放注册': '平台未开放注册，请使用已有账号或联系平台管理员。',
                               '邀请码无效': '平台要求有效邀请码，请重新填写。',
                               '密码至少 6 位': '密码至少需要 6 位。', '密码至少 8 位': '密码至少需要 8 位。'}
                    try: reason = exc.response.json().get('error', {}).get('message')
                    except (ValueError, AttributeError): reason = None
                    if isinstance(reason,str):
                        known=next((key for key in reasons if reason==key or reason.endswith(': '+key)),None)
                        if known:record['error']['message']=reasons[known]
                    if status == 429: record['error']['message'] = '请求过于频繁，请稍后恢复原请求。'
            self._save(record)
            return self.status()


def json_result(value):
    import json
    try:
        result = json.loads(value)
        if not isinstance(result, dict) or 'status' not in result: raise ValueError()
        return result
    except ValueError: raise RuntimeFault('result_unknown', 'Local configuration response is incomplete') from None
