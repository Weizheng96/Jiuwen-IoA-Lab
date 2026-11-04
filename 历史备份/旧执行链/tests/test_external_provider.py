"""Same public registration, discovery, packages and completion for independent services."""
import httpx
import pytest
from traffic import run_order

def test_external_provider_end_to_end(platform,providers,local_client):
    agent=next(iter(providers.agents.values()))
    row=platform._agent_of_provider(agent.url)
    assert row and row['owner_id']==agent.account.user['id'] and row['status']=='online'
    assert platform.discovery.get_card(row['id'])['url']==agent.url
    assert platform.db.one('SELECT 1 FROM certifications WHERE agent_id=?',agent.url)
    mine=agent.account.api('GET','/provider/mine')
    assert any(r['id']==row['id'] for r in mine)
    templates=local_client.templates(agent.url)
    assert templates[0]['signatures']['market'] and templates[0]['provider']==agent.url
    assert run_order(local_client,agent.definition,agent.url)['status']=='accepted'
    from conftest import wait_for
    wait_for(lambda:platform.market.reputation(agent.url)['completed']>=1)
    assert platform.market.reputation(agent.url)['completed']>=1
    with pytest.raises(httpx.HTTPStatusError):
        agent.account.api('POST','/provider/register',{'url':'http://127.0.0.1:1','name':'unreachable','ap2Jwk':agent.provider.ap2_key.jwk})
    with pytest.raises(httpx.HTTPStatusError):
        agent.account.api('POST','/provider/register',{'url':agent.url+'/bad','name':'invalid','ap2Jwk':{'kty':'OKP'}})


def test_third_party_direct_a2c_provider(platform,local_client,tmp_path):
    import importlib.util
    from pathlib import Path
    from conftest import free_port,wait_for
    from _support import new_user,PASSWORD
    path=Path(__file__).resolve().parents[1]/'examples/external_provider/provider.py'
    spec=importlib.util.spec_from_file_location('external_example',path)
    example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)
    port=free_port();user=new_user(platform)
    service=example.ExternalProvider(platform=platform.s.base_url,public_url=f'http://127.0.0.1:{port}',data_dir=tmp_path/'external',stub=True)
    try:
        row=service.start(user['username'],PASSWORD,port=port,host='127.0.0.1',category='cat_writing')
        assert platform.discovery.get_card(row['id'])['url']==service.public_url
        result=run_order(local_client,example.DEFINITION,service.public_url)
        assert result['status']=='accepted'
        wait_for(lambda:result['cardDigest'] in service.closed)
        assert platform.market.reputation(service.public_url)['completed']==1
    finally: service.stop()
