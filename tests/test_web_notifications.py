"""Durability and scope over real local SQLite/WS; native history is an explicit double."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
import uuid

import pytest
from websockets.sync.server import serve

from ioalab_jiuwen.tools import ConfiguredTools
from ioalab_jiuwen.web_client import WebClient, NotSent, fingerprint
from a2c.codec import digest
from ioalab_jiuwen.web_notifications import NOTIFICATION_OUTPUT, WebNotifications, factory
from ioalab_sdk.runtime.host import validate_output
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


def test_public_tools_and_rule_preview_include_notification_output_contract(platform, tmp_path, monkeypatch):
    from test_assistant_role import runtime_at
    monkeypatch.setenv('IOALAB_TEST_WEB_SECRET', 'LOCAL-TEST-SECRET')
    tools = ConfiguredTools([{'factory': 'jiuwen-web', 'profile': 'personal', 'options': options()}],
                            ({'key': 'personal', 'role': 'assistant'},), home=tmp_path / 'queue', base_dir=tmp_path)
    try:
        bindings = tools.for_profile('personal')
        with runtime_at(platform, tmp_path / 'owner', tools=bindings.values()) as (_, client, _, _):
            listed = client.call('assistant.tools.list')
            assert listed['status'] == 'completed'
            assert {tool['name']: tool['output_schema'] for tool in listed['data']['tools']} == {
                name: NOTIFICATION_OUTPUT for name in bindings}
            drafted = client.call('assistant.rules.draft', {'declaration': {
                'name': 'owner.forward', 'description': '隔离测试：按实际通知回执回复',
                'inputSchema': bindings['owner.notify'].input_schema, 'outputSchema': NOTIFICATION_OUTPUT,
                'allowedSenders': [], 'tools': list(bindings)},
                'instructions': '把已获准内容交给owner.notify，按原回执回复；不将排队或会话接收说成已读。'})
            assert drafted['status'] == 'completed', drafted
            preview = client.call('assistant.rules.preview', {'draftRef': drafted['data']['draftRef']})
            assert preview['status'] == 'completed'
            assert {tool['name']: tool['output_schema'] for tool in preview['data']['preview']['tools']} == {
                name: NOTIFICATION_OUTPUT for name in bindings}
            for tool in bindings.values():
                assert digest(tool.policy()) != digest(replace(tool, output_schema=None).policy())
        assert not (tmp_path / 'queue').exists()
    finally: tools.close()


def test_actual_queue_states_validate_without_inventing_delivery_or_read(tmp_path, monkeypatch):
    monkeypatch.setenv('IOALAB_TEST_WEB_SECRET', 'LOCAL-TEST-SECRET')
    tools = ConfiguredTools([{'factory': 'jiuwen-web', 'profile': 'personal', 'options': options(max_pending=2)}],
                            ({'key': 'personal', 'role': 'assistant'},), home=tmp_path, base_dir=tmp_path)
    bindings = tools.for_profile('personal'); queue = bindings['owner.notify'].invoke.__self__
    native = NativeDouble(); entered, release = threading.Event(), threading.Event()
    def deliver(notice):
        entered.set()
        assert release.wait(3), 'Release the isolated in-flight notification'
        return native.deliver(notice)
    queue.client.probe, queue.client.get, queue.client.deliver = native.probe, native.get, deliver
    def notify(source, text):
        return validate_output(bindings['owner.notify'], bindings['owner.notify'].invoke({'text': text}, source))
    def query(source, receipt):
        result = bindings['owner.notification_status'].invoke({'notificationRef': receipt['notificationRef']}, source)
        return validate_output(bindings['owner.notification_status'], result)
    try:
        tools.start()
        first_source, second_source, rejected_source = context(), context(), context()
        first, second = notify(first_source, 'First'), notify(second_source, 'Second')
        rejected = notify(rejected_source, 'Capacity full')
        assert first['status'] == second['status'] == 'queued'
        assert rejected['status'] == 'rejected' and rejected['read'] == 'unknown'
        assert query(rejected_source, rejected) == rejected and not native.calls
        with queue.lock:
            queue.db.execute("UPDATE notices SET expires=? WHERE state='queued'", (time.time() - 1,))
            queue._expire()
        assert query(first_source, first)['status'] == query(second_source, second)['status'] == 'expired'
        unknown_source = context(); pending = notify(unknown_source, 'Unknown write')
        native.online = native.unknown = True; queue.wake.set()
        assert entered.wait(3)
        sending = query(unknown_source, pending)
        assert sending['status'] == 'sending' and sending['read'] == 'unknown' and 'sessionId' not in sending
        release.set()
        wait_for(lambda: query(unknown_source, pending)['status'] == 'unknown')
        unknown = query(unknown_source, pending)
        assert notify(unknown_source, 'Unknown write') == unknown and len(native.calls) == 1
        native.unknown = False
        received_source = context(); received_notice = notify(received_source, 'Received write')
        wait_for(lambda: query(received_source, received_notice)['status'] == 'session_received')
        received_notice = query(received_source, received_notice)
        assert received_notice['sessionId'] == 'native-test-session' and received_notice['read'] == 'unknown'
        assert len(native.calls) == 2 and first['status'] == 'queued'
    finally: release.set(); tools.close()


@pytest.mark.parametrize('result', [
    None,
    {},
    {'notificationRef': 'original', 'status': 'delivered', 'read': 'unknown'},
    {'notificationRef': 'original', 'status': 'session_received', 'read': True},
    {'notificationRef': 'original', 'status': 'queued', 'read': 'read'},
    {'notificationRef': '', 'status': 'unknown', 'read': 'unknown'},
    {'notificationRef': 'original', 'status': 'unknown', 'read': 'unknown', 'sessionId': None},
    {'notificationRef': 'original', 'status': 'unknown', 'read': 'unknown', 'sessionId': ''},
    {'notificationRef': 'original', 'status': 'queued', 'read': 'unknown', 'success': True},
])
def test_notification_contract_rejects_invented_results_before_they_reach_the_model(tmp_path, monkeypatch, result):
    monkeypatch.setenv('IOALAB_TEST_WEB_SECRET', 'LOCAL-TEST-SECRET')
    bundle = factory(options(), home=tmp_path, profile='personal', base_dir=tmp_path)
    for tool in bundle.bindings:
        with pytest.raises(RuntimeFault) as error: validate_output(tool, result)
        assert error.value.code == 'invalid_tool_output' and error.value.recovery == 'inspect_tool_effect'
    assert not (tmp_path / 'roles').exists()
