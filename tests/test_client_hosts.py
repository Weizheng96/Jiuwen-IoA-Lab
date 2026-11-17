"""Product configuration and lifecycle through its public, host-selecting CLI."""
import json
import sys
import pytest
from pathlib import Path
import threading
from ioalab_client import assembled
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
        'host': {'registrations': {'codex': {'type': 'codex'}}}}}))
    assert main(['configure', '--config', str(path), '--args-file', str(changes)]) == 0
    output = capsys.readouterr().out
    assert 'private-test-value' not in output
    assert json.loads(path.read_text())['username'] == 'test-account'
    assert json.loads(path.read_text())['password'] == 'private-test-value'
    assert main(['configuration', '--config', str(path)]) == 0
    after = json.loads(capsys.readouterr().out)['data']
    assert after['hostType'] == 'codex' and after['hostFields']==['default','registrations']
    assert 'host.data_dir' not in after['editableFields']
    assert 'host.registrations' in after['editableFields']


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


def test_codex_resident_connects_binds_both_roles_and_closes_original_runtime(platform, tmp_path, monkeypatch):
    from importlib import metadata
    original_version=metadata.version
    monkeypatch.setattr(metadata,'version',lambda name:'0.0.186' if name=='ioalab-client' else original_version(name))
    from test_resident_runtime import configuration
    from conftest import wait_for
    path, _ = configuration(platform, tmp_path)
    value = json.loads(path.read_text())
    # Unified product registration selects only the packaged native adapter.
    value['host'] = {'default':'codex','registrations':{'codex':{'type': 'codex'}}}
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
            # Startup is not gated on S02 and never falls back to the explicit SDK driver.
            assert status['host']['configured'] and status['host']['adapterId'] == 'codex'
            assert status['host']['state'] == 'unknown' and status['host']['available'] is False
            assert {identity['role'] for identity in status['identities']} == {'assistant', 'service'}
            assert all(identity['bound'] for identity in status['identities'])
            assert status['installation']['assembly'] == 'ioalab-client'
        finally:
            try:
                latest = client.call('runtime.status')['data']
                stopped = client.call('runtime.stop', {'bootId': latest['bootId']})
                assert stopped['status'] == 'completed'
            finally:
                assembly.runtime.stop_requested.set()
                running.join(10)
        assert not running.is_alive()


def test_registry_addition_keeps_default_and_explicit_migration_is_single_authority(tmp_path,capsys):
    path=account(tmp_path)
    document=json.loads(path.read_text())
    old={'type':'codex','command':[sys.executable],'model':'test-model'}
    document['host']=old
    path.write_text(json.dumps(document))
    assert main(['start','--config',str(path)])==1
    assert json.loads(capsys.readouterr().out)['error']['recovery']=='migrate_host'
    assert main(['configuration','--config',str(path)])==0
    before=json.loads(capsys.readouterr().out)['data']
    changes=tmp_path/'migration.json'
    changes.write_text(json.dumps({'expectedDigest':before['configurationDigest'],'changes':{'host':{
        'registrations':{'claude':{'type':'claude_code'}}}}}))
    assert main(['configure','--config',str(path),'--args-file',str(changes)])==0
    capsys.readouterr()
    host=json.loads(path.read_text())['host']
    assert set(host)=={'default','registrations'} and host['default']=='codex'
    assert host['registrations']['codex']==old and set(host['registrations'])=={'codex','claude'}
    assert main(['configure','--config',str(path),'--args-file',str(changes)])==0
    assert json.loads(capsys.readouterr().out)['data']['changed'] is False


def test_interrupted_configuration_migration_preserves_original_session_owner(tmp_path,capsys,monkeypatch,platform):
    from ioalab_sdk.runtime import RuntimeStore,HostRegistry,RuntimeConfig,ResidentRuntime
    from test_resident_runtime import configuration
    from ioalab_sdk.runtime import configuration as configuration_module
    path,_=configuration(platform,tmp_path);document=json.loads(path.read_text())
    document['host']={'type':'codex','command':[sys.executable],'model':'test-model'}
    path.write_text(json.dumps(document));original=path.read_bytes()
    runtime=ResidentRuntime(RuntimeConfig.load(path))
    # Login has already migrated the legacy password into the device vault.
    # Host migration must preserve this authenticated configuration snapshot.
    original=path.read_bytes()
    actor=runtime.identities[0].agent_id;actors=[item.agent_id for item in runtime.identities];owner=runtime.account.account['id'];home=runtime.config.home
    store=runtime.store
    store.prepare(actor,'message.send',{'messageId':'mid','recipient':'peer','kind':'message.request',
                  'body':{'interfaceName':'echo','revision':1}},'operation')
    store.recover_link(actor,{'interactionId':'interaction','sender':actor,'recipient':'peer','originMessageId':'mid',
                       'interfaceName':'echo','revision':1,'deadline':'2099-01-01T00:00:00Z','maxRequests':8})
    store.bind_session('interaction','real-codex-session','definition')
    store.prepare(actor,'message.send',{'messageId':'pending-mid','recipient':'peer','kind':'message.request',
                  'body':{'interfaceName':'echo','revision':1}},'pending-operation')
    store.recover_link(actor,{'interactionId':'pending-interaction','sender':actor,'recipient':'peer','originMessageId':'pending-mid',
                       'interfaceName':'echo','revision':1,'deadline':'2099-01-01T00:00:00Z','maxRequests':8})
    store.prepare(actor,'message.send',{'messageId':'unopened-mid','recipient':'peer','kind':'message.request',
                  'body':{'interfaceName':'echo','revision':1}},'unopened-operation')
    runtime.close()
    assert main(['configuration','--config',str(path)])==0
    digest=json.loads(capsys.readouterr().out)['data']['configurationDigest']
    changes=tmp_path/'migration.json'
    changes.write_text(json.dumps({'expectedDigest':digest,'changes':{'host':{'default':'claude','registrations':{
        'claude':{'type':'claude_code'}}}}}))
    write=configuration_module.private_json
    def interrupted(*args):raise OSError('interrupted settings replacement')
    monkeypatch.setattr(configuration_module,'private_json',interrupted)
    with pytest.raises(OSError,match='interrupted'):
        main(['configure','--config',str(path),'--args-file',str(changes)])
    assert path.read_bytes()==original
    monkeypatch.setattr(configuration_module,'private_json',write)
    assert main(['configure','--config',str(path),'--args-file',str(changes)])==0
    capsys.readouterr()
    store=RuntimeStore(home/'runtime.sqlite',platform.s.base_url,actors,owner_id=owner)
    try:
        registry=HostRegistry({'codex':object(),'claude':object()},'claude');registry.bind(store)
        assert registry.context(actor,'interaction')['hostRef']=='codex'
        assert registry.context(actor,'interaction')['sessionId']=='real-codex-session'
        assert registry.context(actor,'pending-interaction')['hostRef']=='codex'
        assert registry.context(actor,'pending-interaction')['sessionId'] is None
        store.recover_link(actor,{'interactionId':'unopened-interaction','sender':actor,'recipient':'peer','originMessageId':'unopened-mid',
                           'interfaceName':'echo','revision':1,'deadline':'2099-01-01T00:00:00Z','maxRequests':8})
        assert registry.context(actor,'unopened-interaction')['hostRef']=='codex'
    finally:store.close()


def test_untyped_legacy_host_requires_explicit_migration_declaration(tmp_path,capsys):
    path=account(tmp_path);document=json.loads(path.read_text())
    document['host']={'command':[sys.executable],'model':'test-model'};path.write_text(json.dumps(document))
    assert main(['configuration','--config',str(path)])==0
    before=json.loads(capsys.readouterr().out)['data']
    assert 'host.migrate_from' in before['editableFields']
    changes=tmp_path/'migration.json'
    changes.write_text(json.dumps({'expectedDigest':before['configurationDigest'],'changes':{'host':{'migrate_from':'codex'}}}))
    assert main(['configure','--config',str(path),'--args-file',str(changes)])==0
    capsys.readouterr();host=json.loads(path.read_text())['host']
    assert set(host)=={'default','registrations'} and host['default']=='codex'
    assert host['registrations']['codex']['type']=='codex'
