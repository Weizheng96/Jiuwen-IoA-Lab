"""Core has public market/payment endpoints and no participant execution endpoints."""
from pathlib import Path
import httpx
FORBIDDEN=('托管','代收代付','账户余额','清算','结算通道')

def test_no_forbidden_words():
    root=Path(__file__).resolve().parents[1]
    for p in (root/'src/platform/ioalab').rglob('*'):
        if p.suffix in ('.py','.md','.html','.js') and '__pycache__' not in p.parts:
            text=p.read_text(encoding='utf-8')
            assert not any(w in text for w in FORBIDDEN),str(p)

def test_a2x_routes_not_mounted(platform):
    paths=[getattr(r,'path','') for r in platform.app.routes]
    assert '/a2a' in paths
    assert not any(p.startswith('/api/build') or p.startswith('/api/providers') for p in paths)

def test_provider_execution_stays_outside_core_while_agent_card_is_public(platform,client,tmp_path):
    from test_resident_delegation import participants
    with participants(platform,tmp_path,formation='countersign',fee=0) as (provider,assistant,host,template,task):
        identity=provider.identities[0]
        response=client.get('/a2a/agents/'+identity.agent_id+'/.well-known/agent-card.json')
        assert response.status_code==200
        interfaces=response.json()['supportedInterfaces']
        assert any(item['url']==identity.protocol_id+'/a2a' and item['protocolBinding']=='JSONRPC'
                   for item in interfaces)
        assert provider.connection.connected and provider.modules[0].runtime is provider
        for name in ('client','provider','assistant','swarm'):
            assert not hasattr(platform,name)
        for role in ('market','pay'):
            assert client.get('/a2a/'+role+'/.well-known/agent-card.json').status_code==200


def test_core_has_no_participant_runtime(platform,client):
    assert not hasattr(platform,'assistants') and not hasattr(platform,'services')
    assert set(platform.router.apps)=={'market','pay','agents'}
    # The new agents mount publishes keys and gates unpublished service calls;
    # it is a transport/identity gateway, not a participant executor in the core.
    assert client.post('/a2a/agents/unpublished',json={}).json()['error']['code']=='not_ready'
    assert not hasattr(platform,'register_assistant')
    for path in ['/api/assistant','/api/needs','/api/v1/delegations','/api/v1/cards/sign','/api/admin/traffic']:
        assert client.post(path,json={}).status_code in (404,405)
    for row in platform.db.rows("SELECT id FROM users WHERE kind='isc'"):
        assert not platform.db.one("SELECT id FROM agents WHERE owner_id=?",row['id'])
