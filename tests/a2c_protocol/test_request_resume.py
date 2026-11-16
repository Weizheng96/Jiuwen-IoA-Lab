"""Resume a prepared request without another submission, fee or task identity."""
import copy

import pytest

from a2c.common import Store
from a2c.errors import A2CError
from a2c.provider import Provider
from .support import MARKET, PROVIDER, ProposalPolicy, World


class PausedPolicy(ProposalPolicy):
    def __init__(self):
        super().__init__();self.calls=0;self.paused=True
    def draft(self,*args):
        self.calls+=1
        if self.paused:raise RuntimeError('the original policy round stopped before returning a decision')
        return super().draft(*args)


def prepared(tmp_path,monkeypatch=None):
    policy=PausedPolicy();world=World(policy=policy)
    world.provider.store=Store(tmp_path/'provider.json')
    template=world.publish('proposal')
    [template]=world.client.get_templates(PROVIDER,template['templateDigest'])
    request=world.client.draft_request(template,request_id='resume-original',spec=world.spec(),materials=world.materials(),
        signers={'slot1':'user','slot7':'user'},delegation_scope_digest='sha256:'+'d'*64)
    if monkeypatch is not None:
        monkeypatch.setattr(world.provider,'_materials',lambda *_args,**_kwargs:(_ for _ in ()).throw(RuntimeError('material review interrupted')))
    with pytest.raises(RuntimeError):world.client.submit_request(request)
    pending=world.provider.store.all('pending_requests');assert len(pending)==1
    task_id=next(iter(pending));assert world.provider.task(task_id).state=='working'
    assert len(world.psp.ledger)==1
    return world,policy,request,task_id


def restarted(world,tmp_path,policy):
    fresh=Provider(world.provider_signer,market=MARKET,keys=world.keys,transport=world.net,fetch=world.fetch,
        clock=world.clock,store=Store(tmp_path/'provider.json'),policy=policy,model=world.model)
    world.provider=fresh;world.net.register(PROVIDER,fresh)
    return fresh


def test_resume_after_restart_preserves_original_request_materials_fee_and_task(tmp_path,monkeypatch):
    world,policy,_,task_id=prepared(tmp_path)
    original=copy.deepcopy(world.provider.store.get('pending_requests',task_id));policy.paused=False
    fresh=restarted(world,tmp_path,policy)
    monkeypatch.setattr(fresh,'_materials',lambda *_args,**_kwargs:pytest.fail('Verified materials were reviewed again'))
    deferred=fresh.resume_request(task_id)
    assert deferred.task_id==task_id and policy.calls==1
    completed=fresh.finish(deferred)
    assert completed.task_id==task_id and completed.state=='completed' and policy.calls==2
    card=completed.body['card'];assert card['signatures']['2'] and card['signatures']['3'] and card['signatures']['1'] is None
    assert fresh.store.get('pending_requests',task_id)['body']==original['body']
    assert fresh.store.get('pending_requests',task_id)['files']==original['files']
    assert len(world.psp.ledger)==1 and len(fresh.tasks)==1
    with pytest.raises(A2CError) as error:fresh.resume_request(task_id)
    assert error.value.code=='out-of-order' and fresh.task(task_id).state=='completed' and policy.calls==2


def test_resume_requires_completed_material_review(tmp_path,monkeypatch):
    world,policy,_,task_id=prepared(tmp_path,monkeypatch)
    with pytest.raises(A2CError) as error:world.provider.resume_request(task_id)
    assert error.value.code=='out-of-order' and policy.calls==0


def test_resume_does_not_renew_the_original_response_window(tmp_path):
    world,policy,_,task_id=prepared(tmp_path);world.clock.advance(days=2)
    with pytest.raises(A2CError) as error:world.provider.resume_request(task_id)
    assert error.value.code=='deadline-passed' and policy.calls==1


def test_new_request_version_prevents_resuming_the_old_task(tmp_path):
    world,policy,request,task_id=prepared(tmp_path)
    template=world.client.template(request['templateDigest'])
    revision=world.client.draft_request(template,request_id=request['requestId'],version=2,spec=world.spec(),materials=world.materials(),
        signers={'slot1':'user','slot7':'user'},delegation_scope_digest='sha256:'+'d'*64)
    with pytest.raises(RuntimeError):world.client.submit_request(revision)
    with pytest.raises(A2CError) as error:world.provider.resume_request(task_id)
    assert error.value.code=='out-of-order' and policy.calls==2


@pytest.mark.parametrize('after_market',[False,True])
def test_returned_decision_and_unknown_signing_never_reenter_the_policy(tmp_path,monkeypatch,after_market):
    world,policy,_,task_id=prepared(tmp_path);policy.paused=False;send=world.net.send;calls=[]
    def unknown(target,op,*args,**kwargs):
        if target==MARKET and op=='card.endorse':
            calls.append(copy.deepcopy(args[0]))
            if after_market:send(target,op,*args,**kwargs)
            raise RuntimeError('market result is unknown')
        return send(target,op,*args,**kwargs)
    monkeypatch.setattr(world.net,'send',unknown)
    with pytest.raises(RuntimeError):world.provider.finish(world.provider.resume_request(task_id))
    assert len(calls)==1 and calls[0]['card']['signatures']['2'] and policy.calls==2
    with pytest.raises(A2CError) as error:world.provider.resume_request(task_id)
    assert error.value.code=='out-of-order' and len(calls)==1 and policy.calls==2


def test_pre_review_preparation_is_local_and_restores_original_request(tmp_path, monkeypatch):
    world, policy, request, tid = prepared(tmp_path, monkeypatch)
    fresh = restarted(world, tmp_path, policy); policy.paused = False
    original = copy.deepcopy(fresh.store.get('pending_requests', tid)); scopes = []
    with monkeypatch.context() as local:
        def forbidden(*a, **k): pytest.fail('preparation performed an execution effect')
        local.setattr(fresh, '_materials', forbidden); local.setattr(fresh, 'fetch', forbidden)
        local.setattr(fresh, 'model', forbidden); local.setattr(fresh.policy, 'draft', forbidden)
        local.setattr(world.net, 'send', forbidden)
        with pytest.raises(A2CError): fresh.resume_request(tid)
        deferred = fresh.resume_material_review(tid, guard=scopes.append)
        assert deferred.task_id == tid and deferred.state == 'working'
        assert fresh.store.get('pending_requests', tid) == original and policy.calls == 0
    result = fresh.finish(deferred)
    assert result.task_id == tid and result.state == 'completed' and policy.calls == 1
    assert len(scopes) == 2 and scopes[0] == scopes[1] and scopes[0].request_id == request['requestId']
    assert len(world.psp.ledger) == 1 and len(fresh.tasks) == 1
    with pytest.raises(A2CError): fresh.resume_material_review(tid, guard=scopes.append)


def test_pre_review_old_unsealed_record_refuses_without_mutation(tmp_path, monkeypatch):
    world, policy, _, tid = prepared(tmp_path, monkeypatch)
    fresh = restarted(world, tmp_path, policy); record = fresh.store.get('pending_requests', tid)
    record.pop('material_seal'); fresh.store.put('pending_requests', tid, record)
    before = copy.deepcopy(fresh.store.all('pending_requests'))
    with pytest.raises(A2CError): fresh.resume_material_review(tid, guard=lambda _: pytest.fail('unsealed proof admitted'))
    assert fresh.store.all('pending_requests') == before and policy.calls == 0


def test_pre_review_finish_rechecks_guard_before_materials(tmp_path, monkeypatch):
    world, policy, _, tid = prepared(tmp_path, monkeypatch); fresh = restarted(world, tmp_path, policy)
    checks = []
    def guard(scope):
        checks.append(scope)
        if len(checks) > 1: raise RuntimeError('original execution proof changed')
    deferred = fresh.resume_material_review(tid, guard=guard)
    with pytest.raises(RuntimeError): fresh.finish(deferred)
    assert fresh.task(tid).state == 'working'
    assert not fresh.store.get('pending_requests', tid)['reviewed'] and policy.calls == 0


def test_material_resume_retains_sealed_template_not_changed_store_value(tmp_path, monkeypatch):
    world, policy, request, tid = prepared(tmp_path, monkeypatch); policy.paused = False
    fresh = restarted(world, tmp_path, policy)
    template = fresh.template(request['templateDigest']); template['validUntil'] = '2000-01-01T00:00:00Z'
    fresh.store.put('templates', request['templateDigest'], template)
    result = fresh.finish(fresh.resume_material_review(tid, guard=lambda scope: None))
    assert result.state == 'completed' and policy.calls == 1 and len(world.psp.ledger) == 1


@pytest.mark.parametrize('fault', ['body', 'file', 'template', 'seal', 'deadline', 'terminal'])
def test_material_preparation_rejects_changed_original_snapshot(tmp_path, monkeypatch, fault):
    world, policy, _, tid = prepared(tmp_path, monkeypatch); fresh = restarted(world, tmp_path, policy)
    rec = fresh.store.get('pending_requests', tid)
    if fault == 'body': rec['body']['request']['version'] += 1
    elif fault == 'file': rec['files'][next(iter(rec['files']))] = 'Y2hhbmdlZA=='
    elif fault == 'template': rec['material_seal']['template']['validUntil'] = '2000-01-01T00:00:00Z'
    elif fault == 'seal': rec['material_seal']['bodyDigest'] = 'changed'
    elif fault == 'deadline': world.clock.advance(days=2)
    elif fault == 'terminal': rec['terminal'] = 'failed'
    fresh.store.put('pending_requests', tid, rec)
    with pytest.raises(A2CError): fresh.resume_material_review(tid, guard=lambda _: pytest.fail('invalid scope'))
    assert policy.calls == 0
