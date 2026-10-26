"""Preserve full signed flow, amount and public observer regressions with a local client."""
from decimal import Decimal
from traffic import run_order
from conftest import wait_for

def test_order_flow(platform,providers,local_client):
    d=providers.selected[0];url=providers.public_url+'/a2a/providers/svc-'+d['id']
    before=platform.wallets.balance(local_client.account['id'])
    result=run_order(local_client,d,url);cd=result['cardDigest']
    wait_for(lambda:bool(platform.market.market.store.get('ledger',cd)))
    card=local_client.client.card(cd)
    assert all(len(card['signatures'][k])==1 for k in ('4','5','6','7'))
    amount=Decimal(str(card['derived']['total']))
    assert platform.wallets.balance(local_client.account['id'])==before-amount
    transfers=platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd)
    assert sum(Decimal(t['amount']) for t in transfers)==amount
    assert transfers[0]['payer_id']==local_client.account['id']
    assert local_client.store.get('cards',cd)['vouchers'][0]['purpose']=='installment'
    assert platform.market.reputation(url)['completed']>=1
    view=local_client.api('GET','/orders/'+cd)
    assert view['state']=='completed' and view['signatures']['7']==[True]
    assert [e['seq'] for e in view['events']]==sorted(e['seq'] for e in view['events'])
    assert {'order.started','order.fetched','order.verified','order.accepted'}<={e['op'] for e in view['events']}
