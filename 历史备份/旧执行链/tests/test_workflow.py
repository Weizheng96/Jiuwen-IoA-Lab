"""Deterministic human gates through real HTTP/A2C; model and production are explicit test doubles."""
from pathlib import Path
import threading
import time
import pytest
from conftest import free_port,wait_for


@pytest.fixture
def nail_host(platform,tmp_path):
    import uvicorn
    from serve import ServiceHost,create_app
    old=platform.market.market.models.get('qwen-plus')
    platform.market.market.models['qwen-plus']=lambda request,files:{'hasBooking':True,'hasCancellation':True,'hasCapacityControl':True}
    port=free_port();host=ServiceHost(platform=platform.s.base_url,public_url=f'http://127.0.0.1:{port}',data=tmp_path/'nail-services',stub=True,scenario='nail-booking')
    server=uvicorn.Server(uvicorn.Config(create_app(host),host='127.0.0.1',port=port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    wait_for(lambda:host.ready or host.error,40);assert host.ready,host.error
    yield host
    server.should_exit=True;thread.join(10)
    if old is None:platform.market.market.models.pop('qwen-plus',None)
    else:platform.market.market.models['qwen-plus']=old


def test_delegate_drives_real_proposals_and_stops_at_each_human_gate(platform,local_client,nail_host):
    c=local_client
    platform.grant(c.account['id'],1000,by='admin',note='explicit workflow test credit')
    plan=Path(__file__).resolve().parents[1]/'examples/nail-booking/plan.json'
    initial=c.delegate('begin',need='小美美甲店：每时段两人，可以取消，预算500虚拟币',budget=500,plan_file=str(plan),spec={'shop':'小美美甲店','capacity':2})
    task=initial['taskId']
    assert initial['gate']=='authorization' and initial['proposalFeeLimit']=='3'
    assert not c.store.all('scopes') and not c.store.all('cards')
    assert initial['excluded']==[]
    assert not c.store.all('templates')
    again=c.delegate('begin',need=initial['need'],budget=500,plan_file=str(plan),spec={'shop':'小美美甲店','capacity':2})
    assert again['taskId']==task
    assert c.delegate('status')['tasks'][0]['taskId']==task
    with pytest.raises(ValueError,match='confirmation'):c.delegate('advance',task_id=task)
    proposed=c.delegate('advance',task_id=task,confirmed=True)
    assert proposed['gate']=='contract' and len(proposed['quotes'])==3
    assert {'微页通','极速APP工坊','云构工作室','尊享定制'} <= {x['provider'] for x in proposed['excluded']}
    assert {q['provider'] for q in proposed['quotes']}=={'灵犀小程序','快搭建站','码上飞'}
    assert proposed['recommended']['provider']=='码上飞'
    assert proposed['recommended']['totalCost']=='302'
    cd=proposed['cardDigest']
    assert not c.client.card(cd)['signatures']['1']
    assert c.delegate('status',task_id=task)['cardDigest']==cd
    step=c.delegate('advance',task_id=task,confirmed=True,timeout=5)
    for _ in range(20):
        if step['gate']=='step':break
        step=c.delegate('poll',task_id=task,timeout=1)
    assert step['gate']=='step' and step['stepRequest']['step']=='realname_registration'
    assert not c.client.card(cd)['signatures']['5']
    with pytest.raises(ValueError,match='confirmation'):c.delegate('advance',task_id=task)
    accepted=c.delegate('advance',task_id=task,confirmed=True,timeout=5)
    for _ in range(20):
        if accepted['gate']=='acceptance':break
        accepted=c.delegate('poll',task_id=task,timeout=1)
    assert accepted['gate']=='acceptance' and len(accepted['report']['checks'][0])==10
    assert not c.client.card(cd)['signatures']['7']
    with pytest.raises(ValueError,match='Use delegate'):c.accept(cd,1,confirmed=True)
    with pytest.raises(ValueError,match='Use delegate'):c.fetch(cd,1)
    with pytest.raises(ValueError,match='confirmation'):c.delegate('advance',task_id=task)
    paid=c.delegate('advance',task_id=task,confirmed=True)
    assert paid['gate']=='completed' and paid['payment']['voucher']['amount']==299
    assert c.delegate('advance',task_id=task,confirmed=True)==paid


def test_plan_cannot_inject_executable_actions(local_client,tmp_path):
    import json
    path=tmp_path/'plan.json';path.write_text(json.dumps({'format':'ioalab.delegate.plan','code':'print(1)'}),encoding='utf-8')
    with pytest.raises(ValueError,match='declarative'):local_client.delegate('begin',need='test',budget=500,plan_file=str(path))
