"""Real IoA HTTP/WS and original native history; the model round is an explicit double."""
import json
import os
from pathlib import Path
import uuid

import pytest
from conftest import wait_for
from ioalab_jiuwen.tools import ConfiguredTools
from ioalab_sdk.runtime.host import HostResult
from test_assistant_role import runtime_at, user_decision, send_and_read


@pytest.mark.skipif(not os.environ.get('IOALAB_NATIVE_WEB_CONFIG'), reason='requires explicitly running original native web instance')
def test_platform_forward_reaches_original_native_session_once(platform, tmp_path):
    config_path = Path(os.environ['IOALAB_NATIVE_WEB_CONFIG'])
    original = json.loads(config_path.read_text(encoding='utf-8'))
    options = original['host']['business_tools'][0]['options']
    tools = ConfiguredTools([{'factory': 'jiuwen-web', 'profile': 'personal', 'options': options}],
                            ({'key': 'personal', 'role': 'assistant'},), home=tmp_path / 'receiver/state', base_dir=config_path.parent)
    class ForwardHost:
        def capabilities(self): return {'available': True, 'explicitModelDouble': True}
        def cancel(self, operation): return True
        def run(self, turn):
            turn.on_session(turn.session_id or 'explicit-forward-model-double-' + uuid.uuid4().hex)
            incoming = json.loads(turn.input.split('\n', 1)[1])['input']
            receipt = turn.on_tool('owner.notify', incoming, 'original-notice-call')
            assert turn.on_tool('owner.notify', incoming, 'original-notice-call') == receipt
            turn.on_tool('ioa_reply', {'output': receipt}, 'original-reply')
            return HostResult('completed', turn.session_id, {'explicitModelDouble': True})
    host = ForwardHost()
    try:
        with runtime_at(platform, tmp_path / 'sender') as (sender, outgoing, _, sender_user):
            with runtime_at(platform, tmp_path / 'receiver', host, list(tools.for_profile('personal').values())) as (receiver, inbox, _, owner_user):
                tools.start()
                declaration = {'name': 'message.forward', 'description': '把来信转给主人九问网页',
                    'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}, 'title': {'type': 'string'}},
                                    'required': ['text', 'title'], 'additionalProperties': False},
                    'outputSchema': {'type': 'object', 'properties': {'notificationRef': {'type': 'string'},
                                     'status': {'type': 'string'}, 'read': {'type': 'string'}, 'sessionId': {'type': 'string'}},
                                     'required': ['notificationRef', 'status', 'read'], 'additionalProperties': False},
                    'allowedSenders': [sender_user['username']], 'tools': ['owner.notify']}
                drafted = inbox.call('assistant.rules.draft', {'declaration': declaration, 'instructions': '调用owner.notify转交来信；按实际排队回执回复，不声称主人已读。'})
                assert drafted['status'] == 'completed', drafted
                draft = drafted['data']['draftRef']
                preview = inbox.call('assistant.rules.preview', {'draftRef': draft})['data']
                action = str(uuid.uuid4()); args = {'draftRef': draft, 'contentDigest': preview['contentDigest'], 'expectedRevision': 0}
                waiting = inbox.call('assistant.rules.publish', args, operation_id=action)
                assert waiting['status'] == 'waiting', waiting
                user_decision(receiver, waiting['data']['confirmationRef'])
                assert inbox.call('assistant.rules.publish', args, operation_id=action)['status'] == 'completed'
                interface = outgoing.call('assistant.messaging.interfaces', {'account': owner_user['username']})['data'][0]['interfaceRef']
                content = '赵专员的个人助手发来消息：会议资料已整理，请王经理查收。'
                sent, result = send_and_read(outgoing, {'interfaceRef': interface, 'input': {'title': 'IoA真实收信验证', 'text': content}}, operation=str(uuid.uuid4()))
                assert result['state'] == 'completed', result
                notice = result['result']['output']['notificationRef']
                queue = tools.for_profile('personal')['owner.notify'].invoke.__self__
                rows = []
                def arrived():
                    with queue.lock:
                        row = queue.db.execute('SELECT * FROM notices WHERE id=?', (notice,)).fetchone()
                    if row and row['state'] == 'session_received': rows.append(dict(row)); return True
                    return False
                wait_for(arrived, timeout=30)
                row = rows[0]; source = json.loads(row['context']); native = json.loads(row['result'])
                assert source['agentId'] == receiver.identities[0].agent_id
                assert source['peerAgentId'] == sender.identities[0].agent_id
                assert source['interactionId'] == result['interactionRef'] and source['messageId'] == sent['data']['messageRef']
                assert native['read'] == 'unknown'
                queue.wake.set()
                assert queue.query({'notificationRef': notice}, {**source, 'operationId': 'readonly-query'})['status'] == 'session_received'
                report = {'scope': 'Actual HTTP/WS resident sender/receiver and original Jiuwen history; explicit model/browser-confirmation doubles',
                          'messageRef': source['messageId'], 'interactionId': source['interactionId'], 'senderAgent': source['peerAgentId'],
                          'receiverAgent': source['agentId'], 'notificationRef': notice, 'sessionId': native['sessionId'],
                          'receiptStoreId': native['receiptStoreId'], 'contentDigest': native['contentDigest'],
                          'state': row['state'], 'read': native['read'], 'text': content,
                          'limits': ['Not a native menu/onboarding proof', 'Not real model reasoning', 'User read not proven', 'No new film']}
                if os.environ.get('IOALAB_NATIVE_WEB_REPORT'):
                    Path(os.environ['IOALAB_NATIVE_WEB_REPORT']).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    finally: tools.close()
