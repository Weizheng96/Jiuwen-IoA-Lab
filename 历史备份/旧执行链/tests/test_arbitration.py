"""Failed local acceptance yields a signed report and independent market recomputation, never payment."""
import base64
from decimal import Decimal
from pathlib import Path
import httpx
import pytest
from _support import prepare,fetch_when_ready

def test_arbitration_recompute(platform,providers,local_client):
    a=providers.agents['svc-wechat-posts'];a.definition['deliver_anyway']=True
    before=platform.wallets.balance(local_client.account['id'])
    try:
        d,t,cd,agent=prepare(local_client,providers,'wechat-posts',{'posts':{'shop':'另一个绝不匹配的店铺','theme':'周末'}})
        files=fetch_when_ready(local_client,cd)
        assert local_client.verify(cd,1)['status']=='fail'
        assert local_client.accept(cd,1,confirmed=True)['status']=='fail'
        assert not local_client.client.card(cd)['signatures']['7']
        assert not platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd)
        assert platform.wallets.balance(local_client.account['id'])==before
        with pytest.raises(httpx.HTTPStatusError):
            local_client.api('POST','/arbitrations',{'card':local_client.client.card(cd),'period':1,'ground':'acceptance-failed',
                                                  'files':{'posts.json':base64.b64encode(b'{}').decode()}})
        ruling=local_client.arbitrate(cd,1,confirmed=True)
        assert ruling['signature'] and ruling['liable']=='provider' and ruling['evidence']['recompute']=='fail'
        assert platform.market.ledger()[cd]['outcome']=='arbitrated'
        assert platform.wallets.balance(local_client.account['id'])==before
        with pytest.raises(Exception): agent.provider.close(cd)
    finally: a.definition.pop('deliver_anyway',None)
