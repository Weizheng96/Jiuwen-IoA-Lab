"""Candidate comparison uses real A2C/AP2/WS and a non-production host double."""
import copy

import pytest

from a2c.card import card_digest
from ioalab_sdk.adapters import ProtocolStore
from ioalab_sdk.runtime.ipc import LocalClient
from test_resident_delegation import participants,uid,QuoteHost
from ioalab_sdk.assistant.role import AssistantRole
from ioalab_sdk.runtime.resident import ResidentRuntime
from test_resident_contracts import candidate
from conftest import wait_for


def compared(client,reference):
    value=client.call('operations.get',{'reference':reference})
    assert value['status']=='completed',value
    job=value['data']
    assert job['status'] not in ('failed','unknown','rejected'),job
    return job['data'] if job['status']=='completed' else None


def compare(client,task,refs,operation=None):
    operation=operation or uid();args={'taskRef':task,'quoteRefs':refs}
    started=client.call('assistant.delegation.compare',args,operation_id=operation)
    assert started['status'] in ('accepted','completed'),started
    reference=started['data']['reference']
    wait_for(lambda:compared(client,reference))
    return compared(client,reference),reference,operation,args


def test_mixed_quotes_use_original_terms_source_and_signed_reputation_without_new_effects(platform,tmp_path):
    source="def verify(ctx):\n    raise RuntimeError('Review must not execute this source')\n"
    with participants(platform,tmp_path/'one',capacity=2,verifier_source=source) as (p,a,host,t,task):
        with participants(platform,tmp_path/'two',formation='countersign',fee=0,verifier_source=source) as (p2,unused,host2,t2,other_task):
            one=candidate(a,p,t,task);two=candidate(a,p2,t2,task);three=candidate(a,p,t,task)
            client=LocalClient(a.config.home);before=len(platform.db.rows('SELECT * FROM transfers'))
            turns=len(host.turns),len(host2.turns);confirmations=len(a.confirmations.list())
            result,reference,operation,args=compare(client,task,[one['quoteRef'],two['quoteRef'],three['quoteRef']])
            assert result['quoteCount']==3 and result['serviceCount']==2
            assert [row['state'] for row in result['candidates']]==['offered','unsigned','offered']
            stored=a.scheduler.records(a.identities[0].agent_id,('assistant.compare',),reference=reference['id'])[0]
            assert all('initial' not in item['record'] for item in stored['payload']['quotes'])
            assert len(result['verifierPackages'])==1
            package=result['verifierPackages'][0]
            assert package['complete'] and source in package['files'].values()
            assert all(row['verifierEvidence'][0]['reviewRef']==package['reviewRef'] for row in result['candidates'])
            for original,row in zip((one,two,three),result['candidates']):
                assert row['cardDigest']==card_digest(original['card'])
                assert row['derived']==original['card']['derived']
                assert row['reputation']['status']=='verified'
                rep=row['reputation']['record'];assert rep['completed']==0 and rep['successRate'] is None and rep['signature']
                assert original['card']['signatures']['1'] is None
            assert (len(host.turns),len(host2.turns))==turns
            assert len(a.confirmations.list())==confirmations and len(platform.db.rows('SELECT * FROM transfers'))==before
            repeated=client.call('assistant.delegation.compare',args,operation_id=operation)
            assert repeated['data']['reference']==reference and compared(client,reference)==result
            changed=client.call('assistant.delegation.compare',{**args,'quoteRefs':args['quoteRefs'][::-1]},operation_id=operation)
            assert changed['status']=='rejected' and changed['error']['code']=='id_conflict'
            inspected=client.call('assistant.delegation.inspect',{'service':p.identities[0].agent_id})
            assert inspected['data']['reputation']['status']=='verified'
            owner=LocalClient(p.config.home).call('provider.reputation',{'profile':'service'})
            assert owner['status']=='completed' and owner['data']['reputation']['provider']==p.identities[0].protocol_id
            config=a.config;a.close()
            restored=ResidentRuntime(config,modules=(AssistantRole(),)).start()
            try:
                restored_client=LocalClient(config.home)
                replay=restored_client.call('assistant.delegation.compare',args,operation_id=operation)
                assert replay['status']=='completed' and replay['data']['reference']==reference
                assert compared(restored_client,reference)==result
            finally:restored.close()


@pytest.mark.parametrize('kind',['duplicate','unknown','another_task'])
def test_invalid_batch_creates_no_comparison_job(platform,tmp_path,kind):
    with participants(platform,tmp_path) as (p,a,host,t,task):
        q=candidate(a,p,t,task);client=LocalClient(a.config.home)
        refs=[q['quoteRef']];selected=task
        if kind=='duplicate':refs*=2
        elif kind=='unknown':refs.append(uid())
        else:selected=client.call('assistant.delegation.prepare',{'purpose':'another purpose','budget':50,'proposalFeeLimit':0})['data']['taskRef']
        before=len(a.scheduler.records(a.identities[0].agent_id,('assistant.compare',)))
        response=client.call('assistant.delegation.compare',{'taskRef':selected,'quoteRefs':refs})
        assert response['status']=='rejected' and response['error']['code'] in ('invalid_input','forbidden')
        assert len(a.scheduler.records(a.identities[0].agent_id,('assistant.compare',)))==before


@pytest.mark.parametrize('damage',['amount','request','missing_source','scope'])
def test_comparison_and_first_confirmation_share_original_evidence_checks(platform,tmp_path,damage):
    with participants(platform,tmp_path) as (p,a,host,t,task):
        q=candidate(a,p,t,task);client=LocalClient(a.config.home);trading=a.modules[0].trading
        key='quote:'+q['quoteRef'];record=a.store.checkpoint(trading.namespace,key)
        protocol=ProtocolStore(a.store,a.identities[0].agent_id,q['interactionId']);ref=card_digest(q['card'])
        original=protocol.get('cards',ref)
        if damage=='missing_source':protocol.delete('cards',ref)
        elif damage=='request':
            broken=copy.deepcopy(original);broken['request']['spec']['result']={'changed':True};protocol.put('cards',ref,broken)
        else:
            broken=copy.deepcopy(record)
            if damage=='amount':broken['result']['card']['derived']['total']=99
            else:broken['result']['card']['delegationScopeDigest']='sha256:'+'0'*64
            a.store.checkpoint_set(trading.namespace,key,broken)
        before=len(a.confirmations.list());paid=len(platform.db.rows('SELECT * FROM transfers'))
        result,_,_,_=compare(client,task,[q['quoteRef']])
        assert result['candidates'][0]['state']=='invalid'
        response=client.call('assistant.delegation.confirm',{'quoteRef':q['quoteRef']})
        assert response['status']=='rejected',response
        assert len(a.confirmations.list())==before and len(platform.db.rows('SELECT * FROM transfers'))==paid


def test_large_source_is_explicitly_incomplete_and_never_executed(platform,tmp_path):
    source='#'+('x'*34000)+"\ndef verify(ctx):\n    raise RuntimeError('must not execute')\n"
    with participants(platform,tmp_path,verifier_source=source) as (p,a,host,t,task):
        q=candidate(a,p,t,task);before=len(host.turns)
        result,_,_,_=compare(LocalClient(a.config.home),task,[q['quoteRef']])
        package=result['verifierPackages'][0]
        assert not package['complete'] and package['omitted'][0]['reason']=='size_limit'
        assert not package['files'] and len(host.turns)==before


def test_expired_offer_keeps_declared_omissions_but_cannot_create_confirmation(platform,tmp_path,monkeypatch):
    from datetime import timedelta
    from a2c.codec import utcnow
    class OmissionHost(QuoteHost):
        def run(self,turn):
            self.turns.append(turn);turn.on_session('original-omission-session')
            turn.on_tool('provider.task.quote',{
                'acceptance':{'result':{'verifiers':{'script':self.verifier},'notCovered':['External certification is excluded']}},
                'acceptWithin':{'result':'PT10M'},'lineItems':{'result':9}},'original-quote-call')
            from ioalab_sdk.runtime.host import HostResult
            return HostResult('completed',turn.session_id,{})
    with participants(platform,tmp_path,agent_host=OmissionHost()) as (p,a,host,t,task):
        q=candidate(a,p,t,task);client=LocalClient(a.config.home)
        future=utcnow()+timedelta(days=2)
        monkeypatch.setattr('ioalab_sdk.assistant.candidates.utcnow',lambda:future)
        result,_,_,_=compare(client,task,[q['quoteRef']])
        row=result['candidates'][0]
        assert row['state']=='expired'
        assert row['deliverables'][0]['acceptance']['notCovered']==['External certification is excluded']
        before=len(a.confirmations.list())
        rejected=client.call('assistant.delegation.confirm',{'quoteRef':q['quoteRef']})
        assert rejected['status']=='rejected' and rejected['error']['code']=='expired'
        assert len(a.confirmations.list())==before


def test_unverifiable_reputation_is_not_reported_as_zero_history(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (p,a,host,t,task):
        q=candidate(a,p,t,task)
        def corrupt(account):
            original=account.api
            def api(method,path,body=None):
                value=original(method,path,body)
                if path.startswith('/providers/reputation?'):
                    value={**value,'completed':123,'successRate':1.0}
                return value
            monkeypatch.setattr(account,'api',api)
        corrupt(a.account);corrupt(p.account)
        result,_,_,_=compare(LocalClient(a.config.home),task,[q['quoteRef']])
        reputation=result['candidates'][0]['reputation']
        assert reputation['status']=='invalid' and 'record' not in reputation
        rejected=LocalClient(p.config.home).call('provider.reputation',{'profile':'service'})
        assert rejected['status']=='rejected' and rejected['data'] is None


def test_offer_expiring_during_package_read_is_not_left_current(platform,tmp_path,monkeypatch):
    from datetime import timedelta
    from a2c.codec import utcnow
    from ioalab_sdk.assistant.candidates import PackageReview
    with participants(platform,tmp_path) as (p,a,host,t,task):
        q=candidate(a,p,t,task);clock=[utcnow()]
        monkeypatch.setattr('ioalab_sdk.assistant.candidates.utcnow',lambda:clock[0])
        original=PackageReview.read
        def completed_after_expiry(self,kind,ref):
            value=original(self,kind,ref)
            clock[0]+=timedelta(days=2)
            return value
        monkeypatch.setattr(PackageReview,'read',completed_after_expiry)
        result,_,_,_=compare(LocalClient(a.config.home),task,[q['quoteRef']])
        assert result['candidates'][0]['state']=='expired'
