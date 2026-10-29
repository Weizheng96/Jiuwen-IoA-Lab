"""Real HTTP protocol loop for named services; explicit sample production only in this test."""
import threading
import time
from decimal import Decimal
import pytest
from conftest import free_port,wait_for
from ioalab_sdk.scope import amount


def test_scope_requires_explicit_confirmation_and_bounded_values(local_client):
    c=local_client
    with pytest.raises(ValueError,match='confirmation'): c.authorize('预约工具',500)
    for value in ('NaN','Infinity',-1):
        with pytest.raises(ValueError): amount(value)
    with pytest.raises(ValueError): c.authorize('预约工具',2,3,confirmed=True)
    with pytest.raises(ValueError): c.authorize('预约工具',500,3,valid_days=8,confirmed=True)
    s=c.authorize('预约工具',500,3,confirmed=True)
    assert c.scope(s['scopeId'])==s
    s['expiresAt']=time.time()-1;c.store.put('scopes',s['scopeId'],s)
    with pytest.raises(ValueError,match='expired'): c.live_scope(s['scopeId'])


def test_named_flow_three_proposals_four_gates_and_real_acceptance(platform,local_client,tmp_path):
    import uvicorn
    from serve import ServiceHost,create_app
    from scenario import nail_services
    from a2c.template import template_digest
    c=local_client;port=free_port();base=f'http://127.0.0.1:{port}'
    platform.grant(c.account['id'],1000,by='admin',note='explicit test virtual credit')
    # An explicitly declared model observation test double; no language-model calls.
    prior=platform.market.market.models.get('qwen-plus')
    platform.market.market.models['qwen-plus']=lambda request,files: {'hasBooking':True,'hasCancellation':True,'hasCapacityControl':True}
    host=ServiceHost(platform=platform.s.base_url,public_url=base,data=tmp_path/'services',stub=True,scenario='nail-booking')
    server=uvicorn.Server(uvicorn.Config(create_app(host),host='127.0.0.1',port=port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        wait_for(lambda:host.ready or host.error,40);assert host.ready,host.error
        defs=dict((d['id'],d) for _,d in nail_services())
        assert {d['name'] for d in defs.values()}=={'微页通','极速APP工坊','云构工作室','尊享定制','灵犀小程序','快搭建站','码上飞'}
        assert c.reputation(base+'/a2a/providers/svc-nail-mashangfei')['completed']==0
        scope=c.authorize('小美美甲预约，最多2人并可取消',500,3,confirmed=True)
        sid=scope['scopeId'];quotes=[]
        for key in ('lingxi','kuaida','mashangfei'):
            url=base+'/a2a/providers/svc-nail-'+key
            template=c.templates(url)[0]
            d=defs['nail-'+key]
            args=dict(template_digest=template_digest(template),spec={'booking':{**d['sample_spec'],'shop':'小美美甲店'}},budget=500,scope_id=sid)
            with pytest.raises(ValueError,match='confirmation'):c.request(**args)
            result=c.request(**args,confirmed=True)
            assert c.request(**args,confirmed=True)==result  # Cached, not a second paid proposal.
            changed={**args,'spec':{'booking':{**d['sample_spec'],'shop':'不同店铺'}}}
            with pytest.raises(ValueError,match='different terms'):c.request(**changed,confirmed=True)
            quotes.append(result)
        assert [q['derived']['total'] for q in quotes]==[239,259,299]
        assert sum(Decimal(r['fee']) for r in c.scope(sid)['requests'].values())==3
        cd=quotes[-1]['cardDigest']
        with pytest.raises(ValueError,match='confirmation'):c.adopt(cd)
        c.adopt(cd,confirmed=True)
        with pytest.raises(ValueError,match='one contract'):c.adopt(quotes[1]['cardDigest'],confirmed=True)
        c.start(cd,confirmed=True)
        deadline=time.monotonic()+15;event=None
        while time.monotonic()<deadline:
            response=c.poll(cd)
            event=next((x for x in response['events'] if x.get('stepRequest')),None)
            if event:break
            time.sleep(.05)
        # The exact A2C task status may wrap the request under type=authorization.
        rec=host.agents['svc-nail-mashangfei'].store.get('cards',cd)
        assert rec['steps'] and not rec['steps'][0].get('answer')
        assert not c.client.card(cd)['signatures']['5']
        with pytest.raises(ValueError,match='confirmation'):c.answer_step(cd,True)
        c.answer_step(cd,True,confirmed=True)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            result=c.poll(cd)
            if any(e.get('type')=='anchored' for e in result['events']):break
            time.sleep(.05)
        c.fetch(cd,1)
        c.fetch(cd,1)  # A retry must not append a second slot-6 receipt.
        assert len(c.client.card(cd)['signatures']['6'])==1
        report=c.verify(cd,1)
        assert report['status']=='pass',report
        assert len(report['checks'][0])==10
        assert not c.client.card(cd)['signatures']['7']
        with pytest.raises(ValueError,match='confirmation'):c.accept(cd,1)
        receipt=c.accept(cd,1,confirmed=True);assert receipt['status']=='pass'
        wait_for(lambda: c.api('GET','/orders/'+cd)['state']=='completed',20)
        assert Decimal(receipt['voucher']['amount'])==299
    finally:
        server.should_exit=True;thread.join(10)
        if prior is None:platform.market.market.models.pop('qwen-plus',None)
        else:platform.market.market.models['qwen-plus']=prior
