"""Default installed-package deployment and fixed safe upgrade stages."""
from contextlib import contextmanager,ExitStack
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.request import build_opener,ProxyHandler,HTTPRedirectHandler,Request
from urllib.parse import urlsplit

from . import activation,backup,units
from .builder import Builder
from .configs import local_url
from .environment import Environment, validation_code
from .files import path_without_links, save, writer
from .jobs import source_identity
from .layout import read_environment
from .linux import Accounts, Systemd, expose_runtime
from .processes import child_environment
from .release import UNITS, load, official_catalog, read_json
from .workspace import Workspace


class DirectEndpoint(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def http_open(url):
    return build_opener(ProxyHandler({}),DirectEndpoint()).open(url,timeout=3)


def http_json(url):
    with http_open(url) as response:
        return json.load(response)


@contextmanager
def execution_leases(layout,*,administrator):
    configuration=layout.config/'agents/official.json'
    home=Path(read_json(configuration)['data_dir'])
    home=path_without_links(home if home.is_absolute() else configuration.parent/home)
    if not home.is_relative_to(layout.state/'agents'):
        raise RuntimeError('The original official state must remain in the owned agents role')
    state=read_json(home/'state.json');accounts=state.get('accounts',{})
    if not accounts:raise RuntimeError('Restore the original prepared official account mapping')
    locks=[home/'deployment.lock']
    configs=[]
    for name in sorted(accounts):
        if not re.fullmatch(r'ISP_\d{3}',name):raise RuntimeError('Restore the original official account names')
        configs.append(home/'accounts'/name/'config.json')
    if administrator:
        config=layout.state/'agents/admin-assistant/config.json'
        if config.exists():
            configs.append(config)
        else:
            # An explicitly replaced instance has no new SDK config yet. Prove
            # the old writer is released using its real archived execution lock.
            replacement=read_json(path_without_links(layout.private/'replace-admin-assistant.json'))
            metadata=read_json(path_without_links(layout.private/'installation.json'))
            archive=path_without_links(layout.private/'retired-isc-assistant')
            original=read_json(archive/'runtime/config.json')
            preparation=read_json(archive/'preparation/state.json')
            if (replacement.get('deploymentId')!=layout.inspect()['deploymentId']
                    or replacement.get('phase')!='completed' or metadata.get('administratorPrepared') is not False
                    or metadata.get('administratorAccount')!='admin' or config.parent.exists()
                    or original.get('username')!='ISC_001'
                    or Path(original.get('data_dir',''))!=config.parent/'resident'
                    or preparation.get('identity',{}).get('agentId')!=replacement.get('agentId')):
                raise RuntimeError('Missing SDK configuration is not a verified fresh administrator replacement')
            locks.append(archive/'runtime/resident/client.lock')
    for config in configs:
        value=read_json(path_without_links(config));directory=value.get('data_dir')
        if not isinstance(directory,str) or not directory:raise RuntimeError('Restore the original participant state mapping')
        directory=Path(directory)
        directory=path_without_links(directory if directory.is_absolute() else config.parent/directory)
        if not directory.is_relative_to(layout.state/'agents'):
            raise RuntimeError('An SDK execution directory escaped its original service role')
        locks.append(directory/'client.lock')
    with ExitStack() as held:
        for path in locks:
            path=path_without_links(path)
            if not path.is_file():raise RuntimeError('Restore the actual original SDK/coordinator execution lock before upgrade')
            held.enter_context(writer(path))
        yield


class Upgrade:
    def __init__(self,layout,operation,candidate,manager,plans,contents,record,save):
        self.layout,self.operation,self.candidate=layout,operation,candidate
        self.manager,self.plans,self.contents=manager,plans,contents
        self.record,self.save=record,save

    def saved(self,**values):self.record.update(values);self.save()

    def released(self):
        return all(self.manager.released(name,state) for name,state in self.record['before'].items())

    def run(self):
        if not self.record:
            previous=activation.current(self.layout)
            if previous is None:raise RuntimeError('An upgrade requires the original activated deployment')
            before=self.manager.freeze([row['unit'] for row in self.plans.values()])
            self.saved(stage='quiescing',previous=previous,candidate=self.candidate,before=before)
        if (self.record.get('candidate')!=self.candidate or self.record.get('stage') not in
                ('quiescing','snapshot','activating','restarting','completed')):
            raise RuntimeError('Continue the original complete upgrade candidate and stage')
        if self.record['stage']=='quiescing':
            for group in units.STOP_GROUPS:
                names=[self.plans[key]['unit'] for key in group if key in self.plans]
                for name in names:self.manager.request_stop(name,self.record['before'][name])
                while not all(self.manager.released(name,self.record['before'][name]) for name in names):time.sleep(.2)
            self.saved(stage='snapshot')
        if self.record['stage'] in ('snapshot','activating'):
            with execution_leases(self.layout,administrator='assistant' in self.plans):
                if not self.released():raise RuntimeError('Original service execution changed before the snapshot or switch')
                if self.record['stage']=='snapshot':
                    result=backup.capture(self.layout,self.operation,self.record['previous'],released=self.released)
                    self.saved(stage='activating',backup=result)
                activation.activate(self.layout,self.candidate,self.record['previous']['setId'])
                self.manager.install(self.contents)
                self.saved(stage='restarting')
        if self.record['stage']=='restarting':
            if activation.current(self.layout)!=self.candidate:
                raise RuntimeError('The active candidate changed; do not restart another installation')
            for key in units.START_ORDER:
                if key not in self.plans:continue
                name=self.plans[key]['unit'];original=self.record['before'][name]
                current=self.manager.owned(name)
                if current.get('UnitFileState')!=original.get('UnitFileState'):
                    raise RuntimeError('The original boot enablement changed during upgrade; preserve it for review')
                if original.get('ActiveState') in ('active','activating','reloading'):
                    self.manager.start(name)
            self.saved(stage='completed')
        # This result means the fixed switch/restart requests finished. The outer
        # installer must still verify actual core/SDK/host/interface readiness.
        if activation.current(self.layout)!=self.candidate:
            raise RuntimeError('The current environment no longer matches this original upgrade')
        return {'stage':self.record['stage'],'setId':self.candidate['setId'],
                'backup':self.record.get('backup'),'runtimeReadyChecked':False}


class Installation:
    """Fixed first-install / safe-upgrade orchestration; all business work stays in SDKs."""
    def __init__(self,job,configuration):
        self.job,self.layout,self.config=job,job.layout,configuration
        self.request=job.request();self.progress_path=job.folder/'progress.json'
        self.metadata_path=self.layout.private/'installation.json'
        self.metadata=read_json(self.metadata_path) if self.metadata_path.exists() else {
            'deploymentId':job.owner,'units':{},'accounts':{},'paths':{}}
        if self.metadata.get('deploymentId')!=job.owner:
            raise RuntimeError('Restore the original installation ownership')
        self.accounts=Accounts(self.layout,job.owner,self.metadata,self.save_metadata)
        self.manager=Systemd(job.owner,self.metadata['units'],self.save_metadata)
        self.plans=units.plans(self.layout,administrator=self.config.input['admin_assistant']['enabled'],core=self.config.input,trial=self.config.input.get('trial'))
        self.contents=units.render(self.layout,job.owner,self.plans)
        from .repair import initial_progress
        self.progress=read_json(self.progress_path) if self.progress_path.exists() else initial_progress(self.request,self.plans)

    def save_metadata(self):save(self.metadata_path,self.metadata)
    def saved(self,**values):self.progress.update(values);save(self.progress_path,self.progress)

    def role(self,name):
        value=self.accounts.ensure(name)
        return {'uid':value['uid'],'gid':value['gid'],'home':value['home'],'name':value['name']}

    def command(self,argv,*,role=None,environment=None,value=None,marker=None,timeout=180,cwd=None):
        context={};env=child_environment();directory=self.layout.private
        if role:
            identity=self.role(role)
            context={'user':identity['uid'],'group':identity['gid'],'extra_groups':[]}
            env.update(HOME=identity['home'],USER=identity['name'],LOGNAME=identity['name'])
            directory=Path(identity['home'])
        env.update(environment or {})
        result=subprocess.run(list(map(str,argv)),cwd=Path(cwd) if cwd is not None else directory,env=env,
            input=json.dumps(value) if value is not None else '',capture_output=True,
            text=True,encoding='utf-8',timeout=timeout,**context)
        with (self.job.folder/'subprocess.log').open('a',encoding='utf-8') as log:
            log.write(result.stdout);log.write(result.stderr)
        if result.returncode:
            raise RuntimeError('An installed preparation/query failed; inspect the original private subprocess log')
        if marker:
            rows=[line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)]
            if len(rows)!=1:raise RuntimeError('The original build did not return one release receipt')
            return rows[0]
        for line in reversed(result.stdout.splitlines()):
            try:answer=json.loads(line)
            except ValueError:continue
            if isinstance(answer,dict) and answer.get('status')=='completed':return answer
        raise RuntimeError('The original installed command returned no completed receipt')

    def installed(self,module,*arguments,role=None,environment=None,value=None):
        return self.command([self.layout.environments/'current/participant/bin/python',
            '-I','-X','utf8','-m',module,*arguments],role=role,environment=environment,value=value)

    def candidate(self):
        if source_identity(self.request['source']['root'])!=self.request['source']:
            raise RuntimeError('Restore the original source before resuming the build')
        builder=Builder(self.layout.root/'build',Path(self.request['source']['root'])/'deploy/locks/build.lock')
        python=builder.ensure(self.config.input['pypi_index'],retry_failed=True)
        code="import sys; from deploy.build import build; print('IOALAB_RELEASE:'+str(build(sys.argv[1],sys.argv[2])))"
        path=Path(self.command([python,'-X','utf8','-B','-c',code,self.layout.root/'releases','linux-x86_64'],
                              marker='IOALAB_RELEASE:',timeout=None,cwd=self.request['source']['root']))
        if source_identity(self.request['source']['root'])!=self.request['source']:
            raise RuntimeError('Source changed during the original release build')
        results={name:Environment(path,name,self.layout.environments).install(
            index=self.config.input['pypi_index'],retry_failed=True) for name in UNITS}
        candidate=activation.prepare_set(self.layout,path,results)
        expose_runtime(self.layout,candidate)
        for unit,entry in candidate['units'].items():
            prefix=Path(entry['environment']);versions=read_json(prefix/'deployment.json')['versions']
            catalog=official_catalog(path,load(path)) if unit=='participant' else None
            arguments=validation_code(unit,versions,catalog)
            arguments[1]+="\nprint(json.dumps({'status':'completed'}))\n"
            role='core' if unit=='core' else 'agents'
            self.command([prefix/'bin/python','-I','-X','utf8',*arguments],role=role,
                         environment={'A2C_CACHE_DIR':str(self.layout.state/'cache'/role)})
        return candidate

    def directories(self):
        self.accounts.prepare_roots()
        for role in ('core','agents'):
            self.role(role)
            for path in (self.layout.state/role,self.layout.state/'cache'/role):
                self.accounts.reserve(path,role);self.accounts.handoff(path,role)

    def permissions(self):
        for key in self.config.record['files']:
            path=Path(key)
            if path.is_relative_to(self.layout.config):
                role='core' if path.is_relative_to(self.layout.config/'core') else 'agents'
                self.accounts.configuration(path,role)

    def workspace(self,name,*,defer_handoff=False):
        home=self.layout.state/'agents'/name
        ownership=self.accounts.reserve(home,'agents',create=False)
        identity=self.role('agents') if ownership['phase']=='owned' else None
        result=Workspace(self.layout.environments/'current/swarm/bin/python',home,self.job.owner,
                         run_as=identity).prepare(self.config.input['model'])
        if result['status']!='completed':raise RuntimeError('Configure the original real model before proceeding')
        if not defer_handoff:self.accounts.handoff(home,'agents')
        return result

    def core_ready(self):
        values=self.config.core()
        import ipaddress
        bind=values['runtime']['bind'];ipaddress.ip_address(bind)
        port=int(values['runtime']['port'])
        if not 1<=port<=65535:raise ValueError('Use a valid configured core port')
        for url in (local_url(bind,port),values['runtime']['public_url']):
            self.command([self.layout.environments/'current/core/bin/python','-I','-m','ioalab.readiness',
                          '--url',url,'--expected-public-url',self.config.input['public_url'],'--timeout','0'],role='core')

    def wait(self,query):
        until=time.monotonic()+180
        while True:
            try:return query()
            except (RuntimeError,OSError,ValueError):
                if time.monotonic()>=until:raise RuntimeError('Managed readiness is not yet verified; inspect the original units and private logs') from None
                time.sleep(1)

    def bootstrap(self):
        official=self.workspace('swarm-official')
        self.config.official(official)
        self.permissions()
        home=self.layout.state/'agents/official'
        self.accounts.reserve(home,'agents');self.accounts.handoff(home,'agents')
        self.manager.install(self.contents)
        self.manager.start(self.plans['core']['unit']);self.wait(self.core_ready)
        environment=read_environment(self.layout.config/'agents/official.env')
        arguments=['--config',str(self.layout.config/'agents/official.json')]
        if not self.metadata.get('officialPrepared'):
            self.installed('ioalab_services','prepare',*arguments,role='agents',environment=environment)
            self.metadata['officialPrepared']=True;self.save_metadata()
        if not self.metadata.get('officialAuthorized'):
            admin=self.config.core()['admin']
            self.installed('ioalab_services','authorize',*arguments,'--admin-username',admin['username'],'--admin-password-env','IOALAB_BOOTSTRAP_ADMIN_PASSWORD',
                role='agents',environment={**environment,'IOALAB_BOOTSTRAP_ADMIN_PASSWORD':admin['password']})
            self.metadata['officialAuthorized']=True;self.save_metadata()
        if self.config.input['admin_assistant']['enabled']:
            admin=self.workspace('swarm-admin',defer_handoff=True)
            recipe=self.config.administrator(admin)
            self.accounts.reserve(self.layout.state/'agents/admin-assistant','agents',create=False)
            if not self.metadata.get('administratorPrepared'):
                self.installed('ioalab_admin_assistant','prepare','--config',str(recipe),
                    environment=read_environment(self.layout.config/'agents/assistant.env'))
                self.metadata['administratorPrepared']=True;self.save_metadata()
            self.metadata['administratorAccount']='admin';self.save_metadata()
            self.accounts.handoff(self.layout.state/'agents/admin-assistant','agents')
            self.accounts.handoff(self.layout.state/'agents/swarm-admin','agents')
        for key in units.START_ORDER:
            if key in self.plans:
                name=self.plans[key]['unit'];self.manager.enable(name);self.manager.start(name)
        self.metadata['initialized']=True;self.save_metadata()

    def sdk(self,operation,arguments):
        code='''import json,sys
from ioalab_sdk.runtime import LocalClient, RuntimeConfig, require_configuration
c=RuntimeConfig.load(sys.argv[1]);client=LocalClient(c.home,timeout=2)
r=client.call('runtime.status')
require_configuration(r['data'],c,assembly='ioalab-jiuwen')
if sys.argv[2]!='runtime.status':r=LocalClient(c.home,boot_id=r['data']['bootId']).call(sys.argv[2],json.load(sys.stdin))
print(json.dumps(r,ensure_ascii=False))
'''
        return self.command([self.layout.environments/'current/participant/bin/python','-I','-X','utf8','-c',code,
            self.layout.state/'agents/admin-assistant/config.json',operation],role='agents',
            environment=read_environment(self.layout.config/'agents/assistant.env'),value=arguments)

    def readiness(self):
        required=self.progress['requiredRunning'];report={'expectedRunning':required,'services':{},'runtimeReady':False}
        for key,row in self.plans.items():
            actual=self.manager.ready(row['unit'])
            report['services'][key]={'active':actual.get('ActiveState'),'enabled':actual.get('UnitFileState')}
            if key in required and actual.get('ActiveState')!='active':raise RuntimeError('An expected runtime unit is not active')
        if 'core' in required:self.core_ready();report['coreIdentityChecked']=True
        if 'official' in required:
            release=Path(self.progress['candidate']['release'])
            expected=official_catalog(release,load(release))
            result=self.installed('ioalab_services','status','--config',str(self.layout.config/'agents/official.json'),
                role='agents',environment=read_environment(self.layout.config/'agents/official.env'))
            rows=result.get('accounts',[])
            if (not isinstance(rows,list) or any(not isinstance(row,dict) or not isinstance(row.get('account'),str) for row in rows)
                    or sorted(row['account'] for row in rows)!=sorted(expected)
                    or any(row.get('error') or not row.get('runtime',{}).get('platformConnected') or
                    not row.get('runtime',{}).get('host',{}).get('available') for row in rows)):
                raise RuntimeError('Original official SDK/host connectivity is not ready')
            if (any(not isinstance(row.get('services'),list) or any(not isinstance(item,dict) or not isinstance(item.get('profile'),str) for item in row['services']) for row in rows)
                    or any(sorted(item['profile'] for item in row['services'])!=sorted(expected[row['account']]) for row in rows)):
                raise RuntimeError('The original official service status is incomplete')
            services=[item for row in rows for item in row['services']]
            if any(item.get('error') or not item.get('current') for item in services):
                raise RuntimeError('The original official service status is incomplete')
            ready=sum(bool(item['current'].get('indexReady') and item['current'].get('templateReady')
                           and item['current'].get('localExecution',{}).get('executionReady')
                           and item['current'].get('admissionPolicy',{}).get('accepting')) for item in services)
            report['official']={'connectedAccounts':len(rows),'services':len(services),'availableServices':ready}
            if self.request['action'] in ('install','repair-bootstrap','test-reset') and not self.progress.get('recheck') and ready!=sum(map(len,expected.values())):
                raise RuntimeError('Initial official service publication/index is not ready')
        if 'swarm-web' in required:
            env=read_environment(self.layout.config/'agents/swarm-admin.env')
            address=local_url(env['FRONTEND_HOST'],int(env['FRONTEND_PORT']))+'/?user_id=ioa-admin'
            with http_open(address) as answer:
                if b'<html' not in answer.read(100000).lower():raise RuntimeError('The original prebuilt native page is not available')
            report['nativeWeb']=address
        if 'assistant' in required:
            live=self.sdk('runtime.status',{})['data']
            if (live.get('account',{}).get('username')!='admin' or not live.get('platformConnected')
                    or not live.get('host',{}).get('available')):raise RuntimeError('The original administrator SDK/host is not ready')
            self.sdk('assistant.messaging.interfaces',{'account':'admin'})
            report['administrator']={'connected':True,'hostAvailable':True,'currentInterfaceQuerySucceeded':True,
                'meetingConfigured':self.config.notification()[0] is not None}
        if 'trial' in required:
            from .configuration import trial_pair
            trial_pair(self.config.input['platform_config'],self.config.input['trial'])
            value=read_json(Path(self.config.input['trial']['config']))
            request=Request(local_url('127.0.0.1',value.get('port',8766))+'/health',
                            headers={'Host':urlsplit(value['origin']).netloc})
            health=http_json(request)
            if health.get('ready') is not True or health.get('capacity')!=3:
                raise RuntimeError('The complete three-seat trial gateway is not ready')
            report['trial']={'ready':True,'capacity':3,'origin':value['origin']}
        report['runtimeReady']=True
        return report

    def run(self):
        if self.progress.get('stage') not in ('candidate','bootstrap','upgrade','test-reset','verifying','completed'):
            raise RuntimeError('Restore the original fixed installation stage')
        self.saved(outcome='running')
        # A persisted test-reset may have already removed generated role files.
        # Its own clearing/initializing stages own their recreation.
        if self.progress['stage']!='test-reset':
            self.directories();self.config.base();self.permissions()
        if self.progress['stage']=='candidate':
            candidate=self.candidate();current=activation.current(self.layout)
            if self.request['action']=='test-reset':
                resume=self.request.get('resumeTestReset')
                record=({'phase':'resuming','previous':resume['parentCandidate'],
                         'before':self.manager.freeze([row['unit'] for row in self.plans.values()])} if resume else {})
                self.saved(stage='test-reset',candidate=candidate,testReset=record,requiredRunning=list(self.plans))
            elif self.request['action']=='upgrade':
                if not self.metadata.get('initialized') or not current:
                    raise RuntimeError('Finish the original installation before requesting an upgrade')
                self.saved(stage='upgrade',candidate=candidate,upgrade={})
            elif current:
                if not self.metadata.get('initialized') or current!=candidate or self.metadata.get('source')!=self.request['source']:
                    raise RuntimeError('Use upgrade for a changed source or restore the original incomplete install operation')
                required=[key for key,row in self.plans.items() if self.manager.ready(row['unit']).get('ActiveState')=='active']
                self.saved(stage='verifying',candidate=candidate,requiredRunning=required,recheck=True)
            else:
                self.saved(stage='bootstrap',candidate=candidate,requiredRunning=list(self.plans))
        if self.progress['stage']=='bootstrap':
            if self.request['action']!='repair-bootstrap':
                activation.activate(self.layout,self.progress['candidate'],None)
            elif activation.current(self.layout)!=self.progress['candidate']:
                raise RuntimeError('The original repair candidate changed; no activation will be attempted')
            self.bootstrap();self.saved(stage='verifying')
        if self.progress['stage']=='upgrade':
            flow=Upgrade(self.layout,self.job.operation,self.progress['candidate'],self.manager,self.plans,self.contents,
                         self.progress['upgrade'],lambda:self.saved())
            flow.run()
            required=[key for key,row in self.plans.items() if self.progress['upgrade']['before'][row['unit']].get('ActiveState')
                      in ('active','activating','reloading')]
            self.saved(stage='verifying',requiredRunning=required)
        if self.progress['stage']=='test-reset':
            from .test_server import Reset
            Reset(self).run()
            self.saved(stage='verifying',requiredRunning=list(self.plans))
        if self.progress['stage']=='verifying':
            if activation.current(self.layout)!=self.progress['candidate']:
                raise RuntimeError('The current installed set differs from this original operation')
            ready=self.wait(self.readiness)
            if (self.layout.config/'tls/authority.json').exists():
                from .tls import install_renewal
                install_renewal(self.layout)
            self.metadata.update(completedSet=self.progress['candidate']['setId'],source=self.request['source'],
                                 lastReadiness=ready,verifiedAt=datetime.now(timezone.utc).isoformat())
            self.save_metadata();self.saved(stage='completed',outcome='completed')
        if self.progress['stage']=='completed' and self.request.get('resumeTestReset'):
            from .reset_resume import finish
            finish(self.job,self.request)
        return {'status':'completed','setId':self.progress['candidate']['setId'],
                'readiness':self.metadata.get('lastReadiness'),'configuration':self.config.public()}
