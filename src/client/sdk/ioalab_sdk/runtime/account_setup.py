"""One private account-page process shared by CLI, MCP and both role skills."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit
import httpx
from .config import DEFAULT_PLATFORM_URL, read_configuration
from .files import private_json, read_json
from .ipc import IpcServer, LocalClient
from .lease import Lease
from .network import Network
from .onboarding import Onboarding
from .protocol import RuntimeFault
from .user_ui import UserUi


def options_for(path, options):
    if not isinstance(options, dict) or set(options)-{'platform_url','role'} or options.get('role','assistant') not in ('assistant','service'):
        raise RuntimeFault('invalid_input','Use platform_url and the bound role')
    path=Path(path).expanduser().resolve()
    original=read_configuration(path) if path.exists() else {}
    pending=Onboarding(path)._read()
    if pending and pending.get('config'):original=pending['config']
    base=original.get('platform_url',options.get('platform_url',DEFAULT_PLATFORM_URL))
    parsed=urlsplit(base if isinstance(base,str) else '')
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeFault('invalid_input','Use the actual platform HTTP(S) address')
    if original and options.get('platform_url') and options['platform_url'].rstrip('/')!=base.rstrip('/'):
        raise RuntimeFault('identity_conflict','Reuse the original platform configuration')
    original_role=next((item.get('role') for item in original.get('identities',[]) if item.get('role') in ('assistant','service')),'assistant')
    if pending and pending.get('phase') not in ('ready','configured') and options.get('role',original_role)!=original_role:
        raise RuntimeFault('identity_conflict','Resume the original role before changing setup')
    return {'platform_url':base.rstrip('/'),'role':options.get('role',original_role)}


def status(path):
    account=Onboarding(path)
    state=account.status()
    receipt=account.home/'page.json'
    page=read_json(receipt) if receipt.exists() else {}
    phase=state['phase']
    return {'status':'completed' if phase in ('ready','configured') else 'waiting',
            'data':{**state,**page,'stage':'account','nextAction':'configure_host' if phase in ('ready','configured') else 'complete_local_account_page'},'error':None}


def setup(path, *, open_browser=False, options=None):
    return launch(path, options or {}) if open_browser else status(path)


def launch(path, options):
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
            return client.call('account.setup.open',options)
        except RuntimeFault as exc:
            if exc.code not in ('not_ready','unknown_result','authenticate'):raise
        try:probe=Lease(account.home/'page.lock',timeout=0)
        except RuntimeError:return {'status':'waiting','data':{'phase':'starting'},'error':None}
        probe.close()
        private_json(account.home/'options.json',options)
        run_id=str(uuid.uuid4())
        subprocess.Popen([sys.executable,'-m','ioalab_sdk.access','account-setup','--config',str(path),'--worker',run_id,
                          '--platform',base,'--role',options['role']],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          cwd=str(path.parent),close_fds=True,start_new_session=os.name!='nt',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            result=status(path)
            if result['data'].get('runId')==run_id:return result
            time.sleep(.1)
        return {'status':'waiting','data':{'phase':'starting','runId':run_id},'error':None}


def serve(path,options,run_id,*,timeout=900):
    account=Onboarding(path);account.home.mkdir(parents=True,mode=0o700,exist_ok=True)
    with Lease(account.home/'page.lock',timeout=0):
        private_json(account.home/'options.json',options_for(Path(path),options))
        ui=UserUi(onboarding=account)
        def dispatch(name,operation,values):
            if name not in ('account.setup.open','account.setup.status') or name=='account.setup.status' and values:
                raise RuntimeFault('forbidden','Account page only accepts reopening and status')
            if name=='account.setup.open':
                chosen=read_json(account.home/'options.json')
                if values!=chosen and (not account.path.exists() or not isinstance(values,dict) or values.get('platform_url')!=chosen['platform_url']):
                    raise RuntimeFault('identity_conflict','Resume the original platform and bound setup role')
                ui.open()
            return {**status(path),'operationId':operation}
        server=IpcServer(account.home,'account-setup',dispatch,user_ui=ui).start()
        try:
            ui.open();private_json(account.home/'page.json',{'runId':run_id,'opened':True})
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline and not account.cancelled:time.sleep(.1)
        finally:server.close()
