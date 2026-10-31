"""Verify an already-received native notification without creating a new effect."""
import argparse
import json
from pathlib import Path
import time
import uuid

from websockets.sync.client import connect
from ioalab_jiuwen.web_notifications import WebNotifications
from ioalab_jiuwen.web_client import fingerprint
from ioalab_sdk.runtime.protocol import RuntimeFault, encode


def read_history(url, session):
    request = 'ioa-history-check-' + uuid.uuid4().hex
    with connect(url, open_timeout=5, close_timeout=1, proxy=None, max_size=262144) as socket:
        socket.send(json.dumps({'type': 'req', 'id': request, 'method': 'history.get',
                                'params': {'session_id': session, 'page_idx': 1}}))
        deadline = time.monotonic() + 10
        messages = []
        while time.monotonic() < deadline:
            response = json.loads(socket.recv(timeout=max(.1, deadline - time.monotonic())))
            if response.get('type') == 'res' and response.get('id') == request:
                if not response.get('ok'): raise RuntimeError('Original native history query failed')
                continue  # accepted is only a gateway ACK; history follows as events.
            payload = response.get('payload') or {}
            if payload.get('session_id') != session: continue
            if payload.get('event_type') == 'chat.error' or payload.get('status') == 'error':
                raise RuntimeError('Original native history stream failed')
            if payload.get('event_type') != 'history.message': continue
            if isinstance(payload.get('message'), dict): messages.append(payload['message'])
            if payload.get('status') == 'done': return {'messages': messages}
    raise TimeoutError('Original native history response was not received')


def verify(config_path, report_path):
    config_path, report_path = Path(config_path).resolve(), Path(report_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('state') != 'session_received': raise RuntimeError('Use a previously confirmed native notification only')
    options = config['host']['business_tools'][0]['options']
    queue = WebNotifications(options, home=config_path.parent / 'unused-readonly', profile='personal', base_dir=config_path.parent)
    client = queue.client; client.store_id = client.probe()
    notice = {'messageId': report['notificationRef'], 'sender': report['senderAgent'], 'title': 'IoA真实收信验证', 'text': report['text'],
              'group': uuid.uuid5(uuid.NAMESPACE_URL, encode([report['receiverAgent'], report['interactionId']])).hex}
    if fingerprint(notice) != report['contentDigest']: raise RuntimeError('Original source/body does not match its receipt')
    first = client.get(notice)
    if first['status'] != 'session_received' or first['sessionId'] != report['sessionId']:
        raise RuntimeError('Reconcile the original native result; no replay was attempted')
    if client.deliver(notice) != first: raise RuntimeError('Duplicate did not restore the original receipt')
    try: client.deliver({**notice, 'text': notice['text'] + 'changed'})
    except RuntimeFault as exc:
        if exc.code != 'ID_CONFLICT': raise
    else: raise RuntimeError('Changed original body was accepted')
    original_store = client.store_id
    client.store_id = uuid.uuid4().hex
    try: client.get(notice)
    except RuntimeFault as exc:
        if exc.code != 'STORE_CONFLICT': raise
    else: raise RuntimeError('Changed original receipt store was accepted')
    finally: client.store_id = original_store
    history = read_history(client.url, first['sessionId'])
    messages = history.get('messages')
    if not isinstance(messages, list) or len(messages) != 1 or report['text'] not in json.dumps(messages, ensure_ascii=False):
        raise RuntimeError('Expected exactly one original native history record')
    return {'status': 'verified', 'sessionId': first['sessionId'], 'nativeHistoryRecords': len(messages),
            'sameIdSameBody': 'original_receipt', 'differentBody': 'ID_CONFLICT', 'differentStore': 'STORE_CONFLICT', 'read': 'unknown'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.config, args.report), ensure_ascii=False))


if __name__ == '__main__': main()
