"""Product configuration and lifecycle through its public, host-selecting CLI."""
import json
import sys
import pytest
from pathlib import Path
import threading
from ioalab_codex import assembled
from ioalab_sdk.runtime import LocalClient
from ioalab_client.runtime import main


def account(tmp_path):
    path = tmp_path/'settings.json'
    path.write_text(json.dumps({'username': 'test-account', 'password': 'private-test-value',
        'data_dir': str(tmp_path/'data'), 'identities': [
            {'key': 'personal', 'role': 'assistant', 'name': 'Test assistant'}]}), encoding='utf-8')
    return path


def test_codex_configures_without_jiuwen_fields_and_preserves_account(tmp_path, capsys):
    path = account(tmp_path)
    assert main(['configuration', '--config', str(path)]) == 0
    before = json.loads(capsys.readouterr().out)['data']
    assert before['hostType'] is None and before['supportedHostTypes'] == ['jiuwen', 'codex', 'claude_code']
    changes = tmp_path/'changes.json'
    changes.write_text(json.dumps({'expectedDigest': before['configurationDigest'], 'changes': {
        'host': {'type': 'codex', 'command': [sys.executable], 'model': 'test-model'}}}))
    assert main(['configure', '--config', str(path), '--args-file', str(changes)]) == 0
    output = capsys.readouterr().out
    assert 'private-test-value' not in output
    assert json.loads(path.read_text())['username'] == 'test-account'
    assert json.loads(path.read_text())['password'] == 'private-test-value'
    assert main(['configuration', '--config', str(path)]) == 0
    after = json.loads(capsys.readouterr().out)['data']
    assert after['hostType'] == 'codex' and 'data_dir' not in after['hostFields']
    assert 'host.data_dir' not in after['editableFields']
    assert 'host.model' in after['editableFields']


def test_start_requires_explicit_host_before_any_process_or_identity(tmp_path, capsys):
    path = account(tmp_path)
    original = path.read_bytes()
    assert main(['start', '--config', str(path)]) == 1
    assert json.loads(capsys.readouterr().out)['error']['recovery'] == 'configure_host'
    assert path.read_bytes() == original and not (tmp_path/'data').exists()


def test_codex_rejects_foreign_host_fields_without_changing_account(tmp_path, capsys):
    path = account(tmp_path)
    original = path.read_bytes()
    main(['configuration', '--config', str(path)])
    before = json.loads(capsys.readouterr().out)['data']
    changes = tmp_path/'changes.json'
    changes.write_text(json.dumps({'expectedDigest': before['configurationDigest'], 'changes': {
        'host': {'type': 'codex', 'command': [sys.executable], 'model': 'test-model', 'data_dir': '/jiuwen'}}}))
    assert main(['configure', '--config', str(path), '--args-file', str(changes)]) == 1
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'invalid_input'
    assert path.read_bytes() == original and not (tmp_path/'data').exists()


def test_codex_resident_connects_binds_both_roles_and_closes_original_runtime(platform, tmp_path):
    from test_resident_runtime import configuration
    from conftest import wait_for
    path, _ = configuration(platform, tmp_path)
    value = json.loads(path.read_text())
    value['host'] = {'type': 'codex', 'command': [sys.executable, '-u',
        str(Path(__file__).with_name('_codex_process.py'))], 'model': 'test-model'}
    path.write_text(json.dumps(value))
    with assembled(path) as assembly:
        running = threading.Thread(target=assembly.run)
        running.start()
        client = LocalClient(assembly.runtime.config.home, timeout=2)
        try:
            def ready():
                try:
                    result = client.call('runtime.status')['data']
                    return result if result['platformConnected'] else None
                except Exception:
                    return None
            wait_for(ready, timeout=15)
            status = client.call('runtime.status')['data']
            assert status['host']['available'] is True
            assert {identity['role'] for identity in status['identities']} == {'assistant', 'service'}
            assert all(identity['bound'] for identity in status['identities'])
            assert status['installation']['assembly'] == 'ioalab-codex'
            assert status['host']['features']['native_tools'] is False
            assert status['host']['features']['interaction_callback'] is False
        finally:
            try:
                latest = client.call('runtime.status')['data']
                stopped = client.call('runtime.stop', {'bootId': latest['bootId']})
                assert stopped['status'] == 'completed'
            finally:
                assembly.runtime.stop_requested.set()
                running.join(10)
        assert not running.is_alive()
