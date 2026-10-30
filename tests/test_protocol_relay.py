"""Actual A2C countersign over WS, with material consent and private file access."""
import sqlite3
from types import SimpleNamespace
import uuid

import httpx
import pytest
from a2c import objects
from a2c.card import build_card,build_request,build_proposal_card,card_digest
from a2c.codec import digest,digest_bytes,utcnow
from a2c.common import Directory,Store
from a2c.jws import sign
from a2c.provider import Provider,DefaultPolicy
from a2c.errors import A2CError,OUT_OF_ORDER
from a2c.slots import sign_template,sign_slot,verify_card
from ioalab_sdk.templates import template_from_definition
from ioalab_sdk.runtime.files_api import PrivateFiles
from test_service_publication import service,publication,submit
from test_delegation_admission import online,opened,configure,at,invoke
from test_delegation_scopes import authorization,register


def uid():return str(uuid.uuid4())


def template_for(platform,service,*,formation='countersign',revision=1):
    account,identity=service
    def upload(**args):return account.api('POST','/provider/packages',{'verifierId':args['verifier_id'],'source':args['source'],'reads':args.get('reads',[])})
    ref=upload(verifier_id='relay_test@1.0',source="def verify(ctx):\n    return {'pass': True}\n")
    raw=template_from_definition({'formation':formation,'deliverable':{'name':'result'},'pricing':{'price':'9','proposalFee':0},
         'inputs':[{'name':'brief','required':True,'reviewBeforeSigning':True,'specSchema':{}}],
         'verifier':{'id':ref['id'],'source':"def verify(ctx):\n    return {'pass': True}\n"}},packages=SimpleNamespace(put=upload),payment_provider=platform.payment_provider)
    signed=sign_template({**raw,'provider':identity.protocol_id},identity.signer,'provider',utcnow())
    response=submit(account,identity,publication(platform,identity,template=signed,revision=revision));response.raise_for_status()
    return response.json()['templates'][0],ref


def setup_counter(platform,client,local,service,template,ref):
    grant=register(client,local,authorization(local));grant.raise_for_status();scope=grant.json()['scopeDigest']
    card=build_card(template,card_id=uid(),client={'id':local.signer.id,'jwk':local.signer.jwk},
                    user={'id':local.user.id,'jwk':local.user.jwk},signers={'slot1':'user','slot7':'user'},spec={'result':{}},
                    inputs={'brief':{'digest':ref['digest']}},delegation_scope_digest=scope,payment_provider=platform.payment_provider)
    intent={'messageId':uid(),'kind':'delegation.intent','recipient':service[1].agent_id,'expiresAt':at(600),
            'body':{'formation':'countersign','templateDigest':template['templateDigest'],'scopeDigest':scope,'bindingDigest':card_digest(card),'card':card}}
    return scope,card,intent


def request_for(platform,local,service,template,ref,scope,card,intent,sender,receiver,*,commit=True):
    configure(receiver)
    proof=sign(digest({'scopeDigest':scope,'cardDigest':card_digest(card)}),local.user,'ioa-contract-selection',utcnow())
    sender.call('scope.select',{'scopeDigest':scope,'card':card,'proof':proof})
    binding=opened(sender,receiver,intent);held=receiver.call('admission.reserve',binding)
    signed=sign_slot(card,1,local.user,utcnow())
    if commit:receiver.call('admission.commit',{'admissionId':held['admissionId'],'card':signed})
    request={'messageId':uid(),'kind':'a2c.request','recipient':receiver.actor,'interactionId':binding['interactionId'],'expiresAt':at(300),
             'body':{'op':'card.countersign','admissionId':held['admissionId'],'body':{'card':signed},'files':{'brief':ref}}}
    return request,binding


@pytest.mark.parametrize('expire',[False,True])
def test_countersign_delivers_slot_one_before_review_and_commits_before_slot_two(platform,client,local_client,service,monkeypatch,expire):
    local=local_client;account,identity=service;template,_=template_for(platform,service)
    data=uid().encode();ref=PrivateFiles(local.session,local.signer.id).put('brief',data,uid())
    scope,card,intent=setup_counter(platform,client,local,service,template,ref)
    with online(client,local,service) as (sender,receiver):
        request,binding=request_for(platform,local,service,template,ref,scope,card,intent,sender,receiver,commit=False)
        request['body']['body']['informationAuthorization']=material_authorization(local,card)
        aid=request['body']['admissionId']
        assert receiver.call('admission.status',{'admissionId':aid})['state']=='held'
        sender.call('message.send',request)
        inbound=receiver.delivery(request['messageId'])['message']['body']['body']
        receiver.call('request.start',{'messageId':request['messageId'],'interactionId':binding['interactionId']})
        def result(body,task=None,state=None):
            return {'messageId':uid(),'kind':'a2c.result','recipient':sender.actor,'interactionId':binding['interactionId'],
                    'inReplyTo':request['messageId'],'expiresAt':at(300),'body':{'reply':{'body':body,'state':state,'taskId':task},'files':{}}}
        # Even a correctly signed slot 2 cannot enter the platform before commit.
        premature=sign_slot(inbound['card'],2,identity.signer,utcnow())
        assert receiver.call('message.send',result({'op':'card.countersign','card':premature}),ok=False)['code']=='not_ready'
        if expire:
            held=platform.db.one('SELECT hold_until FROM delegation_admissions WHERE id=?',aid)
            monkeypatch.setattr(platform.admissions,'clock',lambda:held['hold_until']+1)
        class Policy(DefaultPolicy):
            def review(self,t,c,materials):
                assert materials=={'brief':data} and c==inbound['card']
                reply=receiver.call('admission.commit',{'admissionId':aid,'card':c},ok=not expire)
                if expire:
                    assert reply['code']=='expired'
                    raise A2CError(OUT_OF_ORDER,'Original held admission expired during review')
                assert reply['state']=='committed'
                return None
        keys=Directory();keys.add(identity.protocol_id,{'keys':[identity.signer.jwk]});keys.add(platform.market_id,{'keys':[platform.market_signer.jwk]})
        provider=Provider(identity.signer,market=platform.market_id,keys=keys,transport=local.transport,fetch=account.fetch_package,store=Store(),policy=Policy())
        provider.store.put('templates',template['templateDigest'],template)
        reply=provider.handle('card.countersign',inbound,{'brief':data})
        if expire:
            assert reply.body['error']['code']==OUT_OF_ORDER and provider.store.get('cards',card_digest(card)) is None
        else:
            invalid={**reply.body,'card':{**reply.body['card'],'cardId':uid()}}
            assert receiver.call('message.send',result(invalid,reply.task_id,reply.state),ok=False)['code']=='invalid_input'
            assert reply.body['card']['signatures']['1']==inbound['card']['signatures']['1']
            assert verify_card(template,reply.body['card'],keys).effective is not None
        final=result(reply.body,reply.task_id,reply.state)
        receiver.call('message.send',final)
        assert sender.delivery(final['messageId'])['message']['body']['reply']['body']==reply.body


def material_authorization(local,card):
    return objects.signed(objects.new('information-authorization',inputs=['brief'],jwk=local.user.inline_jwk,
                                      cardDigest=card_digest(card)),local.user,utcnow())


def test_real_countersign_preserves_protocol_and_reads_only_explicitly_authorized_material(platform,client,local_client,service):
    local=local_client;account,identity=service;template,_=template_for(platform,service)
    sender_files=PrivateFiles(local.session,local.signer.id);receiver_files=PrivateFiles(account,identity.agent_id)
    data=b'private brief '+uid().encode();ref=sender_files.put('brief',data,uid())
    scope,card,intent=setup_counter(platform,client,local,service,template,ref)
    with online(client,local,service) as (sender,receiver):
        request,binding=request_for(platform,local,service,template,ref,scope,card,intent,sender,receiver)
        assert sender.call('message.send',request,ok=False)['code']=='forbidden'
        with pytest.raises(httpx.HTTPStatusError):receiver_files.get(ref)
        request['body']['body']['informationAuthorization']=material_authorization(local,card)
        sender.call('message.send',request)
        received=receiver.delivery(request['messageId'])['message']
        receiver.call('request.start',{'messageId':request['messageId'],'interactionId':binding['interactionId']})
        assert receiver_files.get(received['body']['files']['brief'])==data
        keys=Directory();keys.add(identity.protocol_id,{'keys':[identity.signer.jwk]});keys.add(platform.market_id,{'keys':[platform.market_signer.jwk]})
        provider=Provider(identity.signer,market=platform.market_id,keys=keys,transport=local.transport,fetch=account.fetch_package,store=Store())
        provider.store.put('templates',template['templateDigest'],template)
        reply=provider.handle('card.countersign',received['body']['body'],{'brief':data})
        assert 'card' in reply.body,reply.body
        assert verify_card(template,reply.body['card'],keys).effective is not None
        result={'messageId':uid(),'kind':'a2c.result','recipient':sender.actor,'interactionId':binding['interactionId'],
                'inReplyTo':request['messageId'],'expiresAt':at(300),'body':{'reply':{'body':reply.body,'state':reply.state,'taskId':reply.task_id},'files':{}}}
        receiver.call('message.send',result)
        returned=sender.delivery(result['messageId'])['message']['body']['reply']
        assert returned['body']==reply.body and returned['taskId']==reply.task_id
        assert card_digest(returned['body']['card'])==card_digest(card)
        assert receiver.call('message.send',result)['messageId']==result['messageId']


def test_original_protocol_progress_and_taskless_reply_keep_their_states(platform,client,local_client,service):
    local=local_client;account,identity=service;template,_=template_for(platform,service)
    data=uid().encode();ref=PrivateFiles(local.session,local.signer.id).put('brief',data,uid())
    scope,card,intent=setup_counter(platform,client,local,service,template,ref)
    with online(client,local,service) as (sender,receiver):
        request,binding=request_for(platform,local,service,template,ref,scope,card,intent,sender,receiver)
        request['body']['body']['informationAuthorization']=material_authorization(local,card)
        sender.call('message.send',request);receiver.delivery(request['messageId'])
        receiver.call('request.start',{'messageId':request['messageId'],'interactionId':binding['interactionId']})
        keys=Directory();keys.add(identity.protocol_id,{'keys':[identity.signer.jwk]});keys.add(platform.market_id,{'keys':[platform.market_signer.jwk]})
        provider=Provider(identity.signer,market=platform.market_id,keys=keys,transport=local.transport,fetch=account.fetch_package,store=Store())
        provider.store.put('templates',template['templateDigest'],template)
        first=provider.handle('card.countersign',request['body']['body'],{'brief':data},defer=True)
        def respond(reply):
            result={'messageId':uid(),'kind':'a2c.result','recipient':sender.actor,'interactionId':binding['interactionId'],
                    'inReplyTo':request['messageId'],'expiresAt':at(300),'body':{'reply':reply,'files':{}}}
            receiver.call('message.send',result)
            return sender.delivery(result['messageId'])['message']['body']['reply']
        progress={'body':first.body,'state':first.state,'taskId':first.task_id}
        assert respond(progress)==progress and first.state=='working'
        # A foreign task cannot replace the one assigned by the original SDK.
        different={'messageId':uid(),'kind':'a2c.result','recipient':sender.actor,'interactionId':binding['interactionId'],
                   'inReplyTo':request['messageId'],'expiresAt':at(300),'body':{'reply':{**progress,'taskId':'different-task'},'files':{}}}
        assert receiver.call('message.send',different,ok=False)['code']=='id_conflict'
        final=provider.finish(first)
        terminal={'body':final.body,'state':final.state,'taskId':final.task_id}
        assert respond(terminal)==terminal and final.state=='completed'
        different['messageId']=uid();different['body']['reply']=progress
        assert receiver.call('message.send',different,ok=False)['code']=='id_conflict'

    # A checkout is an original taskless Reply: state=None is not an invalid task state.
    # Use a separate real proposal admission so no countersign context is reused.
    proposal,_=template_for(platform,service,formation='proposal',revision=2)
    from a2c.common import Reply
    from a2c.card import request_digest
    # A new scope is needed because the counter scope has already selected its card.
    grant=register(client,local,authorization(local));grant.raise_for_status();scope2=grant.json()['scopeDigest']
    request=build_request(proposal,request_id=uid(),version=1,client={'id':local.signer.id,'jwk':local.signer.jwk},
                          signers={'slot1':'client','slot7':'client'},spec={'result':{}},inputs={'brief':{'digest':ref['digest']}},
                          delegation_scope_digest=scope2,payment_provider=platform.payment_provider)
    intent={'messageId':uid(),'kind':'delegation.intent','recipient':identity.agent_id,'expiresAt':at(600),
            'body':{'formation':'proposal','templateDigest':proposal['templateDigest'],'scopeDigest':scope2,'bindingDigest':request_digest(request),'request':request}}
    with online(client,local,service) as (sender,receiver):
        receiver.call('admission.configure',{'expectedRevision':1,'maxActiveDelegations':3,'accepting':True})
        binding=opened(sender,receiver,intent);held=receiver.call('admission.reserve',binding)
        checkout={'messageId':uid(),'kind':'a2c.request','recipient':receiver.actor,'interactionId':binding['interactionId'],'expiresAt':at(300),
                  'body':{'op':'ap2.checkout','admissionId':held['admissionId'],'body':{'purpose':'proposal-fee','templateDigest':proposal['templateDigest'],'requestDigest':request_digest(request)},'files':{}}}
        sender.call('message.send',checkout);receiver.delivery(checkout['messageId'])
        receiver.call('request.start',{'messageId':checkout['messageId'],'interactionId':binding['interactionId']})
        raw=Reply({'op':'ap2.checkout','checkoutJwt':'opaque-original-payee-output'})
        result={'messageId':uid(),'kind':'a2c.result','recipient':sender.actor,'interactionId':binding['interactionId'],
                'inReplyTo':checkout['messageId'],'expiresAt':at(300),'body':{'reply':{'body':raw.body,'taskId':raw.task_id,'state':raw.state},'files':{}}}
        receiver.call('message.send',result)
        assert sender.delivery(result['messageId'])['message']['body']['reply']['state'] is None
        assert platform.messaging.delivery.message(local.account['id'],sender.actor,checkout['messageId'])['requestState']=='completed'


def test_failed_message_commit_does_not_leak_a_material_read_grant(platform,client,local_client,service):
    local=local_client;account,identity=service;template,_=template_for(platform,service)
    ref=PrivateFiles(local.session,local.signer.id).put('brief',uid().encode(),uid())
    scope,card,intent=setup_counter(platform,client,local,service,template,ref)
    with online(client,local,service) as (sender,receiver):
        request,binding=request_for(platform,local,service,template,ref,scope,card,intent,sender,receiver)
        request['body']['body']['informationAuthorization']=material_authorization(local,card)
        platform.db.run("CREATE TRIGGER test_relay_commit BEFORE INSERT ON delivery_messages WHEN NEW.id='"+request['messageId']+"' BEGIN SELECT RAISE(ABORT,'test relay commit outage'); END")
        try:
            with pytest.raises(sqlite3.IntegrityError):invoke(platform,sender,'message.send',request)
        finally:platform.db.run('DROP TRIGGER test_relay_commit')
        assert platform.db.one('SELECT * FROM delegation_protocol_calls WHERE message=?',request['messageId']) is None
        with pytest.raises(httpx.HTTPStatusError):PrivateFiles(account,identity.agent_id).get(ref)
        sender.call('message.send',request)
        assert PrivateFiles(account,identity.agent_id).get(ref)


def test_new_service_cannot_bypass_admission_using_public_market_slot_three(platform,client,local_client,service):
    local=local_client;account,identity=service;template,verifier=template_for(platform,service,formation='proposal')
    grant=register(client,local,authorization(local));grant.raise_for_status();scope=grant.json()['scopeDigest']
    request=build_request(template,request_id=uid(),version=1,client={'id':local.signer.id,'jwk':local.signer.jwk},
                          signers={'slot1':'client','slot7':'client'},spec={'result':{}},inputs={'brief':{'digest':digest_bytes(b'input')}},
                          delegation_scope_digest=scope,payment_provider=platform.payment_provider)
    card=build_proposal_card(template,request,card_id=uid(),acceptance={'result':{'verifiers':{'script':verifier},'notCovered':[]}},
                             accept_within={'result':'PT10M'},line_items={'result':9})
    card=sign_slot(card,2,identity.signer,utcnow())
    keys=Directory();keys.add(identity.protocol_id,{'keys':[identity.signer.jwk]})
    assert verify_card(template,card,keys).ts[2]
    reply=platform.market.market.handle('card.endorse',{'card':card})
    assert reply.body['error']['code']=='out-of-order'
    assert platform.market.market.store.get('snapshots',card_digest(card)) is None
