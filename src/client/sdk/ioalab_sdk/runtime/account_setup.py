"""Short, user-only enrollment controller. No role, host or business database is loaded."""
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .authentication import authenticate
from .account_store import LocalStore
from .config import DEFAULT_PLATFORM_URL,RuntimeConfig,read_configuration,data_directory
from .files import prepare_home,private_json,read_json
from .ipc import IpcServer,LocalClient
from .lease import Lease
from .network import Network
from .protocol import RuntimeFault
from .session import AccountSession
from .user_ui import UserUi


def setup_document(path,options):
    if not isinstance(options,dict) or set(options)-{'platform_url','role'} or options.get('role','assistant') not in ('assistant','service'):
        raise RuntimeFault('invalid_input','Use platform_url and role (assistant or service)','correct_input')
    if 'platform_url' in options and (not isinstance(options['platform_url'],str) or not options['platform_url'].strip()):
        raise RuntimeFault('invalid_input','Use a nonempty platform_url address','correct_input')
    document=read_configuration(path) if path.exists() else {
        'platform_url':options.get('platform_url',DEFAULT_PLATFORM_URL),'data_dir':str(path.with_suffix('.data')),
        'identities':[{'key':options.get('role','assistant'),'role':options.get('role','assistant'),'name':'我的智能体'}]}
    if not path.exists():
        previous=Path(document['data_dir'])/'account-setup.json'
        if previous.exists():document=read_json(previous)['configuration']
    document.setdefault('platform_url',DEFAULT_PLATFORM_URL)
    base=document['platform_url']
    parsed=urlsplit(base if isinstance(base,str) else '')
    if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeFault('invalid_input','Provide the actual platform HTTP(S) address','configure')
    if options.get('platform_url') and options['platform_url'].rstrip('/')!=base.rstrip('/'):
        raise RuntimeFault('identity_conflict','Reuse the original platform configuration','inspect_configuration')
    return document


class AccountSetupUi(UserUi):
    page_name='account.html'

    def __init__(self,path,options):
        self.path=Path(path).resolve()
        self.document=setup_document(self.path,options)
        self.home=data_directory(self.path,self.document)
        prepare_home(self.home)
        self.journal=self.home/'account-setup.json'
        self.receipt=self.home/'account-setup-result.json'
        self.done=False
        self._initial=self.path.read_bytes() if self.path.exists() else None
        # UserUi owns one-time entry, same-origin session and CSRF checks. Enrollment
        # uses the same channel but has no confirmation authority or resident RPC.
        super().__init__(None,browser=self.document.get('user_browser'))

    def snapshot(self):
        result=read_json(self.receipt) if self.receipt.exists() else {'status':'waiting'}
        credentials=None
        if self.journal.exists():
            record=read_json(self.journal)
            if record['mode']=='register':credentials={key:record['body'][key] for key in ('username','password')}
        return {'result':result,'credentials':credentials}

    def decision(self,body,session):
        with self._lock:
            if body=={'mode':'cancel'}:return self._submit(body)
            try:guard=Lease(self.home/'client.lock',timeout=0)
            except RuntimeError:raise RuntimeFault('not_ready','Safely stop the resident before changing credentials','runtime.stop') from None
            with guard:return self._submit(body)

    def _submit(self,body):
        if set(body)-{'mode','username','password','invite'} or body.get('mode') not in ('existing','register','cancel'):
            raise RuntimeFault('invalid_input','Choose existing, register or cancel','correct_input')
        if body['mode']=='cancel':
            self.done=True
            return {'cancelled':True,'nextAction':'account.setup'}
        current=self.path.read_bytes() if self.path.exists() else None
        if current!=self._initial:
            raise RuntimeFault('configuration_changed','Configuration changed; reopen account setup','account.setup')
        record=read_json(self.journal) if self.journal.exists() else None
        if record and record['platform']!=self.document['platform_url']:
            raise RuntimeFault('identity_conflict','Resume registration at the original platform','account.setup')
        if record and self.document.get('username') and record['body']['username']!=self.document['username']:
            raise RuntimeFault('identity_conflict','Keep the original configured account','account.setup')
        if record and record['phase']=='completed':return record['result']
        if record and record['phase']=='pending':
            if body['mode']!=record['mode']:
                raise RuntimeFault('result_unknown','Resume the original account operation first','account.setup')
            if record['mode']=='existing' and any(body.get(key,record['body'][key])!=record['body'][key] for key in ('username','password')):
                raise RuntimeFault('result_unknown','Preserve the original login credentials','account.setup')
        else:
            if body['mode']=='register' and self.document.get('username'):
                raise RuntimeFault('identity_conflict','This configuration already has an account; use existing login','account.setup')
            values={'username':'ioa_'+secrets.token_hex(8),'password':secrets.token_urlsafe(32),'invite':body.get('invite','')} if body['mode']=='register' else {key:body.get(key,'') for key in ('username','password')}
            if not all(isinstance(value,str) for value in values.values()) or not values['username'] or not values['password']:
                raise RuntimeFault('invalid_input','Enter credentials in this local page','correct_input')
            if self.document.get('username') and values['username']!=self.document['username']:
                raise RuntimeFault('identity_conflict','Keep the original configured account','account.setup')
            if (self.home/'account.sqlite').exists():
                cache=LocalStore(self.home/'account.sqlite')
                try:pending=cache.get('session','pending')
                finally:cache.close()
                if pending and (not isinstance(pending,dict) or pending.get('phase')!='rejected'):
                    raise RuntimeFault('result_unknown','Recover the original pending login before entering new credentials','account.login')
            values['operationId']=str(uuid.uuid4())
            record={'mode':body['mode'],'phase':'pending','body':values,'platform':self.document['platform_url'],
                'configuration':self.document}
            private_json(self.journal,record)
        if record['platform']!=self.document['platform_url']:
            raise RuntimeFault('identity_conflict','Resume registration at the original platform','account.setup')
        try:
            if record['mode']=='register' and record['phase']=='pending':
                with Network(self.document['platform_url'].rstrip('/')) as network:
                    response=network.http.post(self.document['platform_url'].rstrip('/')+'/api/v1/accounts',json=record['body'])
                if response.status_code>=500 or response.status_code==429:
                    raise RuntimeFault('result_unknown','Wait and resume the original registration; platform unavailable or rate limited','account.setup')
                if response.status_code!=200:
                    # Only a structured platform rejection establishes absence of effects.
                    try:error=response.json()['error'];message=error['message']
                    except (ValueError,KeyError,TypeError):raise RuntimeFault('result_unknown','Unrecognized registration response; preserve original request','account.setup') from None
                    if response.status_code not in (400,422) or error.get('code')!='invalid_input':
                        raise RuntimeFault('result_unknown','Original registration is unverified; inspect the original request','account.setup')
                    record['phase']='rejected';private_json(self.journal,record)
                    raise RuntimeFault('invalid_input',message,'account.setup')
                registered=response.json()
                if registered.get('username')!=record['body']['username'] or not registered.get('id'):
                    raise RuntimeFault('result_unknown','Invalid registration result; resume original request','account.setup')
                record['registration']=registered;private_json(self.journal,record)
            updated={**self.document,'username':record['body']['username'],'password':record['body']['password']}
            updated.pop('password_env',None)
            current=self.path.read_bytes() if self.path.exists() else None
            if current!=self._initial:
                raise RuntimeFault('configuration_changed','Configuration changed during authentication; preserve the original operation','account.setup')
            config=RuntimeConfig.from_mapping(self.path,updated)
            if record['mode']=='register':
                account=AccountSession(config.platform,config.username,config.password,config.home,connect=False)
                try:result=account.accept_registration(record['registration'],record['body']['operationId'])
                finally:account.close()
            else:
                result=authenticate(config)
                if not result['data']['newLogin'] and ('password' in self.document or 'password_env' in self.document):
                    updated=self.document
            current=self.path.read_bytes() if self.path.exists() else None
            if current!=self._initial:
                raise RuntimeFault('configuration_changed','Configuration changed during authentication; preserve the original operation','account.setup')
            private_json(self.path,updated);self._initial=self.path.read_bytes();self.document=updated
            result={**result,'data':{**result['data'],'stage':'account','nextAction':'runtime.configuration'}}
            record.update(phase='completed',result=result['data']);private_json(self.journal,record)
            private_json(self.receipt,result)
            return result['data']
        except RuntimeFault as exc:
            if exc.code=='authenticate':
                record['phase']='rejected';private_json(self.journal,record)
            raise
        except (httpx.HTTPError,ValueError):
            raise RuntimeFault('result_unknown','Original account result is unverified; resume with the same credentials','account.setup') from None


def status(path):
    path=Path(path).resolve()
    document=read_configuration(path) if path.exists() else {'data_dir':str(path.with_suffix('.data'))}
    receipt=data_directory(path,document)/'account-setup-result.json'
    return read_json(receipt) if receipt.exists() else {'status':'waiting','data':{'stage':'account','nextAction':'account.setup'},'error':None}


def serve(path,options,run_id,*,timeout=900):
    ui=AccountSetupUi(path,options)
    with Lease(ui.home/'account-setup.lock',timeout=0):
        endpoint=ui.home/'account-ui';prepare_home(endpoint)
        def reopen(operation,operation_id,arguments):
            if operation!='account.setup.open' or arguments:
                raise RuntimeFault('forbidden','Account page only accepts reopening')
            with ui._lock:
                if ui.done:raise RuntimeFault('not_ready','Account page is closing; retry the original setup','account.setup')
                ui.open()
            return {'operationId':operation_id,'status':'completed','data':{'opened':True},'error':None}
        server=IpcServer(endpoint,'account-setup',reopen,user_ui=ui).start()
        try:
            with ui._lock:
                try:ui.open()
                except RuntimeFault:
                    private_json(ui.receipt,{'status':'rejected','data':{'stage':'account','runId':run_id},
                        'error':{'code':'not_ready','message':'The local browser could not be opened','recovery':'configure_browser'}})
                    return
                private_json(ui.receipt,{'status':'waiting','data':{'stage':'account','runId':run_id,'nextAction':'complete_local_account_page'},'error':None})
            deadline=time.monotonic()+timeout
            while not ui.done and time.monotonic()<deadline:
                time.sleep(.1)
        finally:
            server.close()
    record=read_json(ui.journal) if ui.journal.exists() else {}
    if record.get('phase')!='completed':private_json(ui.receipt,{'status':'waiting','data':{'stage':'account','nextAction':'account.setup','cancelled':ui.done},'error':None})


def launch(path,options):
    document=setup_document(Path(path).resolve(),options)
    base=document['platform_url'].rstrip('/')
    try:
        with Network(base) as network:
            response=network.http.get(base+'/api/config',timeout=5)
            response.raise_for_status()
            public=response.json()
            if not isinstance(public,dict) or public.get('publicUrl','').rstrip('/')!=base:
                raise ValueError('Platform address mismatch')
    except (httpx.HTTPError,OSError,ValueError,AttributeError):
        return {'status':'rejected','data':{'stage':'platform','platformUrl':base,'nextAction':'ask_platform_url'},
                'error':{'code':'not_ready','message':'Cannot reach the configured platform; ask the user for its address',
                         'recovery':'provide_platform_url'}}
    ui=AccountSetupUi(path,options)
    with Lease(ui.home/'account-setup-launch.lock',timeout=0):
        try:probe=Lease(ui.home/'account-setup.lock',timeout=0)
        except RuntimeError:
            result=status(path)
            if result.get('status')=='waiting' and (result.get('data') or {}).get('nextAction')=='complete_local_account_page':
                LocalClient(ui.home/'account-ui').call('account.setup.open',{})
            return result
        probe.close()
        run_id=str(uuid.uuid4())
        subprocess.Popen([sys.executable,'-m','ioalab_sdk.access','account-setup','--config',str(ui.path),
            '--worker',run_id,'--platform',ui.document['platform_url'],'--role',options.get('role','assistant')],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            result=status(path)
            if (result.get('data') or {}).get('runId')==run_id:return result
            time.sleep(.05)
        raise RuntimeFault('not_ready','Account browser did not become ready; retain the original state','account.setup')
