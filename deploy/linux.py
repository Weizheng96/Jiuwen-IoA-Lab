"""Bounded Linux account, file and systemd operations; no business or SDK state edits."""
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import uuid


def require_host():
    if sys.platform!='linux' or os.geteuid()!=0 or sys.version_info[:2]!=(3,11):
        raise RuntimeError('Default deployment requires Linux root with CPython 3.11')
    if platform.machine().lower() not in ('x86_64','amd64'):
        raise RuntimeError('The first supported deployment target is Linux x86_64')
    if not Path('/run/systemd/system').is_dir() or not Path('/sys/fs/cgroup/cgroup.controllers').is_file():
        raise RuntimeError('Use systemd with unified cgroups; no fallback service manager is provided')
    for name in ('systemctl','systemd-run','useradd'):
        if not shutil.which(name):raise RuntimeError('Required Linux deployment tool is unavailable: '+name)


def execute(arguments, *, check=True):
    result=subprocess.run(list(map(str,arguments)),stdin=subprocess.DEVNULL,capture_output=True,
        text=True,encoding='utf-8',timeout=30,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
    if check and result.returncode:
        raise RuntimeError('A managed Linux operation failed; retain the original deployment state')
    return result


def regular(path):
    path=Path(path)
    if path.absolute()!=path.resolve() or (path.exists() and not path.is_file()):
        raise ValueError('Use an owned regular deployment file without links')
    return path


def fingerprint(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protected_file(path):
    metadata=Path(path).stat()
    return metadata.st_uid==0 and not metadata.st_mode&0o022


class Accounts:
    def __init__(self, layout, deployment_id, record, save):
        self.layout,self.id,self.record,self.save=layout,deployment_id,record,save
        self.accounts=record.setdefault('accounts',{})
        self.paths=record.setdefault('paths',{})
        if not isinstance(self.accounts,dict) or not isinstance(self.paths,dict):
            raise RuntimeError('Restore the original account and managed-path records')

    def prepare_roots(self):
        for path,mode in ((self.layout.root,0o755),(self.layout.state,0o711),(self.layout.config,0o711)):
            if path.resolve()!=path.absolute() or not path.is_dir() or path.stat().st_uid!=0 or path.stat().st_mode&0o022:
                raise RuntimeError('Deployment parent directories must remain protected and owned by root')
            os.chmod(path,mode)
        private=self.layout.private
        if private.resolve()!=private.absolute() or private.stat().st_uid!=0 or private.stat().st_mode&0o077:
            raise RuntimeError('Deployment private records must remain protected and owned by root')
        cache=self.layout.state/'cache'
        cache.mkdir(mode=0o711,exist_ok=True)
        if cache.resolve()!=cache.absolute() or cache.stat().st_uid!=0 or cache.stat().st_mode&0o022:
            raise RuntimeError('The managed cache parent differs from the original deployment')
        os.chmod(cache,0o711)

    def ensure(self, role):
        import pwd
        import grp
        if role not in ('core','agents'):raise ValueError('Choose the core or agents system role')
        name=self.layout.core_user if role=='core' else self.layout.agents_user
        home=self.layout.state/('core' if role=='core' else 'agents')
        tag='ioalab:'+self.id+':'+role
        entry=self.accounts.get(role)
        try:actual=pwd.getpwnam(name)
        except KeyError:actual=None
        if entry is None:
            if actual is not None:raise RuntimeError('An unrecorded existing system account will not be adopted: '+name)
            entry={'name':name,'home':str(home),'tag':tag,'phase':'creating'}
            self.accounts[role]=entry;self.save()
        if any(entry.get(k)!=v for k,v in {'name':name,'home':str(home),'tag':tag}.items()):
            raise RuntimeError('System account purpose differs from the original deployment')
        if actual is None:
            if entry['phase']!='creating':raise RuntimeError('Restore the original managed system account')
            execute(['useradd','--system','--user-group','--home-dir',home,'--shell','/usr/sbin/nologin','--comment',tag,name])
            actual=pwd.getpwnam(name)
        if (actual.pw_uid==0 or actual.pw_gid==0 or actual.pw_gecos!=tag or actual.pw_dir!=str(home) or
                actual.pw_shell!='/usr/sbin/nologin' or
                (entry.get('uid') is not None and (entry['uid'],entry['gid'])!=(actual.pw_uid,actual.pw_gid))):
            raise RuntimeError('The managed account identity or purpose changed')
        try:group=grp.getgrnam(name)
        except KeyError:raise RuntimeError('Restore the original dedicated service group') from None
        if (group.gr_gid!=actual.pw_gid or set(group.gr_mem)-{name}
                or set(os.getgrouplist(name,actual.pw_gid))!={actual.pw_gid}
                or any(user.pw_uid!=actual.pw_uid and user.pw_gid==actual.pw_gid for user in pwd.getpwall())):
            raise RuntimeError('The managed service group or supplementary access changed; preserve it for review')
        entry.update(phase='ready',uid=actual.pw_uid,gid=actual.pw_gid);self.save()
        return entry

    def leaf(self,path,role):
        if role not in ('core','agents'):raise ValueError('Choose the core or agents system role')
        path=Path(path).absolute();expected=self.layout.state/('core' if role=='core' else 'agents')
        cache=self.layout.state/'cache'/role
        if path.resolve()!=path.absolute() or not (path==expected or path.is_relative_to(expected) or path==cache):
            raise RuntimeError('Ownership handoff must stay in this role\'s recorded state leaves')
        entry=self.accounts[role]
        if entry.get('phase')!='ready':raise RuntimeError('Record the actual role account before ownership handoff')
        return path,entry

    def reserve(self,path,role,*,create=True):
        path,account=self.leaf(path,role);key=str(path)
        expected={'role':role,'uid':account['uid'],'gid':account['gid']}
        entry=self.paths.get(key)
        if entry is None:
            if path.exists() and (not path.is_dir() or any(path.iterdir()) or path.stat().st_uid not in (0,account['uid'])):
                raise RuntimeError('An unknown populated state directory will not be adopted')
            entry={**expected,'phase':'preparing'};self.paths[key]=entry;self.save()
        if (any(entry.get(k)!=v for k,v in expected.items()) or entry.get('phase') not in ('preparing','owned')):
            raise RuntimeError('Managed state path ownership changed')
        if entry['phase']=='owned':
            if not path.is_dir() or (path.stat().st_uid,path.stat().st_gid)!=(account['uid'],account['gid']):
                raise RuntimeError('Restore the original managed state directory and ownership')
        elif create:path.mkdir(mode=0o700,exist_ok=True)
        return entry

    def handoff(self,path,role):
        path,account=self.leaf(path,role);entry=self.paths.get(str(path))
        expected={'role':role,'uid':account['uid'],'gid':account['gid']}
        if entry is None or any(entry.get(k)!=v for k,v in expected.items()):
            raise RuntimeError('Reserve the original role state path before creating or handing it off')
        if entry.get('phase')=='owned':
            self.reserve(path,role,create=False)
            return
        if entry.get('phase')!='preparing' or not path.is_dir():
            raise RuntimeError('Continue the original role state preparation before handoff')
        items=[path,*path.rglob('*')]
        for item in items:
            if item.is_symlink() or item.resolve()!=item.absolute():
                raise RuntimeError('Refuse linked paths during managed state handoff')
            metadata=item.stat()
            if metadata.st_uid not in (0,account['uid']):
                raise RuntimeError('A managed state leaf belongs to an unknown system account')
        for item in reversed(items):
            mode=0o700 if item.is_dir() or item.stat().st_mode&0o111 else 0o600
            os.chown(item,account['uid'],account['gid']);os.chmod(item,mode)
        entry['phase']='owned';self.save()

    def configuration(self,path,role):
        if role not in ('core','agents'):raise ValueError('Choose the core or agents system role')
        path=regular(path);expected=self.layout.config/('core' if role=='core' else 'agents')
        if not path.is_relative_to(expected):raise RuntimeError('Role configuration escaped the configured directory')
        entry=self.accounts[role]
        if expected.resolve()!=expected.absolute() or expected.stat().st_uid!=0 or path.stat().st_uid!=0:
            raise RuntimeError('Role configuration must remain in its root-owned directory')
        os.chown(expected,0,entry['gid']);os.chmod(expected,0o750)
        os.chown(path,0,entry['gid']);os.chmod(path,0o640)


class Systemd:
    FIELDS=('LoadState','ActiveState','SubState','MainPID','ControlGroup','InvocationID','UnitFileState','FragmentPath','DropInPaths','Description')
    def __init__(self, deployment_id, record, save, *, directory=Path('/etc/systemd/system'), cgroups=Path('/sys/fs/cgroup')):
        self.id,self.record,self.save=deployment_id,record,save
        self.directory,self.cgroups=Path(directory),Path(cgroups)

    @staticmethod
    def name(value):
        if not isinstance(value,str) or not re.fullmatch('ioalab-[a-z0-9-]+\\.service',value):
            raise ValueError('Only recorded IoA Lab units may be managed')
        return value

    def inspect(self, name):
        self.name(name)
        answer=execute(['systemctl','show',name,'--no-pager','--property='+','.join(self.FIELDS)],check=False)
        result=dict(line.split('=',1) for line in answer.stdout.splitlines() if '=' in line)
        if not result:
            if answer.returncode:raise RuntimeError('Cannot query the actual system service manager')
            raise RuntimeError('The service manager returned no actual unit state')
        return result

    def owned(self,name):
        self.name(name)
        if name not in self.record:raise RuntimeError('The unit has no recorded deployment ownership')
        path=regular(self.directory/name)
        entry=self.record[name]
        allowed={entry.get('sha256')}
        if entry.get('phase')=='writing':allowed.add(entry.get('previous'))
        if (entry.get('phase') not in ('writing','ready') or not path.is_file()
                or fingerprint(path) not in allowed or not protected_file(path)):
            raise RuntimeError('A managed unit was removed or edited; preserve it for explicit review')
        live=self.inspect(name)
        if live.get('FragmentPath') not in ('',str(path)):
            raise RuntimeError('The actual unit is loaded from another path')
        if live.get('DropInPaths'):
            raise RuntimeError('An unrecorded unit override changes the managed execution context')
        return live

    def install(self,contents):
        planned={}
        for name,text in contents.items():
            self.name(name)
            if not text.startswith('# ioalab-deployment:'+self.id+'\n'):
                raise RuntimeError('Unit payload does not name this deployment')
            path=regular(self.directory/name);sha=hashlib.sha256(text.encode('utf-8')).hexdigest()
            old=self.record.get(name)
            if old is None:
                if path.exists() or self.inspect(name).get('LoadState') not in ('not-found',None):
                    raise RuntimeError('An existing unknown service will not be replaced')
                old={'phase':'writing','sha256':sha,'previous':None};self.record[name]=old;self.save()
            elif old.get('phase')=='ready':
                self.owned(name)
                old.update(phase='writing',previous=old['sha256'],sha256=sha);self.save()
            elif old.get('phase')!='writing' or old.get('sha256')!=sha:
                raise RuntimeError('Continue the original managed unit write')
            if path.exists() and fingerprint(path) not in {old['sha256'],old['previous']}:
                raise RuntimeError('Unit changed during the original write; preserve it')
            planned[name]=(path,text,old)
        for name,(path,text,entry) in planned.items():
            if not path.exists() or fingerprint(path)!=entry['sha256']:
                temporary=path.with_name('.'+path.name+'.'+self.id+'.'+uuid.uuid4().hex)
                descriptor=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as output:
                    output.write(text);output.flush();os.fsync(output.fileno())
                os.chmod(temporary,0o644);os.replace(temporary,path)
        execute(['systemctl','daemon-reload'])
        for _,_,entry in planned.values():
            entry.update(phase='ready',previous=None);self.save()

    def freeze(self,names):
        return {name:self.ready(name) for name in names}

    def ready(self,name):
        live=self.owned(name)
        if self.record[name].get('phase')!='ready':
            raise RuntimeError('Complete the original unit write and reload before starting or freezing it')
        return live

    def request_stop(self,name,frozen):
        live=self.owned(name)
        if self.released(name,frozen):return
        if live.get('InvocationID')!=frozen.get('InvocationID'):
            raise RuntimeError('The managed unit invocation changed; inspect the original stop')
        execute(['systemctl','stop','--no-block',name])

    def empty(self,group):
        if not group:return True
        if not (self.cgroups/'cgroup.controllers').is_file():
            raise RuntimeError('The original unified kernel control-group root is unavailable')
        if not group.startswith('/') or '..' in Path(group).parts:raise RuntimeError('Invalid original control group')
        path=self.cgroups/group.lstrip('/')
        if not path.resolve().is_relative_to(self.cgroups.resolve()):raise RuntimeError('Control group escaped its root')
        if not path.exists():return True
        events=path/'cgroup.events'
        if not events.is_file():raise RuntimeError('The original control group cannot be verified')
        fields=dict(line.split() for line in events.read_text().splitlines())
        if fields.get('populated') not in ('0','1'):
            raise RuntimeError('The kernel did not provide the original group population')
        return fields['populated']=='0'

    def released(self,name,frozen):
        live=self.owned(name)
        if live.get('ActiveState') not in ('inactive','failed'):return False
        if live.get('InvocationID') and live.get('InvocationID')!=frozen.get('InvocationID'):
            raise RuntimeError('Another invocation replaced the original managed unit')
        if live.get('ControlGroup') not in ('',frozen.get('ControlGroup','')):
            raise RuntimeError('The unit control group changed during the original stop')
        return self.empty(frozen.get('ControlGroup','')) and self.empty(live.get('ControlGroup',''))

    def start(self,name):
        self.ready(name);execute(['systemctl','start','--no-block',name])

    def enable(self,name):
        self.ready(name);execute(['systemctl','enable',name])


def readable_tree(root,*,interpreter_links=False):
    root=Path(root)
    plan=[]
    for path in [root,*root.rglob('*')]:
        info=path.lstat()
        if info.st_uid!=0:
            raise RuntimeError('An immutable installed artifact is not owned by root')
        if stat.S_ISLNK(info.st_mode):
            target=path.resolve(strict=True)
            if target.is_relative_to(root):continue
            if (interpreter_links and path.parent==root/'bin' and re.fullmatch(r'python(?:3(?:\.11)?)?',path.name)
                    and target==Path(sys.executable).resolve()):continue
            raise RuntimeError('An installed artifact links outside its permitted prefix')
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or info.st_mode&0o022:
            raise RuntimeError('An installed artifact has unsafe ownership, mode or type')
        if stat.S_ISREG(info.st_mode) and info.st_nlink!=1:
            raise RuntimeError('Do not rewrite permissions through an unrecorded artifact hard link')
        plan.append((path,0o755 if stat.S_ISDIR(info.st_mode) or info.st_mode&0o111 else 0o644))
    return plan


def expose_runtime(layout,candidate):
    from .activation import check_set
    if sys.platform!='linux' or os.geteuid()!=0:
        raise RuntimeError('Runtime artifact exposure requires the Linux deployment root process')
    folder=layout.environments/'sets'/candidate['setId']
    if check_set(layout,folder)!=candidate:
        raise RuntimeError('Only the exact completed candidate set may be exposed to service accounts')
    directories=[layout.root/'releases',layout.environments,layout.environments/'sets',folder]
    files=[folder/'set.json']
    plan=[]
    for directory in directories:
        if directory.is_symlink() or directory.stat().st_uid!=0 or directory.stat().st_mode&0o022:
            raise RuntimeError('The runtime artifact parent must remain protected and owned by root')
        plan.append((directory,0o755))
    for path in files:
        if path.is_symlink() or not path.is_file() or path.stat().st_uid!=0 or path.stat().st_mode&0o022:
            raise RuntimeError('Environment set metadata must remain protected and owned by root')
        plan.append((path,0o644))
    plan.extend(readable_tree(candidate['release']))
    for entry in candidate['units'].values():
        prefix=Path(entry['environment']);group=prefix.parent
        if group.is_symlink() or group.stat().st_uid!=0 or group.stat().st_mode&0o022:
            raise RuntimeError('The original environment installation group changed')
        plan.append((group,0o711))
        plan.extend(readable_tree(prefix,interpreter_links=True))
    # Validate all targets before mutation; do not traverse/chmod any symlink,
    # system interpreter, builder, build log, private config or business state.
    for path,mode in plan:os.chmod(path,mode)
    return {'setId':candidate['setId'],'serviceReadable':True,'artifactPaths':len(plan)}
