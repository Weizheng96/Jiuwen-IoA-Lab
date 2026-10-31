"""Durability and scope over real local SQLite/WS; native history is an explicit double."""
import json
from pathlib import Path
import threading
import time
import uuid

import pytest
from websockets.sync.server import serve

from ioalab_jiuwen.tools import ConfiguredTools
from ioalab_jiuwen.web_client import WebClient, NotSent, fingerprint
from ioalab_jiuwen.web_notifications import WebNotifications
from ioalab_sdk.runtime.protocol import RuntimeFault
from conftest import wait_for


def context(**updates):
    return {'operationId': str(uuid.uuid4()), 'agentId': 'owner', 'messageId': str(uuid.uuid4()),
            'interactionId': 'same-interaction', 'peerAgentId': 'verified-sender', **updates}


def options(**updates):
    return {'url': 'ws://127.0.0.1:1/ws?user_id=test-owner', 'user_id': 'test-owner',
            'secret_env': 'IOALAB_TEST_WEB_SECRET', 'retry_seconds': 1, **updates}


class NativeDouble:
    """Models durable original receipts, not real Jiuwen/UI evidence."""
    def __init__(self):
        self.store = uuid.uuid4().hex
        self.store_id = None
        self.records = {}
        self.calls = []
        self.online = False
        self.lose_reply = False
        self.unknown = False

    def probe(self):
        if not self.online: raise NotSent('explicit offline transport')
        return self.store

    def get(self, notice):
        self.probe()
        return self.records.get(notice['messageId'], self.receipt(notice, 'absent'))

    def receipt(self, notice, state):
        return {'messageId': notice['messageId'], 'contentDigest': fingerprint(notice), 'status': state,
                'userId': 'test-owner', 'receiptStoreId': self.store, 'sessionId': 'native-test-session', 'read': 'unknown'}

    def deliver(self, notice):
        self.probe()
        self.calls.append(notice.copy())
        receipt = self.records.setdefault(notice['messageId'], self.receipt(notice, 'unknown' if self.unknown else 'session_received'))
        if self.lose_reply:
            self.lose_reply = False
            raise TimeoutError('Explicit lost reply after persistence')
        return receipt


def queue_at(tmp_path, monkeypatch, native=None, **updates):
    monkeypatch.setenv('IOALAB_TEST_WEB_SECRET', 'LOCAL-TEST-SECRET')
    queue = WebNotifications(options(**updates), home=tmp_path, profile='personal', base_dir=tmp_path)
    if native: queue.client.probe, queue.client.get, queue.client.deliver = native.probe, native.get, native.deliver
    queue.start()
    return queue


def received(queue, ref, source):
    return queue.query({'notificationRef': ref}, source)['status'] == 'session_received'


def test_factory_is_side_effect_free_and_only_starts_after_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv('IOALAB_TEST_WEB_SECRET', 'LOCAL-TEST-SECRET')
    tools = ConfiguredTools([{'factory': 'jiuwen-web', 'profile': 'personal', 'options': options()}],
                            ({'key': 'personal', 'role': 'assistant'},), home=tmp_path / 'state', base_dir=tmp_path)
    try:
        assert not (tmp_path / 'state').exists()
        assert set(tools.for_profile('personal')) == {'owner.notify', 'owner.notification_status'}
        assert 'LOCAL-TEST-SECRET' not in json.dumps(tools.status())
        with pytest.raises(RuntimeFault, match='not started'):
            tools.for_profile('personal')['owner.notify'].invoke({'text': 'Hello'}, context())
        tools.start()
        assert (tmp_path / 'state/roles/personal/business/web-notifications/queue.json').is_file()
        assert tools.status()[0]['resource']['state'] == 'running'
    finally: tools.close()


def test_offline_restart_original_scope_and_single_delivery(tmp_path, monkeypatch):
    native = NativeDouble(); source = context()
    queue = queue_at(tmp_path, monkeypatch, native)
    try:
        first = queue.notify({'text': '原消息'}, source)
        assert first['status'] == 'queued'
        assert queue.notify({'text': '原消息'}, {**source, 'operationId': str(uuid.uuid4())}) == first
        with pytest.raises(RuntimeFault, match='content or source changed'): queue.notify({'text': '替换正文'}, source)
        for field in ('agentId', 'interactionId', 'peerAgentId'):
            with pytest.raises(RuntimeFault): queue.query({'notificationRef': first['notificationRef']}, {**source, field: 'someone-else'})
    finally: queue.close()
    native.online = True
    queue = queue_at(tmp_path, monkeypatch, native)
    try:
        wait_for(lambda: received(queue, first['notificationRef'], source))
        assert len(native.calls) == 1 and first['status'] == 'queued'
        assert queue.query({'notificationRef': first['notificationRef']}, source)['read'] == 'unknown'
        assert queue.notify({'text': '原消息'}, source)['status'] == 'session_received'
    finally: queue.close()


def test_lost_write_reply_recovers_by_receipt_and_store_reset_never_resends(tmp_path, monkeypatch):
    native = NativeDouble(); native.online = native.lose_reply = True
    queue = queue_at(tmp_path, monkeypatch, native); source = context()
    try:
        ref = queue.notify({'text': 'Original'}, source)['notificationRef']
        wait_for(lambda: received(queue, ref, source))
        assert len(native.calls) == 1
        native.online = False
        second = queue.notify({'text': 'Pending'}, context())
    finally: queue.close()
    native.store = uuid.uuid4().hex; native.records = {}; native.online = True
    queue = queue_at(tmp_path, monkeypatch, native)
    try:
        wait_for(lambda: queue.status()['error'] == 'receipt_store_changed')
        assert len(native.calls) == 1
        assert queue.status()['counts']['queued'] == 1
    finally: queue.close()


def test_unknown_is_retained_beyond_ttl_and_cannot_be_replayed(tmp_path, monkeypatch):
    native = NativeDouble(); native.online = native.unknown = True
    queue = queue_at(tmp_path, monkeypatch, native); source = context()
    try:
        ref = queue.notify({'text': 'Original'}, source)['notificationRef']
        wait_for(lambda: queue.query({'notificationRef': ref}, source)['status'] == 'unknown')
        with queue.lock: queue.db.execute('UPDATE notices SET expires=?', (time.time() - 1,))
        native.records = {}
        queue.wake.set()
        time.sleep(1.1)
        assert queue.query({'notificationRef': ref}, source)['status'] == 'unknown'
        assert queue.notify({'text': 'Original'}, source)['status'] == 'unknown' and len(native.calls) == 1
    finally: queue.close()


def test_never_sent_expiry_capacity_and_missing_database_are_explicit(tmp_path, monkeypatch):
    native = NativeDouble(); queue = queue_at(tmp_path, monkeypatch, native, max_pending=1)
    source = context()
    try:
        first = queue.notify({'text': 'Original'}, source)
        assert queue.notify({'text': 'Another'}, context())['status'] == 'rejected'
        with queue.lock: queue.db.execute('UPDATE notices SET expires=? WHERE id=?', (time.time() - 1, first['notificationRef']))
        queue.wake.set()
        wait_for(lambda: queue.query({'notificationRef': first['notificationRef']}, source)['status'] == 'expired')
    finally: queue.close()
    (queue.root / 'notifications.sqlite').unlink()
    with pytest.raises(RuntimeFault, match='Restore the original notification database'):
        queue_at(tmp_path, monkeypatch, native)
    assert not native.calls


def test_rpc_wire_checks_fixed_target_credentials_and_original_receipt():
    records = []
    store = uuid.uuid4().hex
    def handle(socket):
        request = json.loads(socket.recv(timeout=5)); records.append(request)
        method, args = request['method'], request['params']
        assert socket.request.path == '/ws?user_id=test-owner'
        assert args['bridge_token'] == 'LOCAL-TEST-SECRET'
        payload = {'protocol': 'ioa.web-notifications/1', 'userId': 'test-owner', 'receiptStoreId': store}
        if method.endswith('.deliver'):
            assert args['receiptStoreId'] == store
            payload = {**payload, 'messageId': args['messageId'], 'contentDigest': args['contentDigest'],
                       'status': 'session_received', 'sessionId': 'actual-wire-session', 'read': 'unknown'}
        socket.send(json.dumps({'type': 'res', 'id': request['id'], 'ok': True, 'payload': payload}))
    server = serve(handle, '127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        port = server.socket.getsockname()[1]
        client = WebClient(f'ws://127.0.0.1:{port}/ws?user_id=test-owner', 'test-owner', 'LOCAL-TEST-SECRET')
        client.store_id = client.probe()
        notice = {'messageId': uuid.uuid4().hex, 'sender': 'verified', 'title': '通知', 'text': '内容', 'group': 'original-iid'}
        assert client.deliver(notice)['sessionId'] == 'actual-wire-session'
        assert len(records) == 2
    finally: server.shutdown(); thread.join(timeout=5)
    for url in ('ws://example.com/ws?user_id=test-owner', 'ws://127.0.0.1/ws?user_id=other', 'ws://name:password@127.0.0.1/ws?user_id=test-owner'):
        with pytest.raises(RuntimeFault): WebClient(url, 'test-owner', 'LOCAL-TEST-SECRET')
