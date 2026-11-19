"""Public deadline recovery and legacy-schema preservation."""
import json
import hashlib
import time
import uuid

import pytest

from ioalab_sdk.runtime import ConfirmationDeadline,RuntimeFault,RuntimeStore
from ioalab_sdk.runtime.confirmations import Confirmations
from test_user_confirmations import binding,approve


def legacy_pending_fixture(manager,reference):
    """External old-version pending artifact, never a fabricated user decision."""
    from ioalab_sdk.runtime import encode
    item=manager.get(reference,detail=True)
    payload=encode({'binding':item['binding'],'questions':item['questions'],'ttl':900})
    fingerprint=hashlib.sha256(payload.encode()).hexdigest()
    with manager.store.transaction() as conn:
        now=manager.clock()
        conn.execute('UPDATE runtime_confirmations SET binding=?,fingerprint=?,expires=?,created=? WHERE id=?',
                     (payload,fingerprint,now-1,now-901,reference))
    return manager.get(reference)


@pytest.mark.parametrize('soft',[False,True])
@pytest.mark.parametrize('from_form',[False,True])
def test_legacy_expired_pending_requires_same_object_and_a_new_real_decision(tmp_path,soft,from_form):
    path=tmp_path/'runtime.sqlite';store=RuntimeStore(path,'http://local',['actor'],owner_id='owner')
    clock=[1000.];manager=Confirmations(store,clock=lambda:clock[0]);actual=binding()
    from contextlib import nullcontext
    from ioalab_sdk.runtime.forms import _form_scope
    with _form_scope({'reference':'original-form'}) if from_form else nullcontext():
        old=manager.ensure(actual)
    clock[0]=2000.
    assert manager.get(old['reference'])['status']=='expired'
    deadline=ConfirmationDeadline(response_due_at=1500.) if soft else ConfirmationDeadline(expires_at=10000.)
    new=manager.migrate_pending(old['reference'],old['bindingDigest'],actual,deadline=deadline)
    assert new['reference']==old['reference'] and new['bindingDigest']!=old['bindingDigest']
    assert new['status']=='pending'
    with pytest.raises(RuntimeFault,match='changed'):approve(manager,old)
    assert manager.find(actual)==new
    store.close()
    store=RuntimeStore(path,'http://local',['actor'],owner_id='owner');manager=Confirmations(store,clock=lambda:clock[0])
    try:
        assert manager.find(actual)==new
        assert manager.ensure(actual,deadline=deadline)==new
        assert manager.pending_marker(background_only=True)['count']==(0 if from_form else 1)
        approve(manager,new);manager.claim(new['reference'],actual)
    finally:store.close()


@pytest.mark.parametrize('decision',['approve','reject','used','answered','cancelled'])
def test_decided_or_consumed_legacy_records_never_migrate(tmp_path,decision):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    clock=[1000.];manager=Confirmations(store,clock=lambda:clock[0]);actual=binding()
    question=decision in ('answered','cancelled')
    value=manager.ensure(actual,kind='question' if question else 'approval',questions=[{'question':'原问题'}] if question else None)
    if decision=='cancelled':manager.cancel_question(value['reference'])
    elif decision=='answered':manager.decide(value['reference'],value['bindingDigest'],'answer',
        authority=manager.user_channel(),source='real-user',answers=[{'custom_input':'原用户答复'}])
    else:
        manager.decide(value['reference'],value['bindingDigest'],'reject' if decision=='reject' else 'approve',
            authority=manager.user_channel(),source='real-user')
        if decision=='used':manager.claim(value['reference'],actual)
    clock[0]=2000.;before=manager.get(value['reference'],detail=True)
    try:
        with pytest.raises(RuntimeFault,match='undecided'):
            manager.migrate_pending(value['reference'],value['bindingDigest'],actual,deadline=ConfirmationDeadline(expires_at=10000.))
        assert manager.get(value['reference'],detail=True)==before
    finally:store.close()


def test_legacy_notnull_schema_preserves_all_original_columns(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    # External legacy database fixture: migration itself must preserve each
    # original serialized field, including decision source and consumption.
    store.db.execute('''CREATE TABLE runtime_confirmations(
      id TEXT PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,kind TEXT NOT NULL,
      binding TEXT NOT NULL,fingerprint TEXT NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,
      answer TEXT,source TEXT,used_by TEXT,created REAL NOT NULL)''')
    actual=binding();payload=json.dumps({'binding':actual,'questions':[],'ttl':900})
    original=('original','actor',actual['operationId'],'approval',payload,'digest',1900.,'approved',None,'real-user',actual['operationId'],1000.)
    store.db.execute('INSERT INTO runtime_confirmations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',original)
    try:
        manager=Confirmations(store,clock=lambda:1500.)
        assert tuple(store.db.execute('SELECT * FROM runtime_confirmations').fetchone())==original
        assert manager.get('original')['source']=='real-user'
        soft=manager.ensure(binding(),deadline=ConfirmationDeadline(response_due_at=2000.))
        assert soft['expiresAt'] is None
    finally:store.close()


def test_real_grant_and_material_authorizations_survive_client_fifteen_minutes(platform,tmp_path,monkeypatch):
    import copy
    import test_resident_delegation as assembly
    from test_resident_delegation import participants
    from test_assistant_role import user_decision
    from ioalab_sdk.runtime.ipc import LocalClient
    original=assembly.template_from_definition
    def with_materials(value,**kwargs):
        value=copy.deepcopy(value);value['inputs']=[{'name':'source','description':'原审查材料','reviewBeforeSigning':True}]
        return original(value,**kwargs)
    monkeypatch.setattr(assembly,'template_from_definition',with_materials)
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        rpc=LocalClient(assistant.config.home)
        fresh=rpc.call('assistant.delegation.prepare',{'purpose':'原scope期限','budget':500,'proposalFeeLimit':3})['data']['taskRef']
        op=str(uuid.uuid4());args={'taskRef':fresh}
        pending=rpc.call('assistant.delegation.authorize',args,operation_id=op)
        ref=pending['data']['confirmationRef']
        monkeypatch.setattr(assistant.confirmations,'clock',lambda:time.time()+1000)
        assert assistant.confirmations.get(ref)['status']=='pending'
        user_decision(assistant,ref)
        assert rpc.call('assistant.delegation.authorize',args,operation_id=op)['status']=='completed'
        args={'taskRef':fresh,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
              'spec':{'result':{}},'materials':{'source':{'text':'原材料'}}}
        op=str(uuid.uuid4());pending=rpc.call('assistant.delegation.request',args,operation_id=op)
        assert pending['status']=='waiting',pending
        ref=pending['data']['confirmationRef']
        monkeypatch.setattr(assistant.confirmations,'clock',lambda:time.time()+2000)
        assert assistant.confirmations.get(ref)['status']=='pending'
        user_decision(assistant,ref)
        assert rpc.call('assistant.delegation.request',args,operation_id=op)['status']=='accepted'
        # Old material waits have no original scope deadline snapshot. A scope
        # renewal must never be mistaken for proof of that historical expiry.
        oldop=str(uuid.uuid4());old=rpc.call('assistant.delegation.request',args,operation_id=oldop)
        oldref=old['data']['confirmationRef'];before=legacy_pending_fixture(assistant.confirmations,oldref)
        role=assistant.modules[0];key='quote:'+old['data']['quoteRef']
        record=role.store.checkpoint(role.trading.namespace,key);record.pop('materialDeadline')
        role.store.checkpoint_set(role.trading.namespace,key,record)
        renewal=str(uuid.uuid4());renewargs={'taskRef':fresh}
        pending=rpc.call('assistant.delegation.renew',renewargs,operation_id=renewal)
        user_decision(assistant,pending['data']['confirmationRef'])
        assert rpc.call('assistant.delegation.renew',renewargs,operation_id=renewal)['status']=='completed'
        assert rpc.call('assistant.delegation.request',args,operation_id=oldop)['status']=='rejected'
        assert assistant.confirmations.get(oldref)==before
        # A different real operation already registered this grant's revision.
        task=rpc.call('assistant.delegation.prepare',{'purpose':'原grant已被别op完成','budget':20,'proposalFeeLimit':1})['data']['taskRef']
        oldop=str(uuid.uuid4());grantargs={'taskRef':task}
        old=rpc.call('assistant.delegation.authorize',grantargs,operation_id=oldop)
        before=legacy_pending_fixture(assistant.confirmations,old['data']['confirmationRef'])
        other=str(uuid.uuid4());pending=rpc.call('assistant.delegation.authorize',grantargs,operation_id=other)
        user_decision(assistant,pending['data']['confirmationRef'])
        assert rpc.call('assistant.delegation.authorize',grantargs,operation_id=other)['status']=='completed'
        assert rpc.call('assistant.delegation.authorize',grantargs,operation_id=oldop)['status']=='rejected'
        assert assistant.confirmations.get(before['reference'])==before
