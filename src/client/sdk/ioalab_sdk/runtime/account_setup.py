"""One private account-page process shared by CLI, MCP and both role skills."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit
import httpx
from .config import DEFAULT_PLATFORM_URL, read_configuration, browser_executable
from .files import private_json, read_json
from .ipc import IpcServer, LocalClient
from .lease import Lease
from .network import Network
from .onboarding import Onboarding
from .protocol import RuntimeFault
from .user_ui import UserUi


def options_for(path, options):
    if not isinstance(options, dict) or set(options)-{'platform_url','role','user_browser','prepare_both_roles'} or options.get('role','assistant') not in ('assistant','service'):
        raise RuntimeFault('invalid_input','Use platform_url and the bound role')
    if type(options.get('prepare_both_roles',True)) is not bool:
        raise RuntimeFault('invalid_input','prepare_both_roles must be a boolean')
    path=Path(path).expanduser().resolve()
    original=read_configuration(path) if path.exists() else {}
    account=Onboarding(path)
    chosen=read_json(account.home/'options.json') if (account.home/'options.json').exists() else {}
    options={**chosen, **options}
    pending=account._read()
    if pending and pending.get('config'):original=pending['config']
    if pending and pending.get('phase') == 'reused':original=read_configuration(pending['existingConfiguration'])
    base=original.get('platform_url',options.get('platform_url',DEFAULT_PLATFORM_URL))
    parsed=urlsplit(base if isinstance(base,str) else '')
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeFault('invalid_input','Use the actual platform HTTP(S) address')
    if original and options.get('platform_url') and options['platform_url'].rstrip('/')!=base.rstrip('/'):
        raise RuntimeFault('identity_conflict','Reuse the original platform configuration')
    original_role=next((item.get('role') for item in original.get('identities',[]) if item.get('role') in ('assistant','service')),'assistant')
    if pending and pending.get('phase') not in ('ready','configured') and options.get('role',original_role)!=original_role:
        raise RuntimeFault('identity_conflict','Resume the original role before changing setup')
    browser=browser_executable(options.get('user_browser',original.get('user_browser')))
    if original and 'user_browser' in options and browser!=original.get('user_browser'):
        raise RuntimeFault('identity_conflict','Change the existing browser through runtime.configure')
    result={'platform_url':base.rstrip('/'),'role':options.get('role',original_role),
            'prepare_both_roles':options.get('prepare_both_roles',True)}
    if browser:result['user_browser']=browser
    return result


def status(path):
    account=Onboarding(path)
    state=account.status()
    receipt=account.home/'page.json'
    page=read_json(receipt) if receipt.exists() else {}
    phase=state['phase']
    return {'status':'completed' if phase in ('ready','configured','reused') else 'waiting',
            'data':{**state,**page,'stage':'account'},'error':None}



def setup(path, *, open_browser=False, options=None, embedded=False):
    return launch(path, options or {}, embedded=embedded) if open_browser or embedded else status(path)


def account_app_origin(path):
    """Resolve only the existing account-page owner; no launch, browser or login."""
    account=Onboarding(path)
    result=LocalClient(account.home,timeout=2).call('account.setup.app',options_for(path,{}))
    if result['status'] not in ('waiting','completed'):
        raise RuntimeFault('not_ready','Reopen the original account page')
    return result['_meta']['ioalab/account']['origin']


def launch(path, options, *, embedded=False):
    path=Path(path).expanduser().resolve();options=options_for(path,options)
    base=options['platform_url']
    try:
        with Network(base) as network:
            response=network.http.get(base+'/api/config',timeout=5);response.raise_for_status();public=response.json()
            if not isinstance(public,dict) or public.get('publicUrl','').rstrip('/')!=base:raise ValueError()
    except (httpx.HTTPError,OSError,ValueError,AttributeError):
        return {'status':'rejected','data':{'stage':'platform','platformUrl':base,'nextAction':'ask_platform_url'},
                'error':{'code':'not_ready','message':'Cannot reach the configured platform; ask the user for its address','recovery':'provide_platform_url'}}
    account=Onboarding(path);account.home.mkdir(parents=True,mode=0o700,exist_ok=True)
    with Lease(account.home/'launch.lock',timeout=0):
        client=LocalClient(account.home,timeout=2)
        try:
            return client.call('account.setup.app' if embedded else 'account.setup.open',options)
        except RuntimeFault as exc:
            if exc.code not in ('not_ready','unknown_result','authenticate'):raise
        try:probe=Lease(account.home/'page.lock',timeout=0)
        except RuntimeError:return {'status':'waiting','data':{'phase':'starting'},'error':None}
        probe.close()
        private_json(account.home/'options.json',options)
        run_id=str(uuid.uuid4())
        subprocess.Popen([sys.executable,'-m','ioalab_sdk.access','account-setup','--config',str(path),'--worker',run_id,
                          '--platform',base,'--role',options['role'], *(['--embedded'] if embedded else [])],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          cwd=str(path.parent),close_fds=True,start_new_session=os.name!='nt',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            result=status(path)
            if result['data'].get('runId')==run_id:
                return client.call('account.setup.app', options) if embedded else result
            time.sleep(.1)
        return {'status':'waiting','data':{'phase':'starting','runId':run_id},'error':None}


def serve(path,options,run_id,*,timeout=900,embedded=False):
    account=Onboarding(path);account.home.mkdir(parents=True,mode=0o700,exist_ok=True)
    with Lease(account.home/'page.lock',timeout=0):
        private_json(account.home/'options.json',options_for(Path(path),options))
        chosen=read_json(account.home/'options.json')
        ui=UserUi(onboarding=account,browser=chosen.get('user_browser'))
        def dispatch(name,operation,values):
            if name not in ('account.setup.open','account.setup.app','account.setup.status') or name=='account.setup.status' and values:
                raise RuntimeFault('forbidden','Account page only accepts reopening and status')
            if name in ('account.setup.open','account.setup.app'):
                chosen=read_json(account.home/'options.json')
                if values!=chosen and (not account.path.exists() or not isinstance(values,dict) or values.get('platform_url')!=chosen['platform_url']):
                    raise RuntimeFault('identity_conflict','Resume the original platform and bound setup role')
                if name == 'account.setup.app':
                    return {**status(path), '_meta': {'ioalab/account': ui.account_app_entry()}, 'operationId': operation}
                ui.open()
            return {**status(path),'operationId':operation}
        server=IpcServer(account.home,'account-setup',dispatch,user_ui=ui).start()
        try:
            if not embedded:ui.open()
            private_json(account.home/'page.json',{'runId':run_id,'opened':not embedded})
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline and not account.cancelled:time.sleep(.1)
        finally:server.close()


def account_page():
    """Reuse the account view with an App-only direct credential transport."""
    from importlib.resources import files
    root = files('ioalab_sdk').joinpath('runtime/web/identity')
    page = root.joinpath('setup.html').read_text(encoding='utf-8')
    bridge = root.joinpath('app.js').read_text(encoding='utf-8')
    return page.replace('<script>', '<script>' + bridge + '\n', 1)
