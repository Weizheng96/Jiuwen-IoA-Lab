"""Real HTTP protocol loop for named services; explicit sample production only in this test."""
import threading
import time
from decimal import Decimal
import pytest
from conftest import free_port,wait_for
from ioalab_sdk.scope import amount


@pytest.mark.parametrize('candidate_key',['kuaida','mashangfei'])
def test_scene_verifier_rejects_mismatched_spec_and_broken_source_structure(candidate_key):
    """Explicit fixture source runs only inside WASM, never as real production."""
    from copy import deepcopy
    import json
    from scenario import nail_services
    from a2c.codec import digest_bytes
    from a2c.verifier.package import build,load
    from a2c.verifier.runner import run
    definition=next(d for _,d in nail_services() if d['id']=='nail-'+candidate_key)
    verifier=definition['verifier']
    manifest={'id':verifier['id'],'kind':'script','runtime':'cpython-3.13-wasi','entry':'verify:verify','reads':verifier['reads']}
    blob=build({'verify.py':verifier['source']},manifest)
    reference={'id':manifest['id'],'digest':digest_bytes(blob),'uri':'ioalab://pkg/'+digest_bytes(blob)}
    package=load(blob,reference,'script')
    spec={**definition['sample_spec'],**{key:domain['const'] for key,domain in definition['deliverable']['specSchema'].items() if 'const' in domain}}
    values={'spec.booking.'+key:value for key,value in spec.items()}
    def verify(data):
        return run(package,target='booking',values=values,files={'booking.json':json.dumps(data,ensure_ascii=False).encode('utf-8')},period=1)
    good=deepcopy(definition['sample']);baseline=verify(good)
    assert baseline.status=='pass' and len(baseline.checks)==len(verifier['criteria'])
    for field in ('open','close'):
        wrong=deepcopy(good);wrong[field]+=1;result=verify(wrong)
        assert result.status=='fail' and result.checks[0]['ok'] is False
    for field,value in (('name','与合同不同'),('minutes',15)):
        wrong=deepcopy(good);wrong['services'][0][field]=value;result=verify(wrong)
        assert result.status=='fail' and result.checks[0]['ok'] is False
    for manifest_text in ('invalid JSON, deliberately long enough',json.dumps({'pages':['pages/missing/missing']})):
        wrong=deepcopy(good);wrong['miniProgram']['app.json']=manifest_text;result=verify(wrong)
        assert result.status=='fail' and result.checks[4]['ok'] is False
    wrong=deepcopy(good);wrong.pop('engineSource');assert verify(wrong).status=='fail'


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


def test_named_flow_three_proposals_four_gates_and_real_acceptance(platform,local_client,tmp_path,monkeypatch):
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
        monkeypatch.setenv('IOALAB_MIN_QUOTES','3')
        scope=c.authorize('小美美甲预约，最多2人并可取消',500,3,confirmed=True)
        sid=scope['scopeId'];quotes=[]
        assert scope['minimumQuotes']==3
        for key in ('lingxi','kuaida','mashangfei'):
            url=base+'/a2a/providers/svc-nail-'+key
            template=c.templates(url)[0]
            d=defs['nail-'+key]
            args=dict(template_digest=template_digest(template),spec={'booking':{**d['sample_spec'],'shop':'小美美甲店'}},budget=500,scope_id=sid)
            with pytest.raises(ValueError,match='requires request'):c.draft(**args)
            assert len(c.scope(sid)['requests'])==len(quotes)
            with pytest.raises(ValueError,match='confirmation'):c.request(**args)
            result=c.request(**args,confirmed=True)
            assert c.request(**args,confirmed=True)==result  # Cached, not a second paid proposal.
            changed={**args,'spec':{'booking':{**d['sample_spec'],'shop':'不同店铺'}}}
            with pytest.raises(ValueError,match='different terms'):c.request(**changed,confirmed=True)
            quotes.append(result)
            assert result['quoteProgress']['receivedQuotes']==len(quotes)
            assert result['quoteProgress']['readyForConfirmation']==(len(quotes)==3)
            if len(quotes)<3:
                with pytest.raises(ValueError,match='Comparison policy'):c.adopt(result['cardDigest'],confirmed=True)
                assert not c.client.card(result['cardDigest'])['signatures']['1']
        assert [q['derived']['total'] for q in quotes]==[239,259,299]
        cached=c.request(**args,confirmed=True)
        assert cached['quoteProgress']['receivedQuotes']==3 and cached['quoteProgress']['readyForConfirmation']
        assert sum(Decimal(r['fee']) for r in c.scope(sid)['requests'].values())==3
        # Same faulty implementation: five base checks pass, extra cross-slot checks reject it.
        from copy import deepcopy
        faulty=deepcopy(defs['nail-mashangfei']['sample'])
        needle="sum(x['slot']==slot for x in self.rows.values()) >= self.capacity"
        assert needle in faulty['engineSource']
        faulty['engineSource']=faulty['engineSource'].replace(needle,"len(self.rows) >= self.capacity")
        for index,key in ((1,'kuaida'),(2,'mashangfei')):
            agent=host.agents['svc-nail-'+key]
            report=agent.selftest(quotes[index]['cardDigest'],faulty)
            assert [r['check'] for r in report['checks'][0]]==defs['nail-'+key]['verifier']['criteria']
            assert report['status']==('pass' if key=='kuaida' else 'fail'),report
            if key=='mashangfei':assert any(not r['ok'] and r['check']=='不同时段独立计数' for r in report['checks'][0])
        observer=host.agents['svc-nail-lingxi']
        observer.provider.model=lambda request,files:{'hasBooking':True,'hasCancellation':True,'hasCapacityControl':True}
        observation=observer.selftest(quotes[0]['cardDigest'],faulty)
        assert observation['status']=='pass'  # Declared observation double; no delivery execution by L2.
        assert [r['check'] for r in observation['checks'][0]]==defs['nail-lingxi']['verifier']['criteria']
        for key in ('lingxi','kuaida','mashangfei'):
            template=c.templates(base+'/a2a/providers/svc-nail-'+key)[0]
            assert all(name in template['deliverables'][0]['description'] for name in defs['nail-'+key]['verifier']['criteria'])
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
        assert [r['check'] for r in report['checks'][0]]==defs['nail-mashangfei']['verifier']['criteria']
        assert not c.client.card(cd)['signatures']['7']
        with pytest.raises(ValueError,match='confirmation'):c.accept(cd,1)
        receipt=c.accept(cd,1,confirmed=True);assert receipt['status']=='pass'
        wait_for(lambda: c.api('GET','/orders/'+cd)['state']=='completed',20)
        assert Decimal(receipt['voucher']['amount'])==299
    finally:
        server.should_exit=True;thread.join(10)
        if prior is None:platform.market.market.models.pop('qwen-plus',None)
        else:platform.market.market.models['qwen-plus']=prior
