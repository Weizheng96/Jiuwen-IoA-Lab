"""One owner-fenced lifecycle for installed Linux components; no business state."""
from contextlib import ExitStack
import hashlib
import json
import subprocess
from pathlib import Path
import time

from . import activation,units
from .files import regular_file, save, writer
from .linux import Systemd
from .release import read_json


class Lifecycle:
    def __init__(self,layout):
        self.layout=layout;self.owner=layout.inspect()['deploymentId']
        self.metadata=read_json(regular_file(layout.private/'installation.json'))
        if self.metadata.get('deploymentId')!=self.owner:
            raise RuntimeError('Deployment ownership changed')
        initial=read_json(regular_file(layout.private/'initial.json'))['input']
        from .configuration import platform
        self.plans=units.plans(layout,administrator=initial['admin_assistant']['enabled'],core=platform(initial['platform_config']),trial=initial.get('trial'))
        if set(self.metadata['units'])!={row['unit'] for row in self.plans.values()}:
            raise RuntimeError('The complete original managed component mapping changed')
        self.manager=Systemd(self.owner,self.metadata['units'],lambda:None)
        self.path=layout.private/'lifecycle.json'

    def leases(self):
        # Reuse actual execution ownership checks, never inspect SDK business DBs.
        from .installation import execution_leases
        stack=ExitStack()
        try:
            trial=self.plans.get('trial')
            if trial:
                config=Path(trial['command'][-1]);value=read_json(regular_file(config))
                control=Path(value['control_directory'])
                if not control.is_absolute():raise RuntimeError('Use an absolute trial control directory')
                receipt=read_json(regular_file(control/'shutdown.json'))
                if (receipt.get('configSha256')!=hashlib.sha256(config.read_bytes()).hexdigest()
                        or receipt.get('allEpochResourcesStopped') is not True):
                    raise RuntimeError('Trial epoch resource shutdown has not been verified')
                from .processes import child_environment
                result=subprocess.run([*trial['command'],'--verify-stopped'],env=child_environment(),
                    capture_output=True,text=True,timeout=180)
                try:observed=json.loads(result.stdout)
                except ValueError:observed={}
                if result.returncode or observed.get('allEpochResourcesStopped') is not True:
                    raise RuntimeError('Current trial epoch resources are not verified stopped')
                stack.enter_context(writer(control/'coordinator.lock'))
            stack.enter_context(execution_leases(self.layout,administrator='assistant' in self.plans))
            core=self.layout.state/'core/.core.lock'
            if not core.is_file():raise RuntimeError('Original core execution lock is unavailable')
            stack.enter_context(writer(core))
            return stack
        except BaseException:stack.close();raise

    def status(self):
        rows={name:self.manager.ready(name) for name in self.metadata['units']}
        value=read_json(self.path) if self.path.exists() else {}
        all_active=bool(rows) and all(row.get('ActiveState')=='active' for row in rows.values())
        phase=value.get('phase','unknown');ready=None
        if all_active and value.get('phase')!='stopped':
            try:ready=self.readiness();phase='running'
            except (RuntimeError,OSError,ValueError):phase='starting'
        elif value.get('phase')=='stopped':
            try:
                if not all(self.manager.released(name,state) for name,state in value['before'].items()):
                    raise RuntimeError('Original groups are not empty')
                with self.leases():pass
            except (RuntimeError,OSError,ValueError,KeyError):phase='unknown'
        elif any(row.get('ActiveState')=='failed' for row in rows.values()):phase='failed'
        return {'status':'completed','deploymentId':self.owner,'phase':phase,'lifecycle':value,
            'components':{name:{key:row.get(key) for key in ('ActiveState','SubState','InvocationID')}
                          for name,row in rows.items()},'runtimeReadinessVerified':ready is not None,'readiness':ready}

    def installer(self):
        from .configs import Configuration
        from .jobs import Job
        from .installation import Installation
        initial=read_json(regular_file(self.layout.private/'initial.json'))
        pointer=read_json(regular_file(self.layout.private/'current-job.json'))
        installer=Installation(Job(self.layout,pointer['operationId']),
            Configuration(self.layout,self.layout.inspect(),initial['input']))
        installer.progress={**installer.progress,'requiredRunning':list(self.plans),'recheck':True}
        return installer

    def readiness(self):return self.installer().readiness()

    def stop(self,wait_seconds=30):
        if not 0<=wait_seconds<=300:raise ValueError('Use a bounded lifecycle observation window')
        with writer(self.layout.private/'launcher.lock'),writer(self.layout.private/'installer.lock'):
            record=read_json(self.path) if self.path.exists() else {}
            if record.get('deploymentId') not in (None,self.owner):raise RuntimeError('Lifecycle owner changed')
            if record.get('phase') not in ('stopping','stopped'):
                record={'deploymentId':self.owner,'phase':'stopping','group':0,
                        'before':self.manager.freeze(list(self.metadata['units']))}
                save(self.path,record)
            deadline=time.monotonic()+wait_seconds
            groups=units.STOP_GROUPS
            while record['group']<len(groups):
                names=[self.plans[key]['unit'] for key in groups[record['group']] if key in self.plans]
                for name in names:self.manager.request_stop(name,record['before'][name])
                if not all(self.manager.released(name,record['before'][name]) for name in names):
                    if time.monotonic()>=deadline:
                        return {'status':'waiting','phase':'stopping','pending':names,'allOwnedGroupsEmpty':False}
                    time.sleep(.2);continue
                record['group']+=1;save(self.path,record)
            if not all(self.manager.released(name,state) for name,state in record['before'].items()):
                raise RuntimeError('An original writer changed after component shutdown')
            with self.leases():
                record['phase']='stopped';save(self.path,record)
            return {'status':'completed','phase':'stopped','allOwnedGroupsEmpty':True,'executionLocksReleased':True}

    def start(self,wait_seconds=30):
        with writer(self.layout.private/'launcher.lock'),writer(self.layout.private/'installer.lock'):
            if not self.metadata.get('initialized') or activation.current(self.layout) is None:
                raise RuntimeError('Complete the original installation before starting components')
            record=read_json(self.path) if self.path.exists() else {}
            if record.get('phase')=='stopping':raise RuntimeError('Finish the original component stop first')
            before=self.manager.freeze(list(self.metadata['units']))
            states={row.get('ActiveState') for row in before.values()}
            if record.get('phase')=='starting':
                if record.get('requested'):
                    raise RuntimeError('Original start result is unbound; stop and inspect that component before retrying')
                for name,invocation in record['started'].items():
                    if before[name].get('InvocationID')!=invocation or before[name].get('ActiveState')=='failed':
                        raise RuntimeError('An original started component changed or failed; explicitly stop before retrying')
            else:
                if states<={'active'}:return self.status()
                if not states<={'inactive','failed'}:
                    raise RuntimeError('A partial running deployment needs an explicit complete stop first')
                if not all(self.manager.released(name,row) for name,row in before.items()):
                    raise RuntimeError('Original component groups are not empty')
                with self.leases():pass
                record={'deploymentId':self.owner,'phase':'starting','started':{},'before':before}
                save(self.path,record)
            deadline=time.monotonic()+wait_seconds
            for key in units.START_ORDER:
                if key not in self.plans:continue
                name=self.plans[key]['unit']
                if name not in record['started']:
                    if not self.manager.released(name,record['before'][name]):
                        raise RuntimeError('An unrequested component appeared during startup')
                    record['requested']=name;save(self.path,record)
                    self.manager.start(name)
                    current=self.manager.ready(name)
                    while (not current.get('InvocationID') or
                           current.get('InvocationID')==record['before'][name].get('InvocationID')):
                        if time.monotonic()>=deadline:
                            raise RuntimeError('Original startup invocation is not verified; preserve the pending request')
                        time.sleep(.1);current=self.manager.ready(name)
                    record['started'][name]=current['InvocationID'];record.pop('requested');save(self.path,record)
                if key=='core':
                    query=self.installer().core_ready
                    while True:
                        live=self.manager.ready(name)
                        if live.get('InvocationID')!=record['started'][name]:
                            raise RuntimeError('The original core invocation changed during readiness')
                        try:query();break
                        except (RuntimeError,OSError,ValueError):
                            if time.monotonic()>=deadline:
                                return {'status':'waiting','phase':'starting','runtimeReadinessVerified':False}
                            time.sleep(.2)
                    if self.metadata.get('administratorPrepared') is False or self.metadata.get('administratorAccount')=='admin':
                        from .administrator import prepare_for_start
                        prepare_for_start(self.installer())
                        self.metadata['administratorPrepared'] = True
            return {'status':'waiting','phase':'starting','runtimeReadinessVerified':False}


def start(layout):return Lifecycle(layout).start()
def stop(layout,wait_seconds=30):return Lifecycle(layout).stop(wait_seconds)
def status(layout):return Lifecycle(layout).status()
