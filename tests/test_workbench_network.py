"""Account projection privacy and payment evidence, through the actual HTTP API."""
import json
from decimal import Decimal
from _support import new_user, PASSWORD
from traffic import run_order


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
    assert all(set(s)=={'id','name','status','category','description','url'} for s in catalog)


def test_service_income_requires_real_card_payment(platform,providers,local_client,client):
    d=providers.selected[0];url=providers.public_url+'/a2a/providers/svc-'+d['id']
    provider=platform._agent_of_provider(url)
    result=run_order(local_client,d,url);cd=result['cardDigest']
    # An unrelated manual fee with the same reference is not A2C payment evidence.
    platform.wallets.transfer(local_client.account['id'],provider['owner_id'],Decimal('1'),purpose='fee',ref=cd)
    headers={'Authorization':'Bearer '+local_client.token}
    order=next(o for o in client.get('/api/workbench',headers=headers).json()['orders'] if o['card_digest']==cd)
    assert Decimal(order['paid'])==Decimal(order['total'])
    assert all(p['purpose'] in ('installment','deposit') for p in order['payments'])
    headers={'Authorization':'Bearer '+platform.auth.bearer(provider['owner_id'])}
    service=next(s for s in client.get('/api/workbench',headers=headers).json()['services'] if s['url']==url)
    expected=platform.db.rows("SELECT t.amount FROM transfers t JOIN orders o ON o.card_digest=t.ref WHERE o.provider_id=? AND t.payee_id=? AND t.payer_id=o.owner_id AND t.purpose IN ('deposit','installment')",url,provider['owner_id'])
    assert Decimal(service['earned'])==sum(Decimal(p['amount']) for p in expected)
    assert any(o['card_digest']==cd for o in service['orders'])
