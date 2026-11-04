"""Shared account helpers; tests never construct a platform-owned Client."""
import uuid
import time
COOKIE="ioalab_session"
PASSWORD="test-pass-1"
def username(): return "t_"+uuid.uuid4().hex[:10]
def new_user(pf,note="测试用户",kind="virtual"):
    return pf.register_user(username(),PASSWORD,kind=kind,display_name=note,note=note)
def as_admin(client,pf=None):
    return client.post('/api/admin/login',json={'password':'test'})

def prepare(local,providers,service_id=None,spec=None):
    d=next(d for d in providers.all_definitions if d['id']==service_id) if service_id else providers.selected[0]
    url=providers.public_url+'/a2a/providers/svc-'+d['id']
    t=local.templates(url)[0]
    cd=local.draft(t['templateDigest'],spec or {d['deliverable']['name']:d['sample_spec']})['cardDigest']
    signed=local.sign(cd,confirmed=True)
    assert not signed.get('declined'),signed
    local.start(cd,confirmed=True)
    return d,t,cd,providers.agents['svc-'+d['id']]

def fetch_when_ready(local,cd,timeout=20):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        state=local.poll(cd)
        for event in state['events']:
            if event['type']=='step-request': local.answer_step(cd,True,confirmed=True)
            if event['type']=='anchored': return local.fetch(cd,event['period'])
        time.sleep(.05)
    raise AssertionError('Delivery did not arrive')
