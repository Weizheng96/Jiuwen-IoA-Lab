"""Owned deployment setup: no adoption, no model reset and original-effect recovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import pytest

from deploy.configuration import load, validate
from deploy.environment import writer
from deploy.release import digest
from deploy.workspace import Workspace, read_json, save

MODEL = {'name': 'configured-model', 'base_url': 'https://example.invalid/v1', 'api_key': 'fake-secret-never-sent'}


@pytest.fixture
def installed(tmp_path):
    prefix = tmp_path / 'swarm-env'
    scripts = prefix / ('Scripts' if os.name == 'nt' else 'bin')
    scripts.mkdir(parents=True)
    python = scripts / ('python.exe' if os.name == 'nt' else 'python')
    python.write_text('test fixture, never executed')
    (scripts / ('jiuwenswarm-process.exe' if os.name == 'nt' else 'jiuwenswarm-process')).write_text('never executed')
    identity = {'unit': 'swarm', 'fixture': 1}
    save(prefix / 'deployment.json', {'identity': identity, 'environmentId': digest(identity)})
    (prefix / '.ready').write_text(digest(identity))
    return python


class SimulatedWorkspace(Workspace):
    def __init__(self, *args):
        super().__init__(*args)
        self.calls = []; self.resources = {}; self.creations = 0; self.lose_receipt = False
        self.extra_catalog = []; self.fail_initial = False

    def run(self, stage, command, value=None):
        self.calls.append((stage, list(map(str, command)), value))
        assert MODEL['api_key'] not in str(command)
        if stage == 'prepare_workspace':
            (self.home / 'config').mkdir(exist_ok=True)
            (self.home / 'config/config.yaml').write_text('public original template')
            if self.fail_initial:
                self.fail_initial = False
                raise RuntimeError('lost preparation completion')
            return ''
        if stage == 'initial_config':
            return ''
        if stage == 'configure_model':
            entry = value['entry']; current = self.resources.get(entry['model_id'])
            if current and current != entry:
                raise RuntimeError('original resource conflicts')
            if current is None:
                self.resources[entry['model_id']] = entry.copy(); self.creations += 1
            if self.lose_receipt:
                self.lose_receipt = False
                raise RuntimeError('effect completed, receipt lost')
            return 'IOALAB_BOOTSTRAP_JSON:' + json.dumps({'configured': True})
        if stage == 'read_model':
            entry = self.resources.get(value['model_id'])
            if not entry:
                raise RuntimeError('original resource absent')
            return 'IOALAB_BOOTSTRAP_JSON:' + json.dumps({'name': entry['model_name'], 'provider': entry['client_provider'], 'display_name': entry['alias']})
        if stage == 'model_catalog':
            assert list(map(str, command))[-2:] == ['--query-json', '-']
            models = [{'selection_key': entry['model_name'] + '#17', 'model_name': entry['model_name'],
                       'display_name': entry['alias'], 'provider': entry['client_provider']} for entry in self.resources.values()]
            return json.dumps({'status': 'completed', 'request_id': value['request_id'], 'data': {'models': models + self.extra_catalog, 'current_selection': 'placeholder#0'}})
        raise AssertionError(stage)


def test_deployment_inputs_are_data_and_do_not_accept_incomplete_models(tmp_path):
    value = validate({'public_url': 'https://ioa.example/', 'model': MODEL, 'admin_password': 'literal$(anything)`text'})
    assert value['public_url'] == 'https://ioa.example'
    assert value['port'] == 8750 and value['max_model_runs'] == 2 and value['isc_count'] == 10
    assert value['admin_password'] == 'literal$(anything)`text'
    assert value['model']['provider'] == 'OpenAI'
    file = tmp_path / 'deployment.json'
    file.write_text(json.dumps({'public_url': 'http://127.0.0.1:8750', 'model': MODEL}), encoding='utf-8')
    assert load(file)['model']['api_key'] == MODEL['api_key']
    file.write_text('{"public_url":"https://one","public_url":"https://two"}')
    with pytest.raises(ValueError, match='Duplicate JSON'):
        load(file)


@pytest.mark.parametrize('changes', [
    {'unknown': True}, {'public_url': 'https://user:password@example.invalid'},
    {'public_url': 'https://example.invalid/path'}, {'public_url': 'https://example.invalid?token=x'},
    {'public_url': 'https://example.invalid:bad'}, {'public_url': 'https://example.invalid/#x'},
    {'bind': ['127.0.0.1']}, {'bind': 123}, {'port': True}, {'port': 0}, {'max_model_runs': 65},
    {'isc_count': 0}, {'mode': 'other'}, {'pypi_index': 'http://example.invalid/simple'},
    {'admin_password': 'short'}, {'admin_password': False}, {'model': {'name': 'partial'}},
    {'model': {**MODEL, 'provider': 'other'}}, {'model': {**MODEL, 'api_key': 'secret\nline'}},
])
def test_invalid_configuration_never_reflects_model_secrets(changes):
    with pytest.raises(ValueError) as error:
        validate({'public_url': 'https://ioa.example', **changes})
    assert MODEL['api_key'] not in str(error.value)


def test_missing_model_remains_pending_and_can_continue_same_preparation(installed, tmp_path):
    workspace = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    result = workspace.prepare()
    assert result['status'] == 'not_ready' and result['model'] is None
    assert workspace.creations == 0
    marker = read_json(workspace.home / 'ioalab-workspace.json')
    assert marker['phase'] == 'awaiting_model'
    result = workspace.prepare(MODEL)
    assert result['status'] == 'completed' and result['model'] == 'configured-model#17'
    assert workspace.creations == 1
    assert [stage for stage, _, _ in workspace.calls].count('prepare_workspace') == 1
    assert MODEL['api_key'] not in json.dumps(result) + json.dumps(read_json(workspace.home / 'ioalab-workspace.json'))


def test_lost_model_receipt_recovers_one_original_resource(installed, tmp_path):
    workspace = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    workspace.lose_receipt = True
    with pytest.raises(RuntimeError, match='receipt lost'):
        workspace.prepare(MODEL)
    marker = read_json(workspace.home / 'ioalab-workspace.json')
    assert marker['phase'] == 'configuring' and workspace.creations == 1
    assert workspace.prepare(MODEL)['status'] == 'completed'
    assert workspace.creations == 1 and set(workspace.resources) == {marker['modelId']}


def test_model_conflict_after_lost_receipt_is_preserved(installed, tmp_path):
    workspace = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    workspace.lose_receipt = True
    with pytest.raises(RuntimeError):
        workspace.prepare(MODEL)
    resource = next(iter(workspace.resources.values())); resource['api_base'] = 'https://manual.invalid/v1'
    before = dict(resource)
    with pytest.raises(RuntimeError, match='conflicts'):
        workspace.prepare(MODEL)
    assert resource == before and workspace.creations == 1


def test_completed_setup_never_resets_manual_model_or_other_settings(installed, tmp_path, monkeypatch):
    monkeypatch.setenv('IOALAB_ADMIN_PASSWORD', 'should-not-be-inherited')
    monkeypatch.setenv('PYTHONPATH', 'should-not-be-inherited')
    workspace = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    workspace.prepare(MODEL)
    resource = next(iter(workspace.resources.values()))
    resource.update(model_name='manually-changed-model', alias='Manual model', api_key='manual-key', is_default=False)
    config = workspace.home / 'config/config.yaml'; config.write_text('manual permissions, updater and skills')
    before = dict(resource); workspace.calls.clear()
    result = workspace.prepare(MODEL)
    assert result['reused'] and result['model'] == 'manually-changed-model#17'
    assert [stage for stage, _, _ in workspace.calls] == ['read_model', 'model_catalog']
    assert resource == before and config.read_text() == 'manual permissions, updater and skills'
    assert not {'IOALAB_ADMIN_PASSWORD', 'PYTHONPATH'} & workspace.environment.keys()
    with pytest.raises(ValueError, match='Initial model input changed'):
        workspace.prepare({**MODEL, 'api_key': 'different-key'})


def test_unowned_or_other_owned_workspace_is_not_adopted(installed, tmp_path):
    home = tmp_path / 'user-existing'; home.mkdir(); sentinel = home / 'keep.txt'; sentinel.write_text('private existing data')
    with pytest.raises(ValueError, match='no deployment ownership'):
        SimulatedWorkspace(installed, home, uuid.uuid4().hex).prepare(MODEL)
    assert {p.name for p in home.iterdir()} == {'keep.txt'}
    owned = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    owned.prepare()
    before = (owned.home / 'ioalab-workspace.json').read_bytes()
    with pytest.raises(ValueError, match='ownership'):
        SimulatedWorkspace(installed, owned.home, uuid.uuid4().hex).prepare(MODEL)
    assert (owned.home / 'ioalab-workspace.json').read_bytes() == before


def test_real_writer_lock_blocks_second_preparation(installed, tmp_path):
    workspace = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    workspace.prepare()
    with writer(workspace.home / 'ioalab-setup.lock'):
        with pytest.raises(RuntimeError, match='writer lock'):
            workspace.prepare(MODEL)
    assert workspace.creations == 0


def test_interrupted_template_preparation_continues_owned_directory(installed, tmp_path):
    workspace = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    workspace.fail_initial = True
    with pytest.raises(RuntimeError):
        workspace.prepare(MODEL)
    assert read_json(workspace.home / 'ioalab-workspace.json')['phase'] == 'initializing'
    assert workspace.prepare(MODEL)['status'] == 'completed'
    assert workspace.creations == 1


def test_missing_or_ambiguous_public_selection_does_not_appear_ready(installed, tmp_path):
    workspace = SimulatedWorkspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    workspace.prepare(MODEL)
    entry = next(iter(workspace.resources.values()))
    workspace.extra_catalog = [{'selection_key': 'configured-model#19', 'model_name': entry['model_name'], 'provider': entry['client_provider'], 'display_name': entry['alias']}]
    with pytest.raises(RuntimeError, match='ambiguous'):
        workspace.prepare(MODEL)
    workspace.resources.clear(); workspace.extra_catalog.clear()
    with pytest.raises(RuntimeError, match='absent'):
        workspace.prepare(MODEL)
    assert workspace.creations == 1


def test_damaged_installation_is_rejected_before_workspace_creation(installed, tmp_path):
    (installed.parent.parent / '.ready').write_text('corrupted')
    home = tmp_path / 'owned'
    with pytest.raises(ValueError, match='verified isolated'):
        SimulatedWorkspace(installed, home, uuid.uuid4().hex)
    assert not home.exists()


def test_process_input_uses_stdin_and_error_does_not_echo_secrets(installed, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import deploy.workspace as module
    workspace = Workspace(installed, tmp_path / 'owned', uuid.uuid4().hex)
    workspace.home.mkdir()
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout=MODEL['api_key'], stderr=MODEL['api_key'])
    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as error:
        workspace.run('configure_model', [installed, '-I', '-c', 'public configuration'], MODEL)
    assert MODEL['api_key'] not in str(error.value) + str(calls[0][0])
    assert json.loads(calls[0][1]['input']) == MODEL
    assert not list(workspace.home.iterdir())


def test_venv_python_link_keeps_its_own_installation_prefix(installed,tmp_path):
    import sys
    installed.unlink()
    try:installed.symlink_to(sys.executable)
    except OSError as exc:
        if os.name=='nt' and exc.winerror==1314:
            pytest.skip('No Windows symlink privilege; Linux CI exercises original venv links')
        raise
    instance=Workspace(installed,tmp_path/'owned-workspace',uuid.uuid4().hex)
    assert instance.installation_id==digest({'unit':'swarm','fixture':1})
    assert instance.python==installed
