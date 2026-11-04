"""Readiness uses public HTTP/WS/SDK observations, never plan-supplied booleans."""
import json
import os
from pathlib import Path
import subprocess
import uuid

import httpx
from websockets.sync.client import connect

from .bootstrap import ROOT, deployment


def sample_budget(client, receipt):
    response=client.get('/api/wallet',cookies={'ioalab_session':receipt['cookie']})
    response.raise_for_status()
    from decimal import Decimal,InvalidOperation
    try:
        amount=Decimal(str(response.json()['balance']))
        return amount.is_finite() and amount>=Decimal('500')
    except (ValueError,KeyError,InvalidOperation):return False


def query(method):
    identifier = uuid.uuid4().hex
    with connect('ws://127.0.0.1:8080/ws?user_id=trial',open_timeout=3,close_timeout=2) as socket:
        socket.send(json.dumps({'type':'req','id':identifier,'method':method,'params':{}}))
        for _ in range(100):
            message = json.loads(socket.recv(timeout=5))
            if message.get('id') == identifier and message.get('type') == 'res':
                if message.get('ok') is not True:raise RuntimeError('Original native query rejected')
                return message.get('payload',message.get('data',{}))
    raise RuntimeError('Original native response missing')


def main():
    from ioalab_sdk.runtime.ipc import LocalClient
    from ioalab_sdk.runtime.config import RuntimeConfig
    from ioalab_services.coordinator import status
    config = RuntimeConfig.load(ROOT/'assistant/config.json')
    live = LocalClient(config.home).call('runtime.status')['data']
    service_state = status(deployment())
    receipt=json.loads((ROOT/'control/probe-session.json').read_text('utf-8'))
    with httpx.Client(base_url='http://127.0.0.1:8750',trust_env=False,timeout=3) as participant:
        sufficient=sample_budget(participant,receipt)
    with httpx.Client(base_url='http://127.0.0.1:8750',trust_env=False,timeout=3) as client:
        core = json.loads((ROOT/'core/environment.json').read_text())
        response = client.post('/api/admin/login',json={'password':core['IOALAB_ADMIN_PASSWORD']})
        response.raise_for_status()
        orders = client.get('/api/admin/orders');orders.raise_for_status()
        intents = client.get('/api/admin/delegation-intents');intents.raise_for_status()
        stats = client.get('/api/admin/components');stats.raise_for_status()
        clean = orders.json() == [] and intents.json() == [] and stats.json()['payment']['transfers_total'] == 0
    response = httpx.get('http://127.0.0.1:8080/',timeout=3,trust_env=False)
    response.raise_for_status()
    installed = query('skills.installed')
    sessions = query('session.list')
    native_config = query('config.get')
    a2ui_enabled = isinstance(native_config,dict) and native_config.get('a2ui_enabled') in (True,'true')
    # Unexpected public schemas fail readiness, rather than assuming an empty list.
    native_sessions = sessions.get('sessions') if isinstance(sessions,dict) else None
    # Skill names are public metadata; do not read chat or configuration content.
    skill_loaded = 'ioalab-delegate' in json.dumps(installed)
    # Physical/local readiness is distinct from Linux/browser acceptance. A deployment
    # must provide a reviewed acceptance receipt baked into the image after the
    # original UI/WS/A2UI and real Skill->SDK loop have actually passed.
    acceptance = Path('/opt/trial/native-acceptance.json')
    native_accepted = False
    if acceptance.is_file():
        receipt = json.loads(acceptance.read_text())
        native_accepted = all(receipt.get(key) is True for key in ('originalUiProxy','originalWsProxy','a2ui','skillSdkCall'))
    print(json.dumps({'nativeWeb':response.is_success and native_accepted,
        'a2uiEnabled':a2ui_enabled,
        'nativeWs':isinstance(sessions,dict) and native_accepted,'skillLoaded':skill_loaded,
        'sdkConnected':live['platformConnected'] and all(item['executionReady'] for item in live['identities']),
        'servicesReady':len(service_state.get('accounts',[])) == 1 and all(
            not row.get('error') and len(row.get('services',[])) == 4 and all(
                not item.get('error') and item.get('current',{}).get('localExecution',{}).get('executionReady') is True
                and item.get('current',{}).get('indexReady') is True and item.get('current',{}).get('templateReady') is True
                for item in row['services']) for row in service_state.get('accounts',[])),
        'cleanInitialState':clean and native_sessions == [],'sampleBudgetAvailable':sufficient}))


if __name__ == '__main__':main()
