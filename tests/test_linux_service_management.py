"""Bounded manager fault tests. Real systemd/cgroup deployment requires Linux verification."""
import hashlib
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from deploy import linux, units


DEPLOYMENT = 'a' * 32


def layout(tmp_path):
    return SimpleNamespace(root=tmp_path/'runtime',state=tmp_path/'state',config=tmp_path/'config',
                           environments=tmp_path/'runtime/envs',core_user='ioalab-core',agents_user='ioalab-agents')


def test_installed_commands_are_role_scoped_and_drain_without_kill():
    root=SimpleNamespace(root=PurePosixPath('/opt/ioalab-runtime'),state=PurePosixPath('/var/lib/ioalab'),
        config=PurePosixPath('/etc/ioalab'),environments=PurePosixPath('/opt/ioalab-runtime/envs'),
        core_user='ioalab-core',agents_user='ioalab-agents')
    plans=units.plans(root,administrator=True)
    rendered=units.render(root,DEPLOYMENT,plans)
    assert len(rendered)==6
    assert len(plans['official']['pre'])==8
    for key,row in plans.items():
        value=rendered[row['unit']]
        assert 'SendSIGKILL=no' in value
        assert ('KillMode=control-group' if key.startswith('swarm-') else 'KillMode=mixed') in value
        assert 'TimeoutStopSec=infinity' in value and 'UMask=0077' in value
        assert 'PYTHONPATH' not in value and 'install.sh' not in value
        assert row['command'][0].startswith('/opt/ioalab-runtime/envs/current/')
        assert row['command'][1:4]==['-I','-X','utf8']
        assert f'User={root.core_user if key=="core" else root.agents_user}' in value
    assert '--config' in plans['assistant']['pre'][0]
    assert set(units.plans(root,administrator=False))=={'core','official'}


@pytest.mark.parametrize('argument',['/tmp/a\nb','/tmp/a%b','/tmp/$HOME','C:\\tool\\python'])
def test_unit_arguments_cannot_expand_or_inject(argument):
    with pytest.raises(ValueError):units.command(['/usr/bin/python3',argument])


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
                         pw_gecos='ioalab:'+DEPLOYMENT+':agents')
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
