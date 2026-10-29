"""Generic skill paths: three formation modes, bounded scopes and schema-addressed messages."""
import json
import time
import pytest
from conftest import wait_for
from ioalab_sdk.messaging import MailboxClient
from _support import PASSWORD


@pytest.mark.parametrize('formation',['preapproval','countersign','proposal'])
def test_generic_primitives_with_authorized_scope(platform,providers,local_client,formation):
    c=local_client
    d=next(v for v in providers.selected if v.get('formation','preapproval')==formation)
    url=providers.public_url+'/a2a/providers/svc-'+d['id']
    template=c.templates(url)[0]
    scope=c.authorize('User-selected professional deliverable',100,proposal_fee_limit=10,confirmed=True)
    spec={d['deliverable']['name']:d.get('sample_spec',{})}
    materials={v['name']:(b'x'*10,d.get('sample_inputs',{}).get(v['name'],{})) for v in d.get('inputs',[])}
    args=dict(materials=materials,budget=100,scope_id=scope['scopeId'])
    if formation=='proposal':
        quote=c.request(template['templateDigest'],spec,confirmed=True,**args)
        cd=quote['cardDigest'];c.adopt(cd,confirmed=True)
    else:
        cd=c.draft(template['templateDigest'],spec,**args)['cardDigest']
        assert c.inspect(cd)['scopeId']==scope['scopeId']
        c.sign(cd,confirmed=True)
    assert c.scope(scope['scopeId'])['selected']==cd
    c.start(cd,confirmed=True)
    until=time.monotonic()+40;periods=set()
    while time.monotonic()<until:
        result=c.poll(cd)
        for event in result['events']:
            assert event['type']!='step-request', 'These ordinary services do not require a real-name gate'
            if event['type']=='anchored':
                p=event['period'];c.fetch(cd,p);periods.add(p)
        if periods:break
        time.sleep(.1)
    assert periods
    for p in sorted(periods):
        assert c.verify(cd,p)['status']=='pass'
        with pytest.raises(ValueError,match='confirmation'):c.accept(cd,p)
        assert c.accept(cd,p,confirmed=True)['status']=='pass'
    wait_for(lambda:c.api('GET','/orders/'+cd)['state']=='completed')


def test_no_implicit_proposal_allowance(local_client):
    assert local_client.authorize('No paid proposal authorized',100,confirmed=True)['proposalFeeLimit']=='0'


def test_private_interaction_cli_claim_and_complete(platform,local_client,tmp_path,monkeypatch,capsys):
    from _support import new_user
    from ioalab_sdk import LabClient
    from ioalab_sdk.message_cli import main
    user=new_user(platform);home=tmp_path/'receiver'
    identity=LabClient(platform.s.base_url,user['username'],PASSWORD,home);identity.close()
    receiver=MailboxClient(platform.s.base_url,user['username'],PASSWORD,home)
    sender=MailboxClient(local_client.base,local_client.username,PASSWORD,local_client.home)
    try:
        schema={'type':'object','properties':{'notice':{'type':'string'}},'required':['notice'],'additionalProperties':False}
        receiver.publish('notice.receive',{'inputSchema':schema,'allowedSenders':[local_client.username]})
        definition=sender.interfaces(user['username'])[0]
        message=sender.send(user['username'],definition['name'],{'notice':'arrived safely'},revision=definition['revision'],idempotency_key='one-notice')
        receiver.sync()
        for k,v in {'IOALAB_BASE_URL':platform.s.base_url,'IOALAB_USERNAME':user['username'],'IOALAB_PASSWORD':PASSWORD,'IOALAB_HOME':str(home)}.items():monkeypatch.setenv(k,v)
        main(['claim','--args',json.dumps({'mid':message['id']})]);claim=json.loads(capsys.readouterr().out)
        assert claim['lease'] and claim['message']['payload']=={'notice':'arrived safely'}
        main(['complete','--args',json.dumps({'mid':message['id'],'lease':claim['lease'],'result':{'status':'received'}})])
        assert json.loads(capsys.readouterr().out)['state']=='completed'
        assert sender.get(message['id'])['result']=={'status':'received'}
    finally:receiver.close();sender.close()


def test_compatibility_workflow_does_not_complete_after_first_period():
    from ioalab_sdk.workflow import Workflow
    class Client:
        def card(self,cd):return {'derived':{'periods':[{'amount':20},{'amount':30}]}}
    class Fake:
        client=Client()
        def require_confirmation(self,value):assert value is True
        def accept(self,cd,period,**kw):return {'status':'pass','voucher':{'amount':20 if period==1 else 30}}
    w=Workflow(Fake());task={'taskId':'two-period','cardDigest':'card','gate':'acceptance','period':1,'report':{'status':'pass'},'files':{}}
    w.load=lambda _:task;w.save=lambda t:dict(t)
    first=w.run('advance',task_id='two-period',confirmed=True)
    assert first['gate']=='working' and 'report' not in first
    task.update(gate='acceptance',period=2)
    second=w.run('advance',task_id='two-period',confirmed=True)
    assert second['gate']=='completed' and set(second['payments'])=={'1','2'}
