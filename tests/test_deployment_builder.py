"""Build preparation cannot turn an unknown or unverified attempt into ready."""
import json
from pathlib import Path
import subprocess

import pytest

from deploy import builder
from deploy.release import read_json
from deploy.workspace import save


def selected(tmp_path):
    lock=tmp_path/'build.lock';lock.write_text('packaging==26.3\nsetuptools==84.2.1\n')
    return builder.Builder(tmp_path/'builds',lock)


def fake_install(monkeypatch, events):
    def create(self,prefix):Path(prefix).mkdir()
    def execute(command,*,cwd,log):
        assert not (Path(command[0]).parent.parent/'.ready').exists() or 'install' not in command
        events.append(list(map(str,command)))
    monkeypatch.setattr(builder.venv.EnvBuilder,'create',create)
    monkeypatch.setattr(builder,'run',execute)


def test_validation_precedes_ready_and_repeat_does_not_reinstall(tmp_path,monkeypatch):
    value=selected(tmp_path);events=[];fake_install(monkeypatch,events)
    original=value.validate
    def validate(state,*,completed=True):
        path=value.folder/state['attempt']
        assert (path/'.ready').exists() is completed
        return original(state,completed=completed)
    monkeypatch.setattr(value,'validate',validate)
    python=value.ensure('https://mirror.example/simple')
    assert python==value.ensure('https://mirror.example/simple')
    assert read_json(value.folder/'state.json')['phase']=='ready'
    assert sum('install' in event for event in events)==1
    assert (value.folder/'requirements.lock').read_bytes()==value.lock.read_bytes()


def test_failed_verification_has_no_completion_and_explicit_retry_is_new_attempt(tmp_path,monkeypatch):
    value=selected(tmp_path);events=[];fake_install(monkeypatch,events)
    original=builder.run
    def fail(command,**options):
        if 'check' in command:raise subprocess.CalledProcessError(1,command)
        return original(command,**options)
    monkeypatch.setattr(builder,'run',fail)
    with pytest.raises(RuntimeError,match='preserve'): value.ensure('https://mirror.example/simple')
    first=read_json(value.folder/'state.json')
    assert first['phase']=='failed' and not (value.folder/first['attempt']/'.ready').exists()
    with pytest.raises(RuntimeError,match='explicitly retry'): value.ensure('https://mirror.example/simple')
    monkeypatch.setattr(builder,'run',original)
    value.ensure('https://mirror.example/simple',retry_failed=True)
    assert read_json(value.folder/'state.json')['attempt']!=first['attempt']
    assert (value.folder/first['attempt']).is_dir()


def test_live_or_unknown_installation_is_not_replayed_even_when_retry_requested(tmp_path,monkeypatch):
    value=selected(tmp_path);value.folder.mkdir(parents=True)
    save(value.folder/'owner.json',value.identity)
    save(value.folder/'state.json',{'phase':'installing','attempt':'a-'+'1'*16})
    monkeypatch.setattr(builder.venv.EnvBuilder,'create',lambda *args:pytest.fail('second writer'))
    with pytest.raises(RuntimeError,match='unknown'): value.ensure('https://mirror.example/simple',retry_failed=True)
    assert read_json(value.folder/'state.json')['phase']=='installing'


def test_owned_lock_change_is_not_repaired(tmp_path,monkeypatch):
    value=selected(tmp_path);events=[];fake_install(monkeypatch,events)
    value.ensure('https://mirror.example/simple')
    path=value.folder/'requirements.lock';path.write_text('packaging==0\nsetuptools==0\n')
    with pytest.raises(ValueError,match='preserve'): value.ensure('https://mirror.example/simple')
    assert path.read_text().startswith('packaging==0')


def test_build_child_does_not_inherit_model_or_import_overrides(monkeypatch):
    monkeypatch.setenv('IOALAB_LLM_API_KEY','private-key')
    monkeypatch.setenv('PYTHONPATH','outside-code')
    monkeypatch.setenv('PIP_INDEX_URL','https://uncontrolled.example')
    values=builder.child_environment()
    assert not {'IOALAB_LLM_API_KEY','PYTHONPATH','PIP_INDEX_URL'} & set(values)
    assert values['PYTHONNOUSERSITE']=='1'


@pytest.mark.parametrize('lock',['packaging==26.3\n','packaging @ https://example.com/code\nsetuptools==84.2.1\n','packaging==26.3\nsetuptools==84.2.1\npytest==9.0\n'])
def test_invalid_build_tools_are_rejected_before_effects(tmp_path,lock):
    path=tmp_path/'build.lock';path.write_text(lock)
    with pytest.raises(ValueError): builder.Builder(tmp_path/'env',path)
    assert not (tmp_path/'env').exists()


def test_runtime_installation_child_uses_same_credential_boundary(tmp_path,monkeypatch):
    from deploy import environment
    monkeypatch.setenv('IOALAB_ADMIN_PASSWORD','private-administrator')
    monkeypatch.setenv('AGENTMAIL_API_KEY','private-notification')
    monkeypatch.setenv('VIRTUAL_ENV','foreign-runtime')
    seen=[]
    def execute(command,**options):seen.append(options['env'])
    monkeypatch.setattr(environment.subprocess,'run',execute)
    environment.execute(Path('python'),['-m','pip','check'],tmp_path,tmp_path/'log')
    assert not {'IOALAB_ADMIN_PASSWORD','AGENTMAIL_API_KEY','VIRTUAL_ENV'} & set(seen[0])
    assert seen[0]['PIP_CONFIG_FILE']==environment.os.devnull
