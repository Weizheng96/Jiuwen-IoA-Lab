"""196 one-way projections of real 193 persisted shapes; never invoke old adapters."""
from dataclasses import asdict
import hashlib
from types import SimpleNamespace
import pytest

from ioalab_sdk.runtime import (HostRegistry,HostRegistration,HostInput,HostContext,NativeEventTarget,
    RuntimeStore,RuntimeFault,encode)


class Adapter:
    registration=HostRegistration('fixture','1',('get_request',))
    def __init__(self):self.calls=[]
    def submit(self,*args,**kwargs):self.calls.append('submit');pytest.fail('Migration replayed native input')
    def get_request(self,*args):self.calls.append('get_request');pytest.fail('Migration invented a native request')


def setup(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Adapter();registry=HostRegistry({'original':host,'other':Adapter()},'other')
    registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='personal')])
    registry.bind_context('actor','interaction',HostContext('original','sid',str(tmp_path)))
    return store,host,registry


def legacy(store,*,interaction='interaction',terminal=None,delivery='handled',target=None):
    target=target or ['actor',interaction,'original','sid']
    digest=hashlib.sha256(encode(target).encode()).hexdigest()
    execution={'operation_id':'op','host_ref':'original','session_id':'sid','request_ref':'actual-native-request',
        'attempt_nonce':'original-attempt','receipt_store_id':'original-private-store','terminal':terminal,'evidence_ref':'original-machine-event'} if terminal else None
    record={'parameters':encode('request'),'target':target,'dispatchState':'attempting','attemptNonce':'original-attempt',
        'receiptStoreId':'original-private-store','targetDigest':digest,
        'receipt':{'operation_id':'op','host_ref':'original','session_id':'sid','status':delivery,'reference':'original-queue','execution':execution}}
    store.checkpoint_set('host-deliveries',encode(['actor','op']),record)
    return record


def test_handled_without_native_request_is_preserved_unknown_without_replay(tmp_path):
    store,host,registry=setup(tmp_path)
    before=legacy(store)
    try:
        receipt=registry.get_submission('actor','interaction','op')
        assert receipt.attempt_id=='original-attempt' and receipt.delivery=='handled'
        assert receipt.state=='unknown' and receipt.request_id is None and receipt.terminal is None
        assert registry.submit('actor','interaction','op',HostInput('request'))==receipt
        record=store.checkpoint('host-deliveries',encode(['actor','op']))
        assert record['legacy']==before and not record['released'] and not host.calls
    finally:store.close()


def test_legacy_run_without_attempt_is_durably_blocked_and_never_guesses_default(tmp_path):
    store,host,registry=setup(tmp_path)
    original={'actor':'actor','interaction':'interaction','hostRef':'original'}
    store.checkpoint_set('host-executions','op',original)
    try:
        with pytest.raises(RuntimeFault) as caught:registry.submit('actor','interaction','op',HostInput('request'))
        assert caught.value.code=='unknown_result'
        record=store.checkpoint('host-deliveries',encode(['actor','op']))
        assert record['legacyUnverified'] and record['legacy']=={'execution':original}
        assert 'attempt' not in record and 'receipt' not in record
        registry.default='other'
        with pytest.raises(RuntimeFault):registry.get_submission('actor','interaction','op')
        assert not host.calls
    finally:store.close()


def test_migration_transaction_interruption_retries_original_receipt(tmp_path):
    store,host,registry=setup(tmp_path);before=legacy(store)
    try:
        # Real SQLite fault at the cross-version checkpoint write boundary.
        with store.transaction() as connection:
            connection.execute("CREATE TEMP TRIGGER fail_migration BEFORE UPDATE ON runtime_checkpoints WHEN NEW.namespace='host-deliveries' AND json_extract(NEW.value,'$.format')=2 BEGIN SELECT RAISE(ABORT,'fixture interruption'); END")
            connection.execute("CREATE TEMP TRIGGER fail_migration_insert BEFORE INSERT ON runtime_checkpoints WHEN NEW.namespace='host-deliveries' AND json_extract(NEW.value,'$.format')=2 BEGIN SELECT RAISE(ABORT,'fixture interruption'); END")
        with pytest.raises(Exception,match='fixture interruption'):registry.get_submission('actor','interaction','op')
        assert store.checkpoint('host-deliveries',encode(['actor','op']))==before
        with store.transaction() as connection:
            connection.execute('DROP TRIGGER fail_migration');connection.execute('DROP TRIGGER fail_migration_insert')
        receipt=registry.get_submission('actor','interaction','op')
        assert receipt.attempt_id=='original-attempt' and not host.calls
        assert store.checkpoint('host-deliveries',encode(['actor','op']))['legacy']==before
    finally:store.close()


@pytest.mark.parametrize('terminal',['failed','cancelled'])
def test_original_193_notice_has_zero_sdk_callback_scope_and_real_failure_can_release(tmp_path,terminal):
    from ioalab_sdk.runtime import NativeEvents
    from ioalab_sdk.runtime.scheduling import Scheduler
    import threading
    store,host,registry=setup(tmp_path)
    registry.record_origin('actor','mid',HostContext('original','sid',str(tmp_path)))
    target=NativeEventTarget('actor','mid','host-origins',encode(['actor','mid']))
    route=encode(['native-origin',asdict(target),str(tmp_path)])
    record={'actor':'actor','operation':'op','target':asdict(target),'context':{'hostRef':'original','sessionId':'sid','workspace':str(tmp_path)},'notice':{'facts':{}}}
    store.checkpoint_set('native-events',encode(['actor','op']),record)
    store.checkpoint_set('native-event-pending',encode(['actor','op']),{'actor':'actor','operation':'op'})
    before=legacy(store,interaction=route,terminal=terminal,delivery='delivered',target=['actor',route,'original','sid'])
    runtime=SimpleNamespace(store=store,closing=threading.Event())
    service=NativeEvents(runtime,registry)
    runtime.scheduler=Scheduler(store,service.handlers(),max_models=1)
    job=runtime.scheduler.submit('actor','op',service.handler,{'eventRef':'op'},operation_id='op',session='sid')['reference']
    # Existing 193 on-disk unknown job, not a new job execution or new dispatch.
    with store.transaction() as connection:connection.execute("UPDATE runtime_jobs SET state='unknown' WHERE id=?",(job,))
    try:
        service.recover()
        result=runtime.scheduler.get(job)
        assert result['status']=='failed' and result['data']['reason']=='native_'+terminal
        assert result['data']['receipt']['delivery']=='delivered'
        migrated=store.checkpoint('host-deliveries',encode(['actor','op']))
        assert migrated['released'] and migrated['legacy']==before
        assert migrated['receipt']['request_id']=='actual-native-request'
        assert store.interaction(origin='mid') is None and not host.calls
        service.clock=lambda:10**12;service.recover()
        assert service.status()['pending']==0
    finally:runtime.scheduler.close();store.close()


def test_ordinary_legacy_execution_terminal_is_not_callback_drain_proof(tmp_path):
    store,host,registry=setup(tmp_path);legacy(store,terminal='failed',delivery='accepted')
    try:
        failed=registry.get_submission('actor','interaction','op')
        assert failed.state=='failed' and failed.delivery=='accepted'
        saved=store.checkpoint('host-deliveries',encode(['actor','op']))
        assert not saved['released'] and 'settledEvidence' not in saved and not host.calls
    finally:store.close()


@pytest.mark.parametrize('field',['attempt_nonce','receipt_store_id','request_ref'])
def test_wrong_original_machine_evidence_cannot_wash_legacy_receipt(tmp_path,field):
    store,host,registry=setup(tmp_path);original=legacy(store,terminal='failed')
    changed={**original,'receipt':{**original['receipt'],'execution':{**original['receipt']['execution'],field:'' if field=='request_ref' else 'another'}}}
    store.checkpoint_set('host-deliveries',encode(['actor','op']),changed)
    try:
        with pytest.raises((RuntimeFault,ValueError)):registry.get_submission('actor','interaction','op')
        assert store.checkpoint('host-deliveries',encode(['actor','op']))==changed and not host.calls
    finally:store.close()
