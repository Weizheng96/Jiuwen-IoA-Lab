"""Image assembly reuses original Swarm pins and fails before substitute installs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


def module():
    spec=importlib.util.spec_from_file_location('trial_swarm_install',Path(__file__).parents[1]/'deploy/trial/install_swarm_wheels.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def inputs(tmp_path):
    vendor=tmp_path/'vendor';(vendor/'jiuwen').mkdir(parents=True);(vendor/'wheels').mkdir()
    wheel=vendor/'wheels/original-1-py3-none-any.whl';wheel.write_bytes(b'original bundle')
    (vendor/'SOURCES.json').write_text(json.dumps({'components':[{'wheelInfo':{'file':'wheels/'+wheel.name,
        'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()}}]}))
    (vendor/'jiuwen/requirements-swarm.lock').write_text('ordinary==1\n./vendor/wheels/'+wheel.name+'\n')
    python=tmp_path/'env/bin/python';python.parent.mkdir(parents=True);(python.parent/'jiuwenswarm-process').touch()
    constraints=tmp_path/'swarm.lock';constraints.write_text('ordinary==1\n')
    return wheel,python,constraints


def test_actual_original_requirement_wheels_all_match_manifest():
    result=module().plan(Path(__file__).parents[1])
    assert len(result)==4 and all(p.name.endswith('.whl') for p in result)
    docker=(Path(__file__).parents[1]/'deploy/trial/Dockerfile').read_text()
    assert 'install_swarm_wheels.py --root /opt/trial-build' in docker and '--constraints /opt/locks/swarm.lock' in docker


def test_offline_install_uses_existing_constraints_and_checks_entry(tmp_path):
    wheel,python,constraints=inputs(tmp_path);calls=[]
    module().install(tmp_path,python,constraints,run=lambda argv,**kw:calls.append((argv,kw)))
    assert calls[0][0][-1]==str(wheel) and '--no-index' in calls[0][0] and '--no-deps' in calls[0][0]
    assert calls[0][0][calls[0][0].index('-c')+1]==str(constraints)
    assert calls[1][0][-2:]==['pip','check'] and all(kw=={'check':True} for _,kw in calls)
    (python.parent/'jiuwenswarm-process').unlink()
    with pytest.raises(ValueError,match='entry point'):module().install(tmp_path,python,constraints,run=lambda *a,**kw:None)


@pytest.mark.parametrize('failure',['missing','digest'])
def test_bad_original_wheel_never_attempts_pip(tmp_path,failure):
    wheel,python,constraints=inputs(tmp_path)
    if failure=='missing':wheel.unlink()
    else:wheel.write_bytes(b'changed')
    calls=[]
    with pytest.raises(ValueError):module().install(tmp_path,python,constraints,run=lambda *a,**kw:calls.append(a))
    assert not calls


def test_pip_failure_is_not_reported_as_success(tmp_path):
    _,python,constraints=inputs(tmp_path);calls=[]
    def run(argv,**kw):calls.append(argv);raise subprocess.CalledProcessError(1,argv)
    with pytest.raises(subprocess.CalledProcessError):module().install(tmp_path,python,constraints,run=run)
    assert len(calls)==1
