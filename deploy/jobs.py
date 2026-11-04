"""One detached systemd installer owner; CLI observation cannot create a second writer."""
import json
import os
from pathlib import Path
import sys
import uuid

from .environment import writer
from .layout import write_initial
from .linux import Systemd,execute,regular
from .release import digest,read_json,sha
from .units import command,quoted
from .workspace import path_without_links,save


SOURCE_DIRECTORIES=('deploy','tools','src','vendor')
SOURCE_FILES=('vendor/jiuwen/requirements-swarm.lock','vendor/jiuwen/swarm.lock.json')


def source_identity(root):
    root=path_without_links(root)
    result={}
    for name in SOURCE_DIRECTORIES:
        folder=path_without_links(root/name)
        if not folder.is_dir():raise ValueError('Use the complete local deployment source')
        for path in sorted(folder.rglob('*')):
            if set(path.relative_to(folder).parts)&{'__pycache__','.git','node_modules'}:continue
            if name=='deploy' and path.relative_to(folder).parts[0]=='verification':continue
            if path.is_symlink():raise ValueError('Deployment build inputs cannot contain links')
            if path.is_file():result[path.relative_to(root).as_posix()]=sha(path)
    for name in SOURCE_FILES:
        path=path_without_links(root/name)
        if not path.is_file():raise ValueError('Use the complete local deployment source')
        result[name]=sha(path)
    return {'root':str(root),'digest':digest(result),'files':len(result)}


class Job:
    def __init__(self,layout,operation):
        if not isinstance(operation,str) or uuid.UUID(operation).hex!=operation:
            raise ValueError('Use the original installation operation UUID')
        self.layout,self.operation=layout,operation
        self.owner=layout.inspect()['deploymentId']
        self.folder=path_without_links(layout.private/'jobs'/operation)
        self.system=Systemd(self.owner,{},lambda:None)

    def create(self,action,source,input_digest):
        if action not in ('install','upgrade'):raise ValueError('Use install or upgrade')
        self.folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        value={'format':'ioalab.installation-job/1','deploymentId':self.owner,'operationId':self.operation,
               'action':action,'source':source,'inputDigest':input_digest}
        write_initial(self.folder/'request.json',(json.dumps(value,sort_keys=True)+'\n').encode())
        return value

    def request(self):
        value=read_json(regular(self.folder/'request.json'))
        if (value.get('format')!='ioalab.installation-job/1' or value.get('deploymentId')!=self.owner
                or value.get('operationId')!=self.operation):
            raise RuntimeError('Restore the original installation job ownership')
        return value

    def planned(self,number):
        unit='ioalab-install-'+self.operation+'-'+str(number)+'.service'
        return {'attempt':number,'unit':unit,'description':'Jiuwen IoA installer '+self.owner+'/'+self.operation+'/'+str(number)}

    def attempt(self):
        path=regular(self.folder/'attempt.json')
        if not path.exists():return None
        value=read_json(path)
        if type(value.get('attempt')) is not int or value['attempt']<=0 or value!=self.planned(value['attempt']):
            raise RuntimeError('Restore the original installation attempt')
        return value

    def runtime_path(self,attempt):return self.folder/('attempt-'+str(attempt['attempt'])+'.runtime.json')

    def inspect(self,attempt):
        live=self.system.inspect(attempt['unit'])
        if live.get('LoadState')=='not-found':return live
        if (live.get('Description')!=attempt['description'] or live.get('DropInPaths')
                or live.get('FragmentPath')!='/run/systemd/transient/'+attempt['unit']):
            raise RuntimeError('The actual installer unit is not this original transient job')
        return live

    def observe(self):
        request=self.request();attempt=self.attempt()
        result={'operationId':self.operation,'action':request['action'],'status':'not_started'}
        if attempt is None:return result
        live=self.inspect(attempt);path=regular(self.runtime_path(attempt))
        birth=read_json(path) if path.exists() else None
        if birth:
            if (birth.get('attempt')!=attempt or birth.get('deploymentId')!=self.owner
                    or birth.get('operationId')!=self.operation or not birth.get('InvocationID') or not birth.get('ControlGroup')):
                raise RuntimeError('Restore the original installer invocation record')
            if live.get('InvocationID') and live['InvocationID']!=birth['InvocationID']:
                raise RuntimeError('Another invocation replaced the original installation task')
            if not self.system.empty(birth['ControlGroup']):result['status']='waiting'
        if live.get('ActiveState') in ('active','activating','reloading','deactivating') or not self.system.empty(live.get('ControlGroup','')):
            result['status']='waiting'
        if result['status']=='not_started':result['status']='stopped' if birth else 'unknown'
        progress=self.folder/'progress.json'
        if progress.exists():
            value=read_json(regular(progress))
            result['stage']=value.get('stage');result['outcome']=value.get('outcome')
            if result['status']=='stopped' and value.get('outcome')=='completed':result['status']='completed'
        result.update(unit=attempt['unit'],attempt=attempt['attempt'])
        return result

    def submit(self):
        # A separate launch lock closes the plan->systemd-run race. The worker
        # takes installer.lock, not this short launcher lock.
        with writer(self.layout.private/'launcher.lock'):
            observed=self.observe();previous=self.attempt()
            if observed['status'] in ('waiting','completed'):return observed
            if observed['status']=='unknown':raise RuntimeError('Installation launch outcome is unknown; preserve and inspect the original job')
            with writer(self.layout.private/'installer.lock'):pass
            request=self.request()
            if source_identity(request['source']['root'])!=request['source']:
                raise RuntimeError('Source changed; continue the original recorded source before resuming')
            quoted(request['source']['root'])
            quoted(self.folder/'installation.log')
            number=previous['attempt']+1 if previous else 1;attempt=self.planned(number)
            save(self.folder/'attempt.json',attempt)
            argv=[sys.executable,'-X','utf8','-B','-m','deploy','_worker',
                  '--state',str(self.layout.state),'--operation',self.operation,'--attempt',str(number)]
            command(argv)  # Reject service-manager expansions, even though no shell is involved.
            execute(['systemd-run','--unit='+attempt['unit'],'--description='+attempt['description'],
                '--working-directory='+request['source']['root'],'--property=Type=exec',
                '--property=UMask=0077','--property=KillMode=mixed','--property=SendSIGKILL=no',
                '--property=TimeoutStartSec=infinity','--property=TimeoutStopSec=infinity',
                '--property=Restart=no','--property=StandardOutput=append:'+str(self.folder/'installation.log'),
                '--property=StandardError=inherit','--setenv=PYTHONUTF8=1','--setenv=PYTHONNOUSERSITE=1',*argv])
            return self.observe()

    def bind_worker(self,number):
        attempt=self.attempt()
        if not attempt or number!=attempt['attempt']:raise RuntimeError('This worker is not the recorded installation attempt')
        live=self.inspect(attempt)
        invocation=os.environ.get('INVOCATION_ID')
        memberships=Path('/proc/self/cgroup').read_text().splitlines()
        unified=[line[3:] for line in memberships if line.startswith('0::')]
        if (live.get('MainPID')!=str(os.getpid()) or not invocation or live.get('InvocationID')!=invocation
                or unified!=[live.get('ControlGroup')] or not live.get('ControlGroup')):
            raise RuntimeError('The worker does not own the actual original service-manager execution context')
        birth={'deploymentId':self.owner,'operationId':self.operation,'attempt':attempt,
               'InvocationID':invocation,'ControlGroup':live['ControlGroup']}
        write_initial(self.runtime_path(attempt),(json.dumps(birth,sort_keys=True)+'\n').encode())
        request=self.request()
        if source_identity(request['source']['root'])!=request['source']:
            raise RuntimeError('Installation source changed before the actual worker started')
        return request
