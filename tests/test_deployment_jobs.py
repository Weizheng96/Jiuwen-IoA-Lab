"""Installer owner faults with a simulated manager and real exclusive OS locks."""
import json
from types import SimpleNamespace
import uuid

import pytest

from deploy.environment import writer
from deploy.jobs import Job,source_identity
from deploy.layout import Layout
from deploy.linux import Systemd
import deploy.jobs as jobs
import deploy.linux as linux


@pytest.fixture
def setup(tmp_path,monkeypatch):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');identity=layout.claim()
    source=tmp_path/'source';source.mkdir()
    for name in ('deploy','tools','src','vendor'):
        (source/name).mkdir();(source/name/'input.txt').write_text(name)
    for name in jobs.SOURCE_FILES:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
    cgroups=tmp_path/'groups';cgroups.mkdir();(cgroups/'cgroup.controllers').write_text('cpu memory')
    states={};launches=[]
    def execute(argv,**kwargs):
        if argv[:2]==['systemctl','show']:
            value=states.get(argv[2],{'LoadState':'not-found','ActiveState':'inactive','ControlGroup':''})
            return SimpleNamespace(returncode=0,stdout='\n'.join(k+'='+v for k,v in value.items()))
        assert argv[0]=='systemd-run';launches.append(argv)
        unit=next(v.split('=',1)[1] for v in argv if v.startswith('--unit='))
        description=next(v.split('=',1)[1] for v in argv if v.startswith('--description='))
        group='/system.slice/'+unit
        states[unit]={'LoadState':'loaded','ActiveState':'active','InvocationID':'original','MainPID':'24222',
            'ControlGroup':group,'Description':description,'FragmentPath':'/run/systemd/transient/'+unit,'DropInPaths':''}
        path=cgroups/group.lstrip('/');path.mkdir(parents=True);(path/'cgroup.events').write_text('populated 1\n')
        return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(linux,'execute',execute);monkeypatch.setattr(jobs,'execute',execute)
    monkeypatch.setattr(jobs,'sys',SimpleNamespace(executable='/usr/bin/python3.11'))
    # Fixture state paths are Windows paths there. Argument grammar itself is
    # covered by POSIX rendering tests; this case tests the owner protocol.
    if __import__('os').name=='nt':
        from deploy import units
        monkeypatch.setattr(jobs,'command',lambda argv:units.command([str(value).replace(chr(92),'/') for value in argv]))
        monkeypatch.setattr(jobs,'quoted',lambda value:units.quoted(str(value).replace(chr(92),'/')))

    job=Job(layout,uuid.uuid4().hex);job.system=Systemd(identity['deploymentId'],{},lambda:None,cgroups=cgroups)
    job.create('install',source_identity(source),'a'*64)
    return job,source,cgroups,states,launches


def record_birth(job,state):
    value={'deploymentId':job.owner,'operationId':job.operation,'attempt':job.attempt(),
           'InvocationID':state['InvocationID'],'ControlGroup':state['ControlGroup']}
    job.runtime_path(job.attempt()).write_text(json.dumps(value))


def test_repeated_submit_observes_same_live_worker_and_detached_service(setup):
    job,_,_,_,launches=setup;first=job.submit()
    assert first['status']=='waiting' and job.submit()==first and len(launches)==1
    argv=launches[0]
    assert '--property=Type=exec' in argv and '--property=UMask=0077' in argv
    assert not {'--scope','--wait','--pty','--pipe'}&set(argv)
    assert '--property=SendSIGKILL=no' in argv and '--property=Restart=no' in argv


def test_completed_json_and_dead_leader_do_not_hide_surviving_children(setup):
    job,_,groups,states,launches=setup;job.submit();state=states[job.attempt()['unit']];record_birth(job,state)
    state.update(ActiveState='inactive',MainPID='0')
    (job.folder/'progress.json').write_text(json.dumps({'stage':'completed','outcome':'completed'}))
    assert job.submit()['status']=='waiting' and len(launches)==1
    (groups/state['ControlGroup'].lstrip('/')/'cgroup.events').write_text('populated 0\n')
    assert job.submit()['status']=='completed' and len(launches)==1


def test_replacement_invocation_and_unverified_launch_do_not_start_workers(setup):
    job,_,_,states,launches=setup;job.submit();state=states[job.attempt()['unit']];record_birth(job,state)
    state['InvocationID']='replacement'
    with pytest.raises(RuntimeError,match='replaced'):job.submit()
    assert len(launches)==1


def test_unknown_launch_is_retained_even_if_unit_is_not_found(setup):
    job,_,_,_,launches=setup;jobs.save(job.folder/'attempt.json',job.planned(1))
    with pytest.raises(RuntimeError,match='unknown'):job.submit()
    assert not launches and job.attempt()['attempt']==1


def test_actual_os_lock_and_original_source_block_second_writer(setup):
    job,source,_,_,launches=setup
    with writer(job.layout.private/'installer.lock'):
        with pytest.raises(RuntimeError,match='writer lock'):job.submit()
    assert not launches and job.attempt() is None
    (source/'src/input.txt').write_text('changed source')
    with pytest.raises(RuntimeError,match='Source changed'):job.submit()
    assert not launches and job.attempt() is None


def test_same_name_nonoriginal_service_is_not_managed(setup):
    job,_,_,states,launches=setup;job.submit();states[job.attempt()['unit']]['Description']='another purpose'
    with pytest.raises(RuntimeError,match='original transient'):job.submit()
    assert len(launches)==1


@pytest.mark.parametrize('name',jobs.SOURCE_FILES)
def test_changed_or_missing_upstream_build_pin_blocks_original_worker(setup,name):
    job,source,_,_,launches=setup
    original=source_identity(source)
    path=source/name;path.write_text('changed upstream dependency pin')
    assert source_identity(source)!=original
    with pytest.raises(RuntimeError,match='Source changed'):job.submit()
    assert not launches and job.attempt() is None
    path.unlink()
    with pytest.raises(ValueError,match='complete local deployment source'):source_identity(source)


@pytest.mark.skipif(__import__('os').name!='posix',reason='Actual directory links require the Linux deployment host')
def test_original_build_input_directory_cannot_be_retargeted(setup):
    _,source,_,_,_=setup
    folder=source/'deploy';(folder/'input.txt').unlink();folder.rmdir()
    target=source.parent/'another-source';target.mkdir();(target/'input.txt').write_text('deploy')
    folder.symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError,match='must not contain links'):source_identity(source)
