"""Public onboarding CLI, native installation and bound launcher behavior."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'src/client/skills'


def test_fixed_handoff_before_account_setup_never_claims_complete(tmp_path, monkeypatch, capsys):
    from ioalab_client.cli import main
    from ioalab_client import installation
    monkeypatch.setattr(installation, 'prepare', lambda: {'status': 'completed', 'data': {'python': sys.executable}})
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert main(['onboarding', '--host', 'codex', '--config', str(tmp_path/'client/settings.local.json')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'waiting'
    data = result['data']
    assert data['format'] == 'ioalab.onboarding/1'
    assert data['website']['myAgents'] == 'https://ioalab.openjiuwen.com/workbench'
    assert data['configurationFile'] == str((tmp_path/'client/settings.local.json').resolve())
    assert len(data['skills']) == 2 and data['newSessionVerified'] is False
    assert 'password' not in json.dumps(data).lower()


def test_bound_role_can_request_handoff_through_its_public_operation(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from ioalab_client import commands
    from ioalab_sdk.runtime import private_json
    path = tmp_path/'client/settings.local.json'
    private_json(path, {'username': 'local-account', 'password': 'private-test-secret',
                        'data_dir': str(tmp_path/'data'),
                        'identities': [{'key': 'personal', 'role': 'assistant', 'name': 'My assistant'}]})
    args = tmp_path/'handoff.json'; args.write_text('{"host":"codex"}')
    monkeypatch.setattr(commands, 'installation', lambda: SimpleNamespace(prepare=lambda: {
        'status': 'completed', 'data': {'python': sys.executable}}))
    assert commands.main(['--role', 'assistant', '--config', str(path), 'runtime.onboarding', '--args-file', str(args)]) == 0
    text = capsys.readouterr().out
    assert json.loads(text)['data']['format'] == 'ioalab.onboarding/1'
    assert 'private-test-secret' not in text and not (tmp_path/'data').exists()


@pytest.mark.parametrize('host,folder', [('codex', '.agents'), ('claude_code', '.claude')])
def test_both_native_skills_share_config_without_reading_it(tmp_path, monkeypatch, host, folder):
    from ioalab_client.native_skills import install, status
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    config = tmp_path/'client/settings.local.json'
    config.parent.mkdir(); config.write_text('not JSON: secret configuration must never be read by installer')
    result = install(host, config=config, source=SOURCE)
    assert result['installed'] and not result['newSessionVerified']
    for item in result['skills']:
        path = tmp_path/folder/'skills'/item['name']
        assert item['path'] == str(path)
        launcher = (path/'scripts/ioalab.py').read_text(encoding='utf-8')
        assert repr(str(config.resolve())) in launcher
        assert 'secret configuration' not in launcher
    assert all(item['installed'] for item in status(host, config=config))
    assert install(host, config=config, source=SOURCE)['installed']
    # Another host reuses exactly the same binding without changing the client.
    other = 'claude_code' if host == 'codex' else 'codex'
    assert install(other, config=config, source=SOURCE)['binding'] == result['binding']


def test_conflicting_role_preserves_user_files_and_prepares_other_role(tmp_path, monkeypatch):
    from ioalab_client.native_skills import install
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path/'.agents/skills/ioalab-delegate'; path.mkdir(parents=True)
    original = path/'SKILL.md'; original.write_bytes(b'user-owned')
    result = install('codex', source=SOURCE)
    assert not result['installed']
    assert result['skills'][0]['status'] == 'manual_required'
    assert result['skills'][0]['instructions']
    assert result['skills'][1]['installed']
    assert original.read_bytes() == b'user-owned'


def test_native_permission_failure_returns_manual_guidance_for_both_roles(tmp_path, monkeypatch):
    from ioalab_client.native_skills import install
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path == tmp_path/'.agents/skills': raise PermissionError('native directory is read-only')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    result = install('codex', source=SOURCE)
    assert all(not item['installed'] and item['instructions'] and item['package'] for item in result['skills'])


def test_manual_native_extraction_retains_program_binding_and_can_be_verified(tmp_path, monkeypatch):
    from ioalab_client import export_skills
    from ioalab_client.native_skills import status
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    files = export_skills(tmp_path/'exports', source=SOURCE)
    native = tmp_path/'.agents/skills'
    for path in files:
        with zipfile.ZipFile(path) as archive: archive.extractall(native)
    assert all(item['installed'] for item in status('codex'))
    (native/'ioalab-provider/SKILL.md').write_bytes(b'user-modified')
    assert not status('codex')[1]['installed']


def test_jiuwen_returns_two_native_import_steps(tmp_path, monkeypatch):
    from ioalab_client.native_skills import install
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    result = install('jiuwen', source=SOURCE)
    assert not result['installed']
    assert all(item['status'] == 'manual_required' and item['instructions'] for item in result['skills'])
    assert not (tmp_path/'.jiuwenswarm').exists()


def test_exported_launcher_refuses_account_rebinding_in_fresh_process(tmp_path):
    from ioalab_client import export_skills
    files = export_skills(tmp_path/'exports', source=SOURCE, config=tmp_path/'original.json')
    with zipfile.ZipFile(files[0]) as archive: archive.extractall(tmp_path/'skill')
    script = tmp_path/'skill/ioalab-delegate/scripts/ioalab.py'
    result = subprocess.run([sys.executable, str(script), 'runtime.status', '--config', str(tmp_path/'other.json')],
                            capture_output=True, encoding='utf-8')
    assert result.returncode == 1
    assert json.loads(result.stdout)['error']['code'] == 'identity_conflict'


def test_new_account_prepares_both_roles_but_existing_configuration_is_untouched(platform, tmp_path):
    from ioalab_sdk.runtime.account_setup import AccountSetupUi, setup_document
    from ioalab_sdk.runtime import RuntimeConfig
    from ioalab_sdk.provider import validate_provider
    path = tmp_path/'client/settings.local.json'
    options = {'platform_url': platform.s.base_url, 'prepare_both_roles': True}
    ui = AccountSetupUi(path, options)
    assert ui.decision({'mode': 'register'}, None)['authenticated']
    config = RuntimeConfig.load(path)
    assert {p['role'] for p in config.identities} == {'assistant', 'service'}
    assert validate_provider(config.provider, config.identities)['service']['accepting'] is False
    before = path.read_bytes()
    setup_document(path, options)
    assert path.read_bytes() == before
    # A user-only view can show the same credentials for website login; public receipts cannot.
    private = ui.snapshot()
    assert private['credentials']['username'] == config.username
    assert private['credentials']['password'] == config.password
    assert private['configurationFile'] == str(path.resolve())
    assert config.password not in json.dumps(json.loads(ui.receipt.read_text(encoding='utf-8')))


def test_handoff_uses_actual_platform_and_never_returns_configured_secrets(platform, tmp_path, monkeypatch):
    from ioalab_client.onboarding import summary, markdown
    from ioalab_sdk.runtime.account_setup import AccountSetupUi
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path/'client/settings.local.json'
    ui = AccountSetupUi(path, {'platform_url': platform.s.base_url, 'prepare_both_roles': True})
    ui.decision({'mode': 'register'}, None)
    secret = ui.snapshot()['credentials']['password']
    result = summary('codex', config=path)
    assert result['data']['website']['home'] == platform.s.base_url
    assert result['data']['accountName'] == ui.snapshot()['credentials']['username']
    assert secret not in json.dumps(result) and secret not in markdown(result)
    assert result['data']['newSessionVerified'] is False and result['status'] == 'waiting'


@pytest.mark.parametrize('mode', ['register', 'existing'])
def test_account_page_credentials_also_login_to_website(platform, client, tmp_path, mode):
    from ioalab_sdk.runtime.account_setup import AccountSetupUi
    from _support import new_user, PASSWORD
    ui = AccountSetupUi(tmp_path/'client/settings.local.json', {'platform_url': platform.s.base_url, 'prepare_both_roles': True})
    values = {'mode': mode}
    if mode == 'existing':
        user = new_user(platform)
        values.update(username=user['username'], password=PASSWORD)
    ui.decision(values, None)
    credentials = ui.snapshot()['credentials']
    response = client.post('/api/auth/login', json=credentials)
    assert response.status_code == 200
    assert client.get('/api/me').json()['username'] == credentials['username']
    assert client.get('/workbench').status_code == 200


def test_external_account_config_does_not_change_reported_client_location(tmp_path):
    from ioalab_client.layout import locations
    root = tmp_path/'client'; root.mkdir()
    (root/'ioalab-bootstrap.json').write_text('{}')
    python = root/'env'/'Scripts'/'python.exe'
    result = locations(tmp_path/'private/settings.json', python)
    assert result['clientDirectory'] == str(root)
    assert result['programDirectory'] == str(root/'env')
    assert result['configurationFile'] == str((tmp_path/'private/settings.json').resolve())
