"""Account projection privacy and payment evidence, through the actual HTTP API."""
import json
from decimal import Decimal
from _support import new_user, PASSWORD
from test_resident_delegation import participants
from ioalab_sdk.runtime.ipc import LocalClient


def test_personal_network_does_not_disclose_other_activity(platform,client,local_client):
    other=new_user(platform)
    marker='another-users-private-search'
    platform.bus.emit('observation','discovery.search',sender=other['id'],detail={'query':marker})
    local_client.observe('discovery.compared',reason='OWN-PRIVATE-COMPARISON')
    response=client.get('/api/workbench',headers={'Authorization':'Bearer '+local_client.token})
    assert response.status_code==200
    data=response.json()
    assert marker not in response.text and other['id'] not in response.text
    assert 'OWN-PRIVATE-COMPARISON' not in response.text  # no raw reported detail
    assert any(e['op']=='discovery.compared' for e in data['events'])
    assert all(set(e)<={'seq','at','op','card_digest','period','outcome','source','trust'} for e in data['events'])
    catalog=client.get('/api/catalog',headers={'Authorization':'Bearer '+local_client.token}).json()
    assert all(set(s)=={'id','name','status','category','description','url','published','indexReady','connected','templateAvailable','accepting','activeDelegations','maxActiveDelegations'} for s in catalog)


def test_public_service_uses_its_declaration_connection_and_current_admission_policy(platform,client,tmp_path):
    with participants(platform,tmp_path,formation='countersign',fee=0) as (provider,assistant,host,template,task):
        actor=provider.identities[0].agent_id
        headers={'Authorization':'Bearer '+platform.auth.bearer(provider.account.account['id'])}
        service=next(s for s in client.get('/api/workbench',headers=headers).json()['services'] if s['id']==actor)
        assert service['published'] and service['indexReady'] and service['connected'] and service['templateAvailable']
        assert service['status']=='online' and service['earned']=='0.00' and service['orders']==[]
        platform.wallets.transfer(assistant.account.account['id'],provider.account.account['id'],Decimal('1'),purpose='fee',ref='not-a-contract')
        assert next(s for s in client.get('/api/workbench',headers=headers).json()['services'] if s['id']==actor)['earned']=='0.00'
        paused=LocalClient(provider.config.home).call('provider.pause',{'profile':'service'})
        assert paused['status']=='completed'
        public=next(s for s in client.get('/api/catalog',headers=headers).json() if s['id']==actor)
        assert public['status']=='paused' and public['connected'] and public['accepting'] is False
        assert 'orders' not in public and 'owner_id' not in public
