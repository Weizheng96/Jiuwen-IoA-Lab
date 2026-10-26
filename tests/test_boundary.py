"""Core has public market/payment endpoints and no participant execution endpoints."""
from pathlib import Path
import httpx
FORBIDDEN=('托管','代收代付','账户余额','清算','结算通道')

def test_no_forbidden_words():
    root=Path(__file__).resolve().parents[1]
    for p in (root/'ioalab').rglob('*'):
        if p.suffix in ('.py','.md','.html','.js') and '__pycache__' not in p.parts:
            text=p.read_text(encoding='utf-8')
            assert not any(w in text for w in FORBIDDEN),str(p)

def test_a2x_routes_not_mounted(platform):
    paths=[getattr(r,'path','') for r in platform.app.routes]
    assert '/a2a' in paths
    assert not any(p.startswith('/api/build') or p.startswith('/api/providers') for p in paths)

def test_provider_endpoint_is_in_separate_public_process(platform,providers,client):
    assert client.get('/a2a/providers/no-provider/.well-known/agent-card.json').status_code==404
    for role in ('market','pay'):
        assert client.get('/a2a/'+role+'/.well-known/agent-card.json').status_code==200
    agent=next(iter(providers.agents.values()))
    assert httpx.get(agent.url+'/.well-known/agent-card.json',trust_env=False).status_code==200
