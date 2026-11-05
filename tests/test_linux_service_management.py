"""Bounded manager fault tests. Real systemd/cgroup deployment requires Linux verification."""
import hashlib
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from deploy import linux, units


DEPLOYMENT = 'a' * 32


def test_linux_source_scope_includes_ownership_regressions_only_for_its_owner():
    from tools.check_scope import select_tests
    selected=select_tests(['deploy/linux.py'])['tests']
    assert 'tests/test_linux_service_management.py' in selected
    assert 'tests/test_deployment_layout.py' in selected
    assert 'tests/test_windows_local.py' not in selected
    assert 'tests/test_linux_service_management.py' not in select_tests(['deploy/windows_local.py'])['tests']


@pytest.fixture
def account_database(monkeypatch):
    rows={};calls=[]
    def lookup(name):
        if name not in rows:raise KeyError(name)
        return rows[name]
    def execute(argv,**kwargs):
        assert argv[:3]==['useradd','--system','--user-group']
        name=argv[-1];tag=argv[argv.index('--comment')+1]
        # shadow's useradd rejects these passwd/GECOS delimiters.
        assert not any(char in tag for char in (':','\n','\r','\x00'))
        identity=10001+len(rows)
        rows[name]=SimpleNamespace(pw_name=name,pw_uid=identity,pw_gid=identity,pw_gecos=tag,
            pw_dir=str(argv[argv.index('--home-dir')+1]),pw_shell='/usr/sbin/nologin')
        calls.append(argv)
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lookup,getpwall=lambda:list(rows.values())))
    monkeypatch.setitem(sys.modules,'grp',SimpleNamespace(getgrnam=lambda name:SimpleNamespace(gr_gid=lookup(name).pw_gid,gr_mem=[])))
    monkeypatch.setattr(linux.os,'getgrouplist',lambda name,gid:[gid],raising=False)
    monkeypatch.setattr(linux,'execute',execute)
    return rows,calls


def test_accounts_use_valid_gecos_and_preserve_exact_ownership(tmp_path,account_database):
    rows,calls=account_database;record={};saves=[]
    accounts=linux.Accounts(layout(tmp_path),DEPLOYMENT,record,lambda:saves.append(True))
    for role in ('core','agents'):
        value=accounts.ensure(role)
        assert value['phase']=='ready' and value['tag']=='ioalab-'+DEPLOYMENT+'-'+role
        assert accounts.ensure(role)==value
    assert len(calls)==2 and saves
    rows['ioalab-core'].pw_gecos='another-deployment'
    with pytest.raises(RuntimeError,match='identity or purpose'):accounts.ensure('core')
    assert len(calls)==2


def test_accounts_do_not_adopt_unrecorded_system_user(tmp_path,account_database):
    rows,calls=account_database
    rows['ioalab-core']=SimpleNamespace(pw_uid=10001)
    accounts=linux.Accounts(layout(tmp_path),DEPLOYMENT,{},lambda:None)
    with pytest.raises(RuntimeError,match='unrecorded'):accounts.ensure('core')
    assert calls==[] and accounts.accounts=={}


@pytest.mark.parametrize('identity',['ioalab:invalid','a'*31+'\n','A'*32])
def test_invalid_account_owner_is_rejected_before_recording(tmp_path,identity):
    record={}
    with pytest.raises(ValueError,match='canonical deployment UUID'):
        linux.Accounts(layout(tmp_path),identity,record,lambda:pytest.fail('Invalid ownership must not be saved'))
    assert record=={}


def layout(tmp_path):
    return SimpleNamespace(root=tmp_path/'runtime',state=tmp_path/'state',config=tmp_path/'config',
                           environments=tmp_path/'runtime/envs',core_user='ioalab-core',agents_user='ioalab-agents')


def test_installed_commands_are_role_scoped_and_drain_without_kill():
    root=SimpleNamespace(root=PurePosixPath('/opt/ioalab-runtime'),state=PurePosixPath('/var/lib/ioalab'),
        config=PurePosixPath('/etc/ioalab'),environments=PurePosixPath('/opt/ioalab-runtime/envs'),
        core_user='ioalab-core',agents_user='ioalab-agents')
    plans=units.plans(root,administrator=True,core={'bind':'127.0.0.1','port':8750,'public_url':'https://ioa.example'})
    rendered=units.render(root,DEPLOYMENT,plans)
    assert len(rendered)==6
    assert len(plans['official']['pre'])==9
    for key,row in plans.items():
        value=rendered[row['unit']]
        assert 'SendSIGKILL=no' in value
        assert ('KillMode=control-group' if key.startswith('swarm-') else 'KillMode=mixed') in value
        assert 'TimeoutStopSec=infinity' in value and 'UMask=0077' in value
        assert 'PYTHONPATH' not in value and 'install.sh' not in value
        assert row['command'][0].startswith('/opt/ioalab-runtime/envs/current/')
        assert row['command'][1:4]==['-I','-X','utf8']
        assert f'User={root.core_user if key=="core" else root.agents_user}' in value
    assert '--config' in plans['assistant']['pre'][1]
    assert 'ioalab.readiness' in plans['assistant']['pre'][0]
    assert 'WorkingDirectory=/var/lib/ioalab/core\n' in rendered['ioalab-core.service']
    assert 'EnvironmentFile=' not in rendered['ioalab-core.service']
    assert 'EnvironmentFile=/etc/ioalab/agents/official.env\n' in rendered['ioalab-official.service']
    assert plans['core']['command'][-4:]==['ioalab','--config','/etc/ioalab/core/platform.json','serve']
    for key,row in plans.items():
        if key=='core':continue
        assert row['pre'][0][:5]==['/opt/ioalab-runtime/envs/current/core/bin/python','-I','-X','utf8','-m']
        assert row['pre'][0][5:] == ['ioalab.readiness','--url','http://127.0.0.1:8750',
            '--expected-public-url','https://ioa.example','--timeout','180','--request-timeout','3']
    assert set(units.plans(root,administrator=False,core={'bind':'127.0.0.1','port':8750,'public_url':'https://ioa.example'}))=={'core','official'}


@pytest.mark.parametrize('argument',['/tmp/a\nb','/tmp/a%b','/tmp/$HOME','C:\\tool\\python'])
def test_unit_arguments_cannot_expand_or_inject(argument):
    with pytest.raises(ValueError):units.command(['/usr/bin/python3',argument])


@pytest.mark.parametrize('path',['relative','/tmp/space path','/tmp/line\nbreak','/tmp/$HOME','/tmp/%n','/tmp/../other','"/tmp/quoted"'])
def test_path_directives_reject_ambiguous_or_expandable_values(path):
    with pytest.raises(ValueError):units.directive_path(path)


class FakeManager:
    def __init__(self):
        self.units={};self.calls=[];self.fail_reload=False

    def __call__(self,argv,*,check=True):
        self.calls.append(argv)
        if argv[:2]==['systemctl','show']:
            state=self.units.get(argv[2],{'LoadState':'not-found','ActiveState':'inactive'})
            return SimpleNamespace(returncode=0,stdout='\n'.join(k+'='+v for k,v in state.items()))
        if argv==['systemctl','daemon-reload'] and self.fail_reload:
            raise RuntimeError('injected daemon-reload failure')
        return SimpleNamespace(returncode=0,stdout='')


def manager(tmp_path,monkeypatch):
    fake=FakeManager();monkeypatch.setattr(linux,'execute',fake)
    # The test runner need not be root; this is a manager protocol/fault test.
    # Real root ownership and mode verification belongs to the Linux install probe.
    monkeypatch.setattr(linux,'protected_file',lambda path:True)
    directory=tmp_path/'units';directory.mkdir()
    cgroups=tmp_path/'cgroups';cgroups.mkdir();(cgroups/'cgroup.controllers').write_text('cpu memory')
    record={};saves=[]
    system=linux.Systemd(DEPLOYMENT,record,lambda:saves.append(repr(record)),directory=directory,cgroups=cgroups)
    return system,fake,record,saves


def test_failed_reload_remains_writing_and_resumes_original_payload(tmp_path,monkeypatch):
    system,fake,record,saves=manager(tmp_path,monkeypatch)
    name='ioalab-core.service';content='# ioalab-deployment:'+DEPLOYMENT+'\n[Service]\nExecStart=/bin/true\n'
    fake.fail_reload=True
    with pytest.raises(RuntimeError,match='daemon-reload'):system.install({name:content})
    assert record[name]['phase']=='writing'
    assert (system.directory/name).read_text()==content
    with pytest.raises(RuntimeError,match='original'):system.install({name:content+'# changed\n'})
    fake.fail_reload=False;system.install({name:content})
    assert record[name]['phase']=='ready' and record[name]['previous'] is None
    assert record[name]['sha256']==hashlib.sha256(content.encode()).hexdigest()
    assert saves


def test_unknown_service_file_is_preserved(tmp_path,monkeypatch):
    system,fake,record,_=manager(tmp_path,monkeypatch)
    name='ioalab-core.service';path=system.directory/name;path.write_text('operator service')
    with pytest.raises(RuntimeError,match='unknown'):system.install({name:'# ioalab-deployment:'+DEPLOYMENT+'\n'})
    assert path.read_text()=='operator service' and record=={}


def frozen_manager(tmp_path,monkeypatch):
    system,fake,record,saves=manager(tmp_path,monkeypatch)
    name='ioalab-core.service';content='# ioalab-deployment:'+DEPLOYMENT+'\n'
    system.install({name:content})
    state={'LoadState':'loaded','ActiveState':'active','InvocationID':'original',
        'ControlGroup':'/system.slice/ioalab-core.service','FragmentPath':str(system.directory/name),'DropInPaths':''}
    fake.units[name]=state
    group=system.cgroups/'system.slice/ioalab-core.service';group.mkdir(parents=True)
    (group/'cgroup.events').write_text('populated 1\nfrozen 0\n')
    return system,fake,name,state,group


def test_stop_checks_original_invocation_and_all_group_processes(tmp_path,monkeypatch):
    system,fake,name,state,group=frozen_manager(tmp_path,monkeypatch)
    frozen=dict(state);system.request_stop(name,frozen)
    assert ['systemctl','stop','--no-block',name] in fake.calls
    state.update(ActiveState='inactive',MainPID='0')
    assert not system.released(name,frozen), 'A dead leader does not release its children'
    (group/'cgroup.events').write_text('populated 0\n')
    assert system.released(name,frozen)
    state.update(ActiveState='active',InvocationID='replacement')
    stops=sum(row[:2]==['systemctl','stop'] for row in fake.calls)
    with pytest.raises(RuntimeError,match='invocation'):system.request_stop(name,frozen)
    assert sum(row[:2]==['systemctl','stop'] for row in fake.calls)==stops


def test_kernel_absence_cannot_be_mistaken_for_empty_group(tmp_path,monkeypatch):
    system,_,_,_=manager(tmp_path,monkeypatch)
    (system.cgroups/'cgroup.controllers').unlink()
    with pytest.raises(RuntimeError,match='unavailable'):system.empty('/original.service')


def test_unrecorded_override_is_not_a_managed_execution_context(tmp_path,monkeypatch):
    system,fake,name,state,group=frozen_manager(tmp_path,monkeypatch)
    state['DropInPaths']='/etc/systemd/system/ioalab-core.service.d/operator.conf'
    with pytest.raises(RuntimeError,match='override'):system.request_stop(name,dict(state))


def test_owned_leaf_is_verified_without_recursive_permission_reset(tmp_path,monkeypatch):
    root=layout(tmp_path);leaf=root.state/'agents';leaf.mkdir(parents=True)
    child=leaf/'operator-edit';child.write_text('preserve')
    record={'accounts':{'agents':{'phase':'ready','uid':leaf.stat().st_uid,'gid':leaf.stat().st_gid}},
            'paths':{str(leaf.absolute()):{'phase':'owned','role':'agents','uid':leaf.stat().st_uid,'gid':leaf.stat().st_gid}}}
    accounts=linux.Accounts(root,DEPLOYMENT,record,lambda:None)
    def forbidden(*args):raise AssertionError('An owned business directory must not be recursively rewritten')
    monkeypatch.setattr(linux.os,'chmod',forbidden)
    monkeypatch.setattr(linux.os,'chown',forbidden,raising=False)
    accounts.handoff(leaf,'agents')
    assert child.read_text()=='preserve'


@pytest.mark.parametrize('unknown_owner',[None,'core-owned','directory','file'])
def test_only_core_configuration_leaf_is_writable_not_parent_or_agents(tmp_path,monkeypatch,unknown_owner):
    root=layout(tmp_path);directory=root.config/'core';directory.mkdir(parents=True)
    path=directory/'platform.json';path.write_text('{}')
    record={'accounts':{'core':{'phase':'ready','uid':1201,'gid':1202}}}
    accounts=linux.Accounts(root,DEPLOYMENT,record,lambda:None)
    ownership=[];permissions=[]
    original_stat=Path.stat
    known={directory:0,path:1201}
    if unknown_owner=='core-owned':known[directory]=1201
    if unknown_owner in ('directory','file'):known[directory if unknown_owner=='directory' else path]=1001
    def owned_stat(subject,*args,**kwargs):
        value=original_stat(subject,*args,**kwargs)
        if subject not in known:return value
        return SimpleNamespace(st_uid=known[subject],st_mode=value.st_mode)
    monkeypatch.setattr(Path,'stat',owned_stat)
    monkeypatch.setattr(linux.os,'chown',lambda p,uid,gid:ownership.append((p,uid,gid)),raising=False)
    monkeypatch.setattr(linux.os,'chmod',lambda p,mode:permissions.append((p,mode)))
    if unknown_owner in ('directory','file'):
        with pytest.raises(RuntimeError,match='root-owned'):accounts.configuration(path,'core')
        assert ownership==[] and permissions==[]
        return
    accounts.configuration(path,'core')
    assert ownership==[(directory,1201,1202),(path,1201,1202)]
    assert permissions==[(directory,0o700),(path,0o600)]
    extra=directory/'unrelated.json';extra.write_text('{}')
    known[extra]=0
    with pytest.raises(RuntimeError,match='unique core'):accounts.configuration(extra,'core')
    assert len(ownership)==2 and root.config not in [item[0] for item in ownership]


def test_unknown_populated_leaf_is_not_adopted(tmp_path):
    root=layout(tmp_path);leaf=root.state/'agents';leaf.mkdir(parents=True);(leaf/'database').write_text('existing')
    record={'accounts':{'agents':{'phase':'ready','uid':leaf.stat().st_uid,'gid':leaf.stat().st_gid}}}
    accounts=linux.Accounts(root,DEPLOYMENT,record,lambda:None)
    with pytest.raises(RuntimeError,match='unknown'):accounts.reserve(leaf,'agents')
    assert accounts.paths=={}


def test_incomplete_unit_write_can_be_drained_but_not_started(tmp_path,monkeypatch):
    system,fake,name,state,group=frozen_manager(tmp_path,monkeypatch)
    original=system.record[name]['sha256'];desired=hashlib.sha256(b'new controlled unit').hexdigest()
    system.record[name].update(phase='writing',previous=original,sha256=desired)
    state['ActiveState']='inactive';(group/'cgroup.events').write_text('populated 0\n')
    assert system.released(name,dict(state))
    with pytest.raises(RuntimeError,match='write and reload'):system.start(name)
    with pytest.raises(RuntimeError,match='write and reload'):system.freeze([name])
    (system.directory/name).write_bytes(b'new controlled unit')
    with pytest.raises(RuntimeError,match='write and reload'):system.start(name)
    system.record[name].update(phase='ready',previous=None)
    system.start(name)
    assert ['systemctl','start','--no-block',name] in fake.calls


@pytest.mark.parametrize('change',['same_name_wrong_gid','extra_member','extra_group','shared_primary'])
def test_system_role_requires_its_original_dedicated_group(tmp_path,monkeypatch,change):
    import sys
    root=layout(tmp_path);name='ioalab-agents'
    user=SimpleNamespace(pw_uid=1201,pw_gid=1202,pw_dir=str(root.state/'agents'),pw_shell='/usr/sbin/nologin',
                         pw_gecos='ioalab-'+DEPLOYMENT+'-agents')
    group=SimpleNamespace(gr_gid=1202,gr_mem=[]);directory=[user];supplementary=[1202]
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda _:user,getpwall=lambda:directory))
    monkeypatch.setitem(sys.modules,'grp',SimpleNamespace(getgrnam=lambda _:group))
    monkeypatch.setattr(linux.os,'getgrouplist',lambda *args:supplementary,raising=False)
    record={'accounts':{'agents':{'name':name,'home':user.pw_dir,'tag':user.pw_gecos,'phase':'ready','uid':1201,'gid':1202}}}
    accounts=linux.Accounts(root,DEPLOYMENT,record,lambda:None)
    assert accounts.ensure('agents')['gid']==1202
    if change=='same_name_wrong_gid':group.gr_gid=999
    elif change=='extra_member':group.gr_mem.append('unrelated-user')
    elif change=='extra_group':supplementary.append(999)
    else:directory.append(SimpleNamespace(pw_uid=1401,pw_gid=1202))
    with pytest.raises(RuntimeError,match='group'):accounts.ensure('agents')
