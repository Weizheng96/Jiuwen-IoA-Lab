"""Actual SDK setup ledger and OS save through the product resource boundary."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from ioalab_codex import native_adapter
from ioalab_sdk.runtime import RuntimeStore, RuntimeFault, HostRegistry
from ioalab_sdk.runtime.native_sources import NativeSources, native_source_descriptor


@pytest.fixture
def original(tmp_path):
    store = RuntimeStore(tmp_path/'runtime.sqlite', 'http://platform', ['assistant', 'provider'], owner_id='owner')
    bundle = native_adapter(host_ref='codex', resource_root=tmp_path/'adapter')
    resource = bundle['native_resources']['codex']
    project = tmp_path/'actual-project'; project.mkdir()
    registry = HostRegistry({'codex':bundle['host']}, 'codex')
    sources = NativeSources(store, registry, [SimpleNamespace(profile=role+'-key',agent_id=role)
        for role in ('assistant','provider')], {'release':'release'}, 'account', tmp_path/'client', clock=lambda:100)
    sources.attach({'bootId':'boot','url':'http://127.0.0.1:19199'})
    for role in ('assistant','provider'): sources.provision('codex',role+'-key')
    descriptor = json.loads(native_source_descriptor(tmp_path/'client','codex','assistant-key').read_text())
    authorization = 'Bearer '+descriptor['grant']
    scope = sources.authenticate(authorization)
    reference = sources.register(authorization, {'sessionId':'actual-SID','workspace':str(project),'captureId':'actual-tool'})
    source = sources.setup_source(reference)
    ledger = sources.entry_bindings
    resource.bind_entry_control(sources.entry_control_for('codex'))
    capture = {'format':'ioalab.codex-launch/1','projectDirectory':str(project),
               'cli':str(tmp_path/'codex.exe'),'extensionDirectory':str(tmp_path/'entry'),
               'configDirectoryState':'unset','configDirectory':None}
    try: yield bundle, resource, scope, ledger, source, capture, reference['entryRef']
    finally: resource.close(); store.close()


def test_native_setup_saves_after_original_intent_and_claim_and_reuses_project(original):
    bundle, resource, scope, ledger, source, capture, entry = original
    handler = bundle['native_handlers']['codex']
    body = {'kind':'entry_setup','entryRef':entry,'capture':capture,'workspace':capture['projectDirectory']}
    with pytest.raises(RuntimeFault): handler(scope, body)
    assert not resource.store.path.exists()
    ledger.begin('setup-original', source=source)
    handler(scope, body)
    assert ledger.get('setup-original')['phase'] == 'bound'
    saved = resource.store.path.read_bytes()
    other = {**capture,'projectDirectory':str(Path(capture['projectDirectory']).parent/'other')}
    handler(scope, {**body,'capture':other,'workspace':other['projectDirectory']})
    assert resource.store.path.read_bytes() == saved
    # Same host/account new legitimate role re-ACKs original saved receipt.
    handler({**scope,'actor':'provider','profile':'provider-key'}, {'kind':'entry_recover'})
    assert resource.store.path.read_bytes() == saved


def test_native_setup_lost_ack_recovers_original_claim_without_recapture(original):
    bundle, resource, scope, ledger, source, capture, entry = original
    ledger.begin('setup-original', source=source)
    original_control = resource.store.entry_control
    def lost(actual, body):
        if body['action'] == 'complete': raise OSError('lost ACK')
        return original_control(actual, body)
    resource.store.entry_control = lost
    handler = bundle['native_handlers']['codex']
    with pytest.raises(OSError):
        handler(scope, {'kind':'entry_setup','entryRef':entry,'capture':capture,'workspace':capture['projectDirectory']})
    saved = resource.store.path.read_bytes()
    resource.store.entry_control = original_control
    handler(scope, {'kind':'entry_recover'})
    assert ledger.get('setup-original')['phase'] == 'bound'
    assert resource.store.path.read_bytes() == saved


def test_native_setup_refuses_wrong_account_or_changed_workspace(original):
    bundle, resource, scope, ledger, source, capture, entry = original
    ledger.begin('setup-original', source=source)
    handler = bundle['native_handlers']['codex']
    with pytest.raises(RuntimeFault):
        handler(scope, {'kind':'entry_setup','entryRef':entry,'capture':capture,'workspace':str(Path(capture['projectDirectory']).parent)})
    assert not resource.store.path.exists()
    handler(scope, {'kind':'entry_setup','entryRef':entry,'capture':capture,'workspace':capture['projectDirectory']})
    with pytest.raises(RuntimeFault): handler({**scope,'accountBindingRef':'other'}, {'kind':'entry_recover'})


def test_native_setup_drain_refuses_new_control_and_waits_for_original_call(original, monkeypatch):
    import threading
    bundle, resource, scope, ledger, source, capture, entry = original
    handler = bundle['native_handlers']['codex']
    entered, release = threading.Event(), threading.Event()
    def slow_recover(actual):
        entered.set(); release.wait(5); return {'status': 'completed'}
    monkeypatch.setattr(resource.store, 'recover', slow_recover)
    running = threading.Thread(target=handler, args=(scope, {'kind':'entry_recover'})); running.start()
    assert entered.wait(5)
    assert resource.drain(0) is False  # the original setup call keeps native control
    with pytest.raises(RuntimeFault) as refused: handler(scope, {'kind':'entry_recover'})
    assert refused.value.code == 'closed'
    release.set(); running.join(5)
    assert resource.drain(.1) is True
