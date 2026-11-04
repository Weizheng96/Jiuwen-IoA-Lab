"""Rolling expiry and persistent active-template selection across service restarts."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from a2c.codec import fmt_time, parse_time
from ioalab_sdk.provider import ProviderAgent, DurableProvider
from ioalab_sdk.store import LocalStore
from ioalab_sdk.templates import template_from_definition


def test_template_validity_is_relative_beyond_2027(providers):
    d=providers.selected[2]
    at=datetime(2031,6,1,tzinfo=timezone.utc)
    t=template_from_definition(d,packages=None,payment_provider={'id':'urn:pay'},now=at)
    assert parse_time(t['validUntil'])==at+timedelta(days=30)


def test_renew_and_reuse_with_old_templates_preserved(tmp_path):
    at=datetime(2031,6,1,tzinfo=timezone.utc)
    store=LocalStore(tmp_path/'provider.sqlite')
    protocol=SimpleNamespace(now=lambda:at)
    calls=[]
    def publish(draft):
        calls.append(draft)
        t={**draft,'templateDigest':'sha256:'+str(len(calls))*64}
        store.put('templates',t['templateDigest'],t)
        return t
    protocol.publish=publish
    agent=ProviderAgent.__new__(ProviderAgent)
    agent.store=store;agent.provider=protocol
    agent.definition={'formation':'proposal','deliverable':{'name':'x'},'pricing':{'proposalFee':0}}
    refresh=[]
    agent.account=SimpleNamespace(config={'pay':'urn:pay'},api=lambda *args:refresh.append(args))
    agent.registration_id='registered-service'
    first=agent.publish()
    agent.refresh_template()
    assert len(calls)==1 and len(refresh)==1
    # Persisted same-definition identity is reused by a new service instance.
    second=ProviderAgent.__new__(ProviderAgent);second.__dict__.update(agent.__dict__)
    assert second.publish()==first and len(calls)==1
    at+=timedelta(days=24)
    second.refresh_template()
    assert len(calls)==2 and len(refresh)==2
    assert len(store.all('templates'))==2
    assert first['validUntil']==fmt_time(datetime(2031,7,1,tzinfo=timezone.utc))
    assert parse_time(second.template['validUntil'])==at+timedelta(days=30)
    assert store.get('metadata','activeTemplate')==second.template['templateDigest']
    view=SimpleNamespace(store=store,now=lambda:at)
    reply=DurableProvider.op_template_get(view,{},None,None)
    assert reply.body['templates']==[second.template]
    store.close()


def test_failed_endorsement_keeps_original_and_retries(tmp_path):
    from a2c.objects import object_digest
    at=datetime(2031,6,1,tzinfo=timezone.utc)
    store=LocalStore(tmp_path/'provider.sqlite')
    old={'templateDigest':'sha256:'+'a'*64,'validUntil':fmt_time(at-timedelta(days=1))}
    definition={'formation':'proposal','deliverable':{'name':'x'}}
    store.put('templates',old['templateDigest'],old)
    store.put('metadata','activeTemplate',old['templateDigest'])
    store.put('metadata','definition',object_digest(definition))
    agent=ProviderAgent.__new__(ProviderAgent);agent.store=store;agent.definition=definition
    def fail(_): raise RuntimeError('market unavailable')
    agent.provider=SimpleNamespace(now=lambda:at,publish=fail)
    agent.account=SimpleNamespace(config={'pay':'urn:pay'})
    with pytest.raises(RuntimeError,match='market unavailable'):agent.publish()
    assert store.get('templates',old['templateDigest'])==old
    assert store.get('metadata','activeTemplate')==old['templateDigest']
    reply=DurableProvider.op_template_get(SimpleNamespace(store=store,now=lambda:at),{},None,None)
    assert reply.body['templates']==[]
    store.close()


def test_real_market_renewal_updates_index_and_preserves_snapshot(platform,providers):
    from a2c.codec import utcnow
    from a2c.template import template_digest
    from a2c.slots import verify_template
    agent=providers.agents['svc-'+providers.selected[0]['id']]
    old=agent.provider.publish(template_from_definition(agent.definition,packages=agent.account,
        payment_provider={'id':agent.account.config['pay'],'jwksUri':agent.account.config['pay']+'/.well-known/jwks.json'},now=utcnow()-timedelta(days=29)))
    agent.store.put('metadata','activeTemplate',old['templateDigest'])
    agent.refresh_template()
    new=agent.template
    assert new['templateDigest']!=old['templateDigest']
    verify_template(old,agent.provider.keys);verify_template(new,agent.provider.keys)
    assert agent.provider.template(old['templateDigest'])==old
    assert platform.market.market.store.get('snapshots',old['templateDigest'])['template']==old
    public=agent.provider.op_template_get({},None,None).body['templates']
    assert [t['templateDigest'] for t in public]==[new['templateDigest']]
    card=platform.discovery.get_card(agent.registration_id)
    assert [t['digest'] for t in card['a2c']['templates']]==[new['templateDigest']]
