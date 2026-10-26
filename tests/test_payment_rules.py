"""User-side retry preserves signed acceptance and prevents duplicate or premature payment."""
from decimal import Decimal
import pytest
from _support import prepare,fetch_when_ready

def test_insufficient_then_pay_installment(platform,providers,local_client):
    c=local_client;d,t,cd,a=prepare(c,providers)
    with pytest.raises(ValueError,match='before'): c.pay(cd,1,confirmed=True)
    fetch_when_ready(c,cd)
    balance=platform.wallets.balance(c.account['id'])
    platform.grant(c.account['id'],-balance,by='admin',note='payment test')
    with pytest.raises(Exception): c.accept(cd,1,confirmed=True)
    assert c.client.card(cd)['signatures']['7'] and not platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd)
    platform.grant(c.account['id'],100,by='admin',note='payment retry')
    assert c.pay(cd,1,confirmed=True)['status']=='paid'
    before=platform.wallets.balance(c.account['id'])
    assert c.pay(cd,1,confirmed=True)['status']=='requires-reconciliation'
    assert platform.wallets.balance(c.account['id'])==before
