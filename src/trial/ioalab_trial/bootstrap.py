"""Concrete ephemeral four-service recipe; public owners perform business writes."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

ROOT = Path('/epoch')
PYTHON = sys.executable
CORE_PYTHON = '/opt/core/bin/python'
SWARM_PYTHON = '/opt/swarm/bin/python'


def public_seed(service_ids, manifest=None):
    from ioalab_services.catalog import seed_manifest
    manifest = seed_manifest() if manifest is None else manifest
    wanted=set(service_ids)
    if len(wanted)!=4:raise ValueError('Use exactly four programming service IDs')
    domains=[]
    for domain in manifest['domains']:
        services=[{key:service[key] for key in ('id','name','description','formation','runnable','pricing','category') if key in service}
                  for service in domain['services'] if service['id'] in wanted]
        if services:
            domains.append({'account':domain['account'],'label':domain['label'],
                'category':{key:domain['category'][key] for key in ('id','name','description') if key in domain['category']},'services':services})
    if (len(domains)!=1 or domains[0]['category']['id']!='cat_dev'
            or {service['id'] for domain in domains for service in domain['services']}!=wanted):
        raise ValueError('Only the complete programming trial catalog may initialize the core')
    return {'source':manifest.get('source',{}),'domains':domains}


def register_assistant(client,password,epoch):
    response=client.post('/api/auth/register',json={'username':'trial-user','password':password,
        'display_name':'本轮试用用户','operationId':'trial-register-'+epoch})
    response.raise_for_status()
    cookie=response.cookies.get('ioalab_session')
    if not cookie:raise RuntimeError('Public registration did not return its browser session')
    observed=client.get('/api/session');observed.raise_for_status()
    if observed.json().get('role')!='user':raise RuntimeError('Trial business identity must be a non-admin user')
    return {'cookie':cookie,'username':'trial-user'}


def core_environment(model, admin):
    return {'IOALAB_MODE':'trial','IOALAB_BIND':'127.0.0.1','IOALAB_PORT':'8750',
        'IOALAB_PUBLIC_URL':'http://127.0.0.1:8750','IOALAB_DATA':'/epoch/core/data',
        'IOALAB_SECRET':secrets.token_hex(32),'IOALAB_ADMIN_PASSWORD':admin,
        'IOALAB_ASSISTANT_PASSWORD':secrets.token_urlsafe(24),'IOALAB_ISC_COUNT':'1',
        'IOALAB_LOGIN_CREDIT':'1000','IOALAB_REGISTRATION':'open',
        'IOALAB_AUTOSEED':'1','IOALAB_TRAFFIC':'off','IOALAB_SEED_FILE':'/epoch/core/seed.json',
        'IOALAB_LLM_API_KEY':model['api_key'],'IOALAB_LLM_BASE_URL':model['base_url'],
        'IOALAB_LLM_MODEL':model['name'],'IOALAB_A2X_MODEL':model['name']}


def write(path,value,uid):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    path.chmod(0o600)
    os.chown(path,uid,uid)


def native_env(name):
    return {**os.environ,'HOME':str(ROOT/name),'TMPDIR':str(ROOT/name/'tmp'),
        'JIUWENSWARM_DATA_DIR':str(ROOT/name/'swarm'),
        'JIUWENSWARM_CONFIG_DIR':str(ROOT/name/'swarm/config'),'JIUWENSWARM_CLI_PORTS':'1',
        'JIUWENSWARM_A2UI_ENABLED':'1',
        'AGENT_SERVER_HOST':'127.0.0.1','AGENT_SERVER_PORT':'8000',
        'GATEWAY_HOST':'127.0.0.1','GATEWAY_PORT':'8001','WEB_HOST':'127.0.0.1','WEB_PORT':'8002',
        'FRONTEND_HOST':'0.0.0.0','FRONTEND_PORT':'8080','GATEWAY_URL':'http://127.0.0.1:8002'}


def deployment():
    # The existing resource coordinator is reused with an explicitly restricted
    # catalog. Neither SDK nor official resources import this optional recipe.
    from ioalab_services.deployment import Deployment
    from a2c.codec import digest
    result = Deployment(ROOT/'services/config.json')
    ids = set(json.loads(Path('/opt/trial/service-ids.json').read_text()))
    if len(ids) != 4:
        raise ValueError('Trial requires the reviewed four programming candidates')
    domains = []
    for original in result.domains:
        selected = [service for service in original['services'] if service['id'] in ids]
        if selected:
            domains.append({**original,'services':selected})
    if {s['id'] for d in domains for s in d['services']} != ids or len(domains) != 1 or domains[0]['slug'] != 'dev':
        raise ValueError('Only the explicit programming trial catalog is supported')
    result.domains = domains
    result.binding = digest({'configuration':result.value,'trialDomains':domains})
    return result


def setup():
    model = json.loads(os.environ.pop('IOALAB_TRIAL_MODEL','{}'))
    if set(model) != {'name','base_url','api_key'} or not all(isinstance(v,str) and v for v in model.values()):
        raise ValueError('Deployment must provide a model; missing model is not readiness')
    for name,uid in (('core',10001),('native',10003),('assistant',10002),('services',10004)):
        (ROOT/name/'tmp').mkdir(exist_ok=True)
        os.chown(ROOT/name/'tmp',uid,uid)
    # The original public workspace/model configuration API runs under the actual owner.
    for name,uid in (('native',10003),('assistant',10002),('services',10004)):
        source = """import json,os,sys
from pathlib import Path
from jiuwenswarm.common.utils import prepare_workspace
prepare_workspace(False,preferred_language='zh',workspace_dir=Path(os.environ['JIUWENSWARM_DATA_DIR']))
"""
        subprocess.run([SWARM_PYTHON,'-c',source],env=native_env(name),cwd=ROOT/name,user=uid,group=uid,extra_groups=[],check=True,timeout=60)
        source = """import json,sys
from jiuwenswarm.common.config import upsert_model_resource,update_config
value=json.load(sys.stdin)
upsert_model_resource({'model_id':'trial-model','model_name':value['name'],'api_base':value['base_url'],'api_key':value['api_key'],'client_provider':'OpenAI','alias':'Trial','is_default':True})
def change(value):
 value.setdefault('updater',{})['enabled']=False
 value.setdefault('a2ui',{})['enabled']=True
 return value
update_config(change)
"""
        subprocess.run([SWARM_PYTHON,'-c',source],input=json.dumps(model),text=True,env=native_env(name),cwd=ROOT/name,
                       user=uid,group=uid,extra_groups=[],check=True,timeout=30)
    admin,assistant = secrets.token_urlsafe(24),secrets.token_urlsafe(24)
    core = core_environment(model,admin)
    write(ROOT/'core/environment.json',core,10001)
    write(ROOT/'services/config.json',{'platform_url':core['IOALAB_PUBLIC_URL'],
        'data_dir':'/epoch/services/official','host':{'command':['/opt/swarm/bin/jiuwenswarm-process'],
        'data_dir':'/epoch/services/swarm'},'max_model_runs':1},10004)
    ids=json.loads(Path('/opt/trial/service-ids.json').read_text())
    write(ROOT/'core/seed.json',public_seed(ids),10001)
    write(ROOT/'assistant/config.json',{'platform_url':core['IOALAB_PUBLIC_URL'],
        'username':'trial-user','password':assistant,'data_dir':'/epoch/assistant/resident',
        'identities':[{'key':'personal','role':'assistant','name':'试用个人助手'}],
        'max_model_runs':1,'assistant':{},'provider':{},
        'host':{'command':['/opt/swarm/bin/jiuwenswarm-process'],'data_dir':'/epoch/assistant/swarm'}},10002)
    # Install the built product skill into the original public skill directory;
    # no history store or original source is edited.
    source = r"""import json,shutil
from pathlib import Path
from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
target=SkillManager().skills_dir/'ioalab-delegate'
shutil.copytree('/opt/trial/assistant-skill',target)
home=Path('/epoch/native/.ioalab');home.mkdir()
(home/'settings.local.json').write_text('{}',encoding='utf-8')
(target/'scripts/ioalab.py').write_text('from ioalab_trial.model_bridge import main\nraise SystemExit(main())\n',encoding='utf-8')
"""
    subprocess.run([SWARM_PYTHON,'-c',source],env=native_env('native'),cwd=ROOT/'native',user=10003,group=10003,extra_groups=[],check=True,timeout=30)
    # Initialization and publication are delegated to their existing public owners.
    core_env = {**os.environ,**core,'HOME':'/epoch/core','TMPDIR':'/epoch/core/tmp'}
    subprocess.run([CORE_PYTHON,'-m','ioalab','init'],env=core_env,cwd=ROOT/'core',
                   user=10001,group=10001,extra_groups=[],check=True,timeout=60,stdout=subprocess.DEVNULL)
    temporary = subprocess.Popen([CORE_PYTHON,'-m','ioalab','serve'],env=core_env,cwd=ROOT/'core',
                   user=10001,group=10001,extra_groups=[],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        import httpx
        for _ in range(60):
            try:
                if httpx.get(core['IOALAB_PUBLIC_URL']+'/api/config',timeout=1).is_success:break
            except httpx.HTTPError:pass
            time.sleep(.2)
        with httpx.Client(base_url=core['IOALAB_PUBLIC_URL'],trust_env=False,timeout=10) as http:
            receipt=register_assistant(http,assistant,os.environ['IOALAB_TRIAL_EPOCH'])
        write(ROOT/'control/probe-session.json',receipt,0)
        # Account/category bootstrap is the public core startup operation; no trial SQL.
        from ioalab_services.preparation import prepare
        from ioalab_services.authorization import authorize,administrator_session
        # Resource preparation runs under service UID through this recipe's entry point.
        subprocess.run([PYTHON,'-m','ioalab_trial.bootstrap','prepare-services'],env={**os.environ,'HOME':'/epoch/services','TMPDIR':'/epoch/services/tmp'},cwd=ROOT/'services',
                       user=10004,group=10004,extra_groups=[],check=True,timeout=90,stdout=subprocess.DEVNULL)
        dep = deployment()
        authorize(dep,administrator_session(dep,admin))
        for item in (ROOT/'services/official').rglob('*'):
            os.chown(item,10004,10004)
    finally:
        temporary.terminate()
        temporary.wait(timeout=30)


def main():
    mode = sys.argv[1]
    if mode == 'setup':setup()
    elif mode == 'prepare-services':
        from ioalab_services.preparation import prepare
        prepare(deployment())
    elif mode == 'core':
        env={**os.environ,**json.loads((ROOT/'core/environment.json').read_text())}
        os.execve(CORE_PYTHON,[CORE_PYTHON,'-m','ioalab','serve'],env)
    elif mode == 'services':
        from ioalab_services.lifecycle import serve
        raise SystemExit(serve(deployment()))
    elif mode == 'native':
        children=[]
        try:
            for module in ('jiuwenswarm.server.app_agentserver','jiuwenswarm.gateway.app_gateway','jiuwenswarm.channels.web.app_web'):
                children.append(subprocess.Popen([SWARM_PYTHON,'-m',module],env=native_env('native')))
            while all(child.poll() is None for child in children):time.sleep(.2)
        finally:
            for child in children:
                if child.poll() is None:child.terminate()
            for child in children:child.wait()
    else:raise ValueError('Unknown fixed trial component')


if __name__ == '__main__':main()
