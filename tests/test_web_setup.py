"""Preparation writes only its native user namespace and never claims host readiness."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from ioalab_jiuwen.web_setup import prepare_extension
from ioalab_sdk.runtime.protocol import RuntimeFault


def request_file(config,profile='personal'):
    path=config.with_name('arguments.json')
    path.write_text(json.dumps({'profile':profile}),encoding='utf-8')
    return path


def configured(tmp_path, monkeypatch):
    monkeypatch.setenv('IOALAB_TEST_WEB_SECRET', 'LOCAL-SETUP-SECRET')
    swarm = tmp_path / 'existing-swarm'
    (swarm / 'config').mkdir(parents=True)
    (swarm / 'config/config.yaml').write_text('original_owner_settings: retained\n')
    (swarm / 'config/.env').write_text('PRIVATE_MODEL_KEY=never-read-or-print\n')
    settings = {'platform_url': 'http://127.0.0.1:8750', 'username': 'test-owner', 'password': 'test-only',
                'data_dir': str(tmp_path / 'sdk-state'), 'identities': [{'key': 'personal', 'role': 'assistant', 'name': 'Owner'}],
                'host': {'command': ['original-user-command'], 'data_dir': str(swarm), 'business_tools': [
                    {'factory': 'jiuwen-web', 'profile': 'personal', 'options': {
                        'url': 'ws://127.0.0.1:19000/ws?user_id=owner', 'user_id': 'owner',
                        'secret_env': 'IOALAB_TEST_WEB_SECRET'}}]}}
    config = tmp_path / 'settings.local.json'
    config.write_text(json.dumps(settings), encoding='utf-8')
    return config, swarm, settings


def test_prepare_reuses_exact_plugin_and_preserves_existing_host(tmp_path, monkeypatch):
    config, swarm, settings = configured(tmp_path, monkeypatch)
    before = {str(p.relative_to(swarm)): p.read_bytes() for p in swarm.rglob('*') if p.is_file()}
    first = prepare_extension(config, request_file(config), 'assistant')
    assert first['status'] == 'prepared' and first['loaded'] is False
    assert 'LOCAL-SETUP-SECRET' not in json.dumps(first)
    assert prepare_extension(config, request_file(config), 'assistant') == first
    assert all((swarm / name).read_bytes() == blob for name, blob in before.items())
    assert not Path(settings['data_dir']).exists()
    target = Path(first['path'])
    private = json.loads((target / 'settings.local.json').read_text())
    assert private == {'user_id': 'owner', 'secret': 'LOCAL-SETUP-SECRET'}
    assert 'dependencies: {}' in (target / 'extension.yaml').read_text()
    assert not list((swarm / 'application_plugins').glob('.ioa-prepare-*'))
    entry = target / 'extension.py'; entry.write_text('# Existing different content\n')
    with pytest.raises(RuntimeFault, match='review before an explicit migration'): prepare_extension(config, request_file(config), 'assistant')
    assert entry.read_text() == '# Existing different content\n'


def test_missing_original_workspace_is_not_initialized_and_wrong_profile_rejected(tmp_path, monkeypatch):
    config, swarm, settings = configured(tmp_path, monkeypatch)
    with pytest.raises(RuntimeFault): prepare_extension(config, request_file(config, 'someone-else'), 'assistant')
    settings['host']['data_dir'] = str(tmp_path / 'not-installed')
    config.write_text(json.dumps(settings))
    with pytest.raises(RuntimeFault, match='existing Jiuwen workspace'): prepare_extension(config, request_file(config), 'assistant')
    assert not (tmp_path / 'not-installed').exists()


def test_command_reports_prepared_without_secret_or_original_runtime_execution(tmp_path, monkeypatch):
    config, swarm, settings = configured(tmp_path, monkeypatch)
    config.write_text(json.dumps(settings),encoding='utf-8-sig')
    arguments=request_file(config)
    arguments.write_text(json.dumps({'profile':'personal'}),encoding='utf-8-sig')
    result = subprocess.run([sys.executable, '-B', '-m', 'ioalab_jiuwen', 'web-extension', '--config', str(config), '--args-file', str(arguments), '--role', 'assistant'],
                            capture_output=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout
    value = json.loads(result.stdout)
    assert value['status'] == 'prepared' and value['loaded'] is False
    assert 'LOCAL-SETUP-SECRET' not in result.stdout and 'PRIVATE_MODEL_KEY' not in result.stdout


def test_explicit_prepare_generates_private_credential_once_without_editing_existing_files(tmp_path, monkeypatch):
    config, swarm, settings = configured(tmp_path, monkeypatch)
    monkeypatch.delenv('IOALAB_TEST_WEB_SECRET')
    credential = tmp_path / 'ioa-only' / 'notification.env'
    settings['host']['business_tools'][0]['options']['credentials_file'] = str(credential)
    config.write_text(json.dumps(settings))
    result = prepare_extension(config, request_file(config), 'assistant')
    original = credential.read_bytes()
    secret = json.loads((Path(result['path']) / 'settings.local.json').read_text())['secret']
    assert len(secret) >= 32 and secret in original.decode() and secret not in json.dumps(result)
    assert prepare_extension(config, request_file(config), 'assistant') == result and credential.read_bytes() == original
    credential.write_text('UNRELATED_SETTING=preserved\n')
    with pytest.raises(RuntimeFault): prepare_extension(config, request_file(config), 'assistant')
    assert credential.read_text() == 'UNRELATED_SETTING=preserved\n'


def test_prepare_never_creates_credential_outside_ioa_configuration_directory(tmp_path, monkeypatch):
    config, swarm, settings = configured(tmp_path, monkeypatch)
    monkeypatch.delenv('IOALAB_TEST_WEB_SECRET')
    outside = tmp_path.parent / (tmp_path.name + '-outside-notification.env')
    settings['host']['business_tools'][0]['options']['credentials_file'] = str(outside)
    config.write_text(json.dumps(settings))
    with pytest.raises(RuntimeFault, match='only inside the IoA configuration directory'): prepare_extension(config, request_file(config), 'assistant')
    assert not outside.exists()


@pytest.mark.parametrize('bad',['role','configuration','runtime_data'])
def test_prepare_rejects_wrong_role_and_protected_arguments_before_writing(tmp_path,monkeypatch,bad):
    config,swarm,settings=configured(tmp_path,monkeypatch)
    arguments=request_file(config);role='assistant'
    if bad=='role':role='service'
    elif bad=='configuration':arguments=config
    else:
        arguments=Path(settings['data_dir'])/'arguments.json'
        arguments.parent.mkdir();arguments.write_text(json.dumps({'profile':'personal'}),encoding='utf-8')
    original=config.read_bytes();original_args=arguments.read_bytes()
    with pytest.raises(RuntimeFault) as error:prepare_extension(config,arguments,role)
    assert error.value.code=='invalid_input'
    assert config.read_bytes()==original and arguments.read_bytes()==original_args
    assert not (swarm/'application_plugins').exists()
