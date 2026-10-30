"""Original-call identity across periods, restarts and persisted schema upgrades."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from a2c.codec import digest_bytes
from ioalab.store import DB
from ioalab.delegation.relay import ProtocolRelay
from ioalab_sdk.adapters.transport import SessionTransport,stable
from ioalab_sdk.runtime import RuntimeStore,RuntimeFault


@pytest.fixture
def transport_context(tmp_path):
    path=tmp_path/'runtime.sqlite'
    store=RuntimeStore(path,'http://local',['assistant'],owner_id='owner')
    relation=('origin','interaction','assistant','service','outbound','delegation',0,'{}')
    with store.transaction() as conn:
        conn.execute('INSERT INTO runtime_interactions VALUES(?,?,?,?,?,?,?,?,NULL)',relation)
    runtime=SimpleNamespace(store=store,account=SimpleNamespace(config={'market':'market','pay':'pay'}),closing=threading.Event())
    calls=[]
    def call(actor,kind,payload,*,operation_id):
        runtime.store.prepare(actor,kind,payload,operation_id)
        result={'original':payload}
        runtime.store.complete({'agentId':actor,'operation':kind,'operationId':operation_id,'ok':True,'result':result})
        calls.append((actor,kind,payload,operation_id))
        return result
    runtime.connection=SimpleNamespace(call=call,wait_for=lambda predicate:{'reply':{'body':{'op':'acceptance.notify'}},'files':{}})
    identity=SimpleNamespace(agent_id='assistant',protocol_id='assistant')
    def create():
        return SessionTransport(runtime,identity,'interaction','admission','service',public=SimpleNamespace())
    yield runtime,create,calls,path
    runtime.store.close()


def test_repeated_original_method_has_per_operation_identity_and_recovers_same_bytes_after_restart(transport_context):
    runtime,create,calls,path=transport_context
    transport=create()
    first={'op':'acceptance.notify','report':{'period':1,'signature':'original-one'}}
    second={'op':'acceptance.notify','report':{'period':2,'signature':'original-two'}}
    with transport.operation('accept-period-1'):
        transport.send('service','acceptance.notify',first)
    with transport.operation('accept-period-2'):
        transport.send('service','acceptance.notify',second)
    envelopes=[call[2] for call in calls]
    assert envelopes[0]['messageId']!=envelopes[1]['messageId']
    assert [value['body']['body'] for value in envelopes]==[first,second]
    runtime.store.close()
    runtime.store=RuntimeStore(path,'http://local',['assistant'],owner_id='owner')
    recovered=create()
    with recovered.operation('accept-period-1'):
        recovered.send('service','acceptance.notify',first)
        with pytest.raises(RuntimeFault,match='without re-signing'):
            recovered.send('service','acceptance.notify',{**first,'replacement':True})
    assert calls[-1][2]==envelopes[0] and calls[-1][3]==calls[0][3]
    with pytest.raises(RuntimeFault,match='saved local business operation'):
        recovered.send('service','acceptance.notify',first)


def test_original_initial_ids_and_partially_uploaded_files_remain_recoverable(transport_context):
    runtime,create,calls,path=transport_context
    uploads=[];transport=create();data=b'original material'
    def put(name,value,operation):
        uploads.append(operation)
        return {'fileId':operation,'name':name,'digest':digest_bytes(value),'size':len(value)}
    transport.files=SimpleNamespace(put=put)
    # The first upload succeeds, but the response is lost before envelope save.
    def lost(name,value,operation):
        put(name,value,operation)
        raise OSError('lost already-uploaded file receipt')
    transport.files.put=lost
    with pytest.raises(OSError):transport.send('service','request.submit',{'request':{}},{'brief':data})
    transport.files.put=put
    transport.send('service','request.submit',{'request':{}},{'brief':data})
    assert uploads==[stable('assistant','interaction','request.submit','brief',digest_bytes(data))]*2
    assert calls[-1][2]['messageId']==stable('assistant','interaction','request.submit')
    assert runtime.store.checkpoint(transport.namespace,'request:request.submit')['envelope']==calls[-1][2]


def test_control_operations_keep_original_receipts_and_context_is_nested_and_thread_local(transport_context):
    runtime,create,calls,path=transport_context
    transport=create()
    with transport.operation(None):
        transport.send('pay','ap2.pay',{'request':{'original':'fee'}})
        initial=transport.control_receipt('ap2.pay')
    assert calls[-1][3]==stable('assistant','interaction','ap2.pay')
    with pytest.raises(RuntimeFault,match='saved local payment operation'):
        transport.send('pay','ap2.pay',{'purpose':'installment','period':1})
    with transport.operation('pay-1'):
        transport.send('pay','ap2.pay',{'purpose':'installment','period':1})
        receipt=transport.control_receipt('ap2.pay')
        with transport.operation('pay-2'):
            transport.send('pay','ap2.pay',{'purpose':'installment','period':2})
            assert transport.control_receipt('ap2.pay')['payload']['ap2']['period']==2
        assert transport.control_receipt('ap2.pay')==receipt
    assert transport.control_receipt('ap2.pay')==initial
    assert len({call[3] for call in calls})==3
    barrier=threading.Barrier(2)
    def isolated(reference):
        with transport.operation(reference):
            barrier.wait(5)
            return transport._identity('delivery.fetch'),transport._key('request','delivery.fetch')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(isolated,['fetch-1','fetch-2']))
    assert results==[(stable('assistant','interaction',key,'delivery.fetch'),'request:'+key+':delivery.fetch') for key in ('fetch-1','fetch-2')]
    with pytest.raises(RuntimeError):
        with transport.operation('failed-parent'):raise RuntimeError('test')
    assert transport._key('request','delivery.fetch')=='request:delivery.fetch'


LEGACY='''CREATE TABLE delegation_protocol_calls(
 message TEXT PRIMARY KEY,interaction TEXT NOT NULL,admission TEXT NOT NULL,op TEXT NOT NULL,
 state TEXT NOT NULL,last_result TEXT,last_hash TEXT,task TEXT,UNIQUE(interaction,op));'''
ROW=('original-message','interaction','admission','ap2.checkout','completed','original-result','original-hash','original-task')


def relay(db):return ProtocolRelay(db,None,None,None,None,clock=lambda:1)


@pytest.mark.parametrize('explicit',[False,True])
def test_call_schema_upgrade_preserves_rows_indexes_triggers_and_can_repeat_method_after_reopen(tmp_path,explicit):
    path=tmp_path/'core.sqlite';db=DB(path)
    try:
        db.run(LEGACY.replace(',UNIQUE(interaction,op)','') if explicit else LEGACY)
        if explicit:db.run('CREATE UNIQUE INDEX legacy_method_unique ON delegation_protocol_calls(interaction,op)')
        db.run('CREATE INDEX preserve_task ON delegation_protocol_calls(task)')
        db.run('CREATE TABLE upgrade_audit(message TEXT)')
        db.run('CREATE TRIGGER preserve_audit AFTER INSERT ON delegation_protocol_calls BEGIN INSERT INTO upgrade_audit VALUES(NEW.message); END')
        db.run('INSERT INTO delegation_protocol_calls VALUES(?,?,?,?,?,?,?,?)',*ROW)
        relay(db)
        assert tuple(db.run('SELECT * FROM delegation_protocol_calls').fetchone())==ROW
        db.close();db=DB(path);relay(db)
        db.run('INSERT INTO delegation_protocol_calls VALUES(?,?,?,?,?,?,?,?)','second-message',*ROW[1:])
        assert db.one('SELECT COUNT(*) AS n FROM delegation_protocol_calls')['n']==2
        assert db.one("SELECT sql FROM sqlite_master WHERE name='preserve_task'")
        assert [row['message'] for row in db.rows('SELECT * FROM upgrade_audit')]==['original-message','second-message']
        with pytest.raises(sqlite3.IntegrityError):db.run('INSERT INTO delegation_protocol_calls VALUES(?,?,?,?,?,?,?,?)',*ROW)
    finally:db.close()


def test_failed_call_schema_upgrade_rolls_back_original_schema_rows_and_indexes(tmp_path,monkeypatch):
    db=DB(tmp_path/'core.sqlite')
    try:
        db.run(LEGACY);db.run('INSERT INTO delegation_protocol_calls VALUES(?,?,?,?,?,?,?,?)',*ROW)
        original=db.tx
        class FailRename:
            def __init__(self,conn):self.conn=conn
            def execute(self,sql,*args):
                if sql.startswith('ALTER TABLE delegation_protocol_calls_upgraded'):raise OSError('test crash after old table drop')
                return self.conn.execute(sql,*args)
        @contextmanager
        def failed():
            with original() as conn:yield FailRename(conn)
        with monkeypatch.context() as patch:
            patch.setattr(db,'tx',failed)
            with pytest.raises(OSError):relay(db)
        assert tuple(db.run('SELECT * FROM delegation_protocol_calls').fetchone())==ROW
        assert not db.one("SELECT 1 FROM sqlite_master WHERE name='delegation_protocol_calls_upgraded'")
        with pytest.raises(sqlite3.IntegrityError):db.run('INSERT INTO delegation_protocol_calls VALUES(?,?,?,?,?,?,?,?)','second-message',*ROW[1:])
        relay(db)
        db.run('INSERT INTO delegation_protocol_calls VALUES(?,?,?,?,?,?,?,?)','second-message',*ROW[1:])
    finally:db.close()
