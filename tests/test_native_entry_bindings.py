"""Setup permission and recovery through public methods and a real RuntimeStore."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3

import pytest

from ioalab_sdk.runtime import RuntimeFault, RuntimeStore
from ioalab_sdk.runtime.native_entry_bindings import EntryBindingAuthority, EntryBindingSource, NativeEntryBindings


@pytest.fixture
def setup(tmp_path):
    store = RuntimeStore(tmp_path/'runtime.sqlite', 'http://platform', ['assistant', 'provider'], owner_id='owner')
    now = [100.0]
    source = EntryBindingSource('host', 'adapter', 'assistant', 'assistant-profile',
                                'release', 'account', 'boot', 'capture-digest', 200.0)
    ledger = NativeEntryBindings(store, boot_id='boot', installation_ref='release',
                                 account_ref='account', clock=lambda: now[0])
    try: yield ledger, source, now, store, tmp_path
    finally: store.close()


def reserve(ledger, source, operation='setup'):
    assert ledger.begin(operation, source=source)['phase'] == 'intent'
    intent = ledger.pending(source)
    return intent, ledger.claim(intent['intentRef'], 'target-digest', scope=source)


def authority(source):
    return EntryBindingAuthority(source.host_ref, source.adapter_id, source.actor, source.profile,
                                 source.installation_ref, source.account_ref, source.boot_id)


def test_readers_and_missing_source_never_mint_setup(setup):
    ledger, source, _, _, _ = setup
    assert ledger.get('missing') is None
    assert ledger.status('host')['state'] == 'unknown'
    assert ledger.pending(source) is None
    with pytest.raises(RuntimeFault): ledger.begin('setup')
    assert ledger.get('setup') is None
    assert ledger.pending(source) is None


@pytest.mark.parametrize('change', [
    {'account_ref': 'other'}, {'installation_ref': 'other'}, {'boot_id': 'other'},
    {'expires_at': 100}, {'expires_at': float('nan')}, {'host_ref': ''},
])
def test_new_setup_rejects_invalid_or_expired_sdk_snapshot(setup, change):
    ledger, source, _, _, _ = setup
    with pytest.raises(RuntimeFault): ledger.begin('setup', source=replace(source, **change))
    assert ledger.get('setup') is None


def test_same_operation_reuses_intent_without_extending_lifetime(setup):
    ledger, source, now, _, _ = setup
    original = ledger.begin('setup', source=source)
    intent = ledger.pending(source)
    now[0] = 150
    assert ledger.begin('setup', source=source) == original
    assert ledger.pending(source) == intent
    now[0] = 201
    assert ledger.get('setup')['phase'] == 'expired'
    with pytest.raises(RuntimeFault): ledger.claim(intent['intentRef'], 'target', scope=source)


@pytest.mark.parametrize('change', [
    {'host_ref': 'other'}, {'adapter_id': 'other'}, {'actor': 'provider'},
    {'profile': 'other'}, {'source_digest': 'other'}, {'expires_at': 300},
])
def test_original_operation_cannot_change_source_target(setup, change):
    ledger, source, _, _, _ = setup
    ledger.begin('setup', source=source)
    original = ledger.pending(source)
    with pytest.raises(RuntimeFault): ledger.begin('setup', source=replace(source, **change))
    assert ledger.pending(source) == original


def test_same_claim_reuses_receipt_and_refuses_changed_target(setup):
    ledger, source, _, _, _ = setup
    intent, claim = reserve(ledger, source)
    assert ledger.claim(intent['intentRef'], 'target-digest', scope=source) == claim
    with pytest.raises(RuntimeFault): ledger.claim(intent['intentRef'], 'new-project', scope=source)
    with pytest.raises(RuntimeFault): ledger.claim('fabricated', 'target-digest', scope=source)
    with pytest.raises(RuntimeFault): ledger.claim(intent['intentRef'], 'target-digest', scope=replace(source, actor='provider'))
    assert ledger.get('setup')['phase'] == 'reserved'


def test_roles_and_operations_race_for_one_host_permission(setup):
    ledger, source, _, store, _ = setup
    competing_store = RuntimeStore(store.path, 'http://platform', ['assistant', 'provider'], owner_id='owner')
    other = NativeEntryBindings(competing_store, boot_id='boot', installation_ref='release', account_ref='account', clock=lambda: 100)
    def attempt(index):
        current = source if index == 0 else replace(source, actor='provider', profile='provider-profile')
        selected = ledger if index == 0 else other
        try: return reserve(selected, current, 'setup-'+str(index))[1]
        except RuntimeFault as exc: return exc.code
    try:
        with ThreadPoolExecutor(max_workers=2) as workers: results = list(workers.map(attempt, range(2)))
    finally: competing_store.close()
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 'busy' in results
    assert ledger.status('host')['phase'] == 'reserved'


def test_begin_storage_failure_cannot_leave_a_dangling_intent(setup):
    ledger, source, _, store, _ = setup
    store.db.execute("CREATE TEMP TRIGGER fail_intent BEFORE INSERT ON runtime_checkpoints "
                     "WHEN NEW.namespace='native-entry-intents' BEGIN SELECT RAISE(ABORT,'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError): ledger.begin('setup', source=source)
    assert ledger.get('setup') is None
    assert ledger.pending(source) is None
    assert ledger.status('host')['state'] == 'unknown'
    store.db.execute('DROP TRIGGER fail_intent')
    assert ledger.begin('setup', source=source)['phase'] == 'intent'


def test_atomic_claim_failure_rolls_back_permission_and_allows_original_retry(setup):
    ledger, source, _, store, _ = setup
    ledger.begin('setup', source=source)
    intent = ledger.pending(source)
    # Inject a storage failure at the durable receipt boundary, after CAS writes.
    store.db.execute("CREATE TEMP TRIGGER fail_claim BEFORE INSERT ON runtime_checkpoints "
                     "WHEN NEW.namespace='native-entry-claims' BEGIN SELECT RAISE(ABORT,'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError): ledger.claim(intent['intentRef'], 'target-digest', scope=source)
    assert ledger.get('setup')['phase'] == 'intent'
    assert ledger.pending(source) == intent
    store.db.execute('DROP TRIGGER fail_claim')
    claim = ledger.claim(intent['intentRef'], 'target-digest', scope=source)
    assert ledger.claim(intent['intentRef'], 'target-digest', scope=source) == claim


def test_saved_adapter_receipt_can_ack_after_restart_without_new_write_permission(setup):
    ledger, source, now, store, path = setup
    intent, claim = reserve(ledger, source)
    receipt_file = path/'adapter-save.json'
    receipt_file.write_text(json.dumps({**claim, 'savedReceiptRef': 'atomic-save-receipt'}), encoding='utf-8')
    now[0] = 500
    restarted_store = RuntimeStore(store.path, 'http://platform', ['assistant', 'provider'], owner_id='owner')
    try:
        restarted = NativeEntryBindings(restarted_store, boot_id='next-boot', installation_ref='release', account_ref='account', clock=lambda: 500)
        # Only a new authenticated control grant: no SID, source digest, expiry,
        # chat, source issuer or P10 entry is needed to acknowledge the saved file.
        current = EntryBindingAuthority('host', 'adapter', 'provider', 'provider-profile',
                                         'release', 'account', 'next-boot')
        with pytest.raises(RuntimeFault): restarted.claim(intent['intentRef'], 'target-digest', scope=current)
        saved = json.loads(receipt_file.read_text(encoding='utf-8'))
        completed = restarted.complete(saved['claimRef'], saved['targetDigest'], saved['savedReceiptRef'], scope=current)
        assert completed['phase'] == 'bound'
        assert restarted.complete(saved['claimRef'], saved['targetDigest'], saved['savedReceiptRef'], scope=current) == completed
        assert restarted.begin('setup', source=None) == completed
        assert restarted.get('setup') == completed
        assert ledger.get('setup') == completed
    finally: restarted_store.close()


def test_control_authority_cannot_begin_or_claim_even_with_an_original_intent(setup):
    ledger, source, _, _, _ = setup
    control = authority(source)
    with pytest.raises(RuntimeFault): ledger.begin('unauthorized', source=control)
    assert ledger.get('unauthorized') is None
    with pytest.raises(RuntimeFault): ledger.complete('absent-claim', 'target', 'saved', scope=control)
    assert ledger.status('host')['state'] == 'unknown'
    ledger.begin('setup', source=source)
    intent = ledger.pending(source)
    with pytest.raises(RuntimeFault): ledger.pending(control)
    with pytest.raises(RuntimeFault): ledger.claim(intent['intentRef'], 'target', scope=control)
    assert ledger.get('setup')['phase'] == 'intent'


def test_source_snapshot_cannot_substitute_for_authenticated_completion_authority(setup):
    ledger, source, _, _, _ = setup
    _, claim = reserve(ledger, source)
    with pytest.raises(RuntimeFault): ledger.complete(claim['claimRef'], 'target-digest', 'saved', scope=source)
    assert ledger.get('setup')['phase'] == 'reserved'


@pytest.mark.parametrize('change', [
    {'host_ref': 'other'}, {'adapter_id': 'other'}, {'account_ref': 'other'},
    {'installation_ref': 'other'}, {'boot_id': 'old-boot'}, {'actor': ''}, {'profile': ''},
])
def test_completion_rejects_wrong_or_unverified_control_identity(setup, change):
    ledger, source, _, _, _ = setup
    _, claim = reserve(ledger, source)
    control = replace(authority(source), **change)
    with pytest.raises(RuntimeFault): ledger.complete(claim['claimRef'], 'target-digest', 'saved', scope=control)
    assert ledger.get('setup')['phase'] == 'reserved'


def test_reserved_unknown_save_never_opens_another_project_after_expiry(setup):
    ledger, source, now, _, _ = setup
    reserve(ledger, source)
    now[0] = 500
    fresh = replace(source, expires_at=600, source_digest='new-chat')
    with pytest.raises(RuntimeFault): ledger.begin('new-project', source=fresh)
    assert ledger.status('host')['phase'] == 'reserved'
    assert ledger.get('new-project') is None


@pytest.mark.parametrize('restart', [False, True])
def test_only_never_claimed_expired_intent_can_be_replaced(setup, restart):
    ledger, source, now, store, _ = setup
    ledger.begin('old', source=source)
    old_intent = ledger.pending(source)
    now[0] = 201 if not restart else 100
    fresh = replace(source, expires_at=400, source_digest='new-chat', boot_id='next' if restart else 'boot')
    active = NativeEntryBindings(store, boot_id=fresh.boot_id, installation_ref='release', account_ref='account', clock=lambda: now[0])
    assert active.begin('new', source=fresh)['phase'] == 'intent'
    assert active.get('old')['phase'] == 'expired'
    with pytest.raises(RuntimeFault): active.claim(old_intent['intentRef'], 'old-target', scope=fresh)
    assert active.pending(fresh)['operationId'] == 'new'


def test_bound_project_is_reused_by_another_chat_role_and_operation(setup):
    ledger, source, now, _, _ = setup
    _, claim = reserve(ledger, source)
    original = ledger.complete(claim['claimRef'], 'target-digest', 'saved', scope=authority(source))
    now[0] = 201
    another = replace(source, actor='provider', profile='provider-profile', source_digest='another-project', expires_at=400)
    repeated = ledger.begin('new', source=another)
    assert repeated['bindingRef'] == original['bindingRef']
    assert repeated['phase'] == 'bound'
    assert ledger.pending(another) is None
    assert ledger.get('new') == repeated


def test_upgrade_preserves_original_slot_and_requires_protected_migration(setup):
    ledger, source, _, store, _ = setup
    _, claim = reserve(ledger, source)
    ledger.complete(claim['claimRef'], 'target-digest', 'saved', scope=authority(source))
    upgrade = NativeEntryBindings(store, boot_id='next', installation_ref='new-release', account_ref='account', clock=lambda: 100)
    fresh = replace(source, boot_id='next', installation_ref='new-release')
    assert upgrade.status('host')['phase'] == 'migration_required'
    assert upgrade.begin('new', source=fresh)['phase'] == 'migration_required'
    assert upgrade.complete(claim['claimRef'], 'target-digest', 'saved', scope=authority(fresh))['phase'] == 'migration_required'
    assert upgrade.pending(fresh) is None
    assert ledger.get('setup')['phase'] == 'bound'


def test_completion_requires_original_claim_target_scope_and_save_receipt(setup):
    ledger, source, _, _, _ = setup
    _, claim = reserve(ledger, source)
    for reference, target, scope in [('missing', 'target-digest', authority(source)),
                                     (claim['claimRef'], 'changed', authority(source)),
                                     (claim['claimRef'], 'target-digest', replace(authority(source), host_ref='other'))]:
        with pytest.raises(RuntimeFault): ledger.complete(reference, target, 'saved', scope=scope)
    completed = ledger.complete(claim['claimRef'], 'target-digest', 'saved', scope=authority(source))
    with pytest.raises(RuntimeFault): ledger.complete(claim['claimRef'], 'target-digest', 'new-save', scope=authority(source))
    assert ledger.get('setup') == completed


def test_deleted_authority_cannot_be_recreated_from_adapter_receipt(setup):
    ledger, source, _, store, _ = setup
    _, claim = reserve(ledger, source)
    # Simulates lost original SDK authority, not an adapter cleanup or migration.
    store.checkpoint_delete('native-entry-claims', claim['claimRef'])
    with pytest.raises(RuntimeFault): ledger.complete(claim['claimRef'], 'target-digest', 'saved', scope=authority(source))
    assert ledger.get('setup')['phase'] == 'reserved'


def test_public_projection_copies_never_export_private_setup_authority(setup):
    ledger, source, _, _, _ = setup
    _, claim = reserve(ledger, source)
    for projection in (ledger.get('setup'), ledger.status('host')):
        assert set(projection) == {'operationId', 'hostRef', 'state', 'phase', 'action'}
        serialized = json.dumps(projection)
        for private in ('capture-digest', 'account', 'release', claim['claimRef'], 'target-digest'):
            assert private not in serialized
        projection['phase'] = 'bound'
    assert ledger.get('setup')['phase'] == 'reserved'
