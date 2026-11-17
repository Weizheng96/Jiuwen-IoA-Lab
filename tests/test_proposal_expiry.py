"""Original signed proposals, atomic expiry and selection/closure competition."""
from datetime import datetime,timezone
import json

import pytest
from a2c.card import card_digest
from a2c.codec import digest,parse_duration,parse_time
from a2c.jws import header_of,sign
from ioalab.errors import LabError
from test_resident_delegation import participants,uid
from test_resident_contracts import candidate
from conftest import wait_for


def windows(template,card):
    response=parse_time(header_of(card['signatures']['3'])['a2c-ts'])+parse_duration(template['deadlines']['respondWithin'])
    return response.timestamp(),(response+parse_duration(template['deadlines']['startWithin'])).timestamp()


def answered(platform,quote):
    with platform.db.tx() as conn:
        return platform.delivery.protocol.proposal_finished_in(conn,quote['admissionId'],platform.delivery.protocol_history_in)


def selection(platform,assistant,card,at,monkeypatch):
    identity=assistant.identities[0];scope=card['delegationScopeDigest']
    payload={'scopeDigest':scope,'card':card,
        'proof':sign(digest({'scopeDigest':scope,'cardDigest':card_digest(card)}),identity.user,
            'ioa-contract-selection',datetime.fromtimestamp(at,timezone.utc))}
    monkeypatch.setattr(platform.scopes,'clock',lambda:at)
    return platform.scopes.prepare(assistant.account.account['id'],identity.agent_id,'scope.select',payload)


def notices(platform,quote):
    return platform.db.rows("SELECT recipient,envelope FROM delivery_messages WHERE interaction=? AND kind='interaction.closed'",quote['interactionId'])


def test_expired_unselected_proposal_releases_atomically_preserving_fee_and_originals(platform,client,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=1) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);wait_for(lambda:answered(platform,quote))
        response,end=windows(template,quote['card']);broker=platform.delivery
        fees=platform.db.rows('SELECT * FROM transfers WHERE ref=?',platform.db.one('SELECT binding FROM delegation_admissions WHERE id=?',quote['admissionId'])['binding'])
        assert len(fees)==1 and not notices(platform,quote)
        with platform.db.lock:
            prepared=selection(platform,assistant,quote['card'],response-1,monkeypatch)
            monkeypatch.setattr(platform.scopes,'clock',lambda:response+.01)
            with pytest.raises(LabError,match='selection window'):
                with platform.db.tx() as conn:platform.scopes.apply_in(conn,assistant.account.account['id'],uid(),prepared)
            monkeypatch.setattr(platform.admissions,'clock',lambda:end)
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',quote['admissionId'])['state']=='committed'
            monkeypatch.setattr(platform.admissions,'clock',lambda:end+.01)
            # Elapsed time is not closure authority. Reads must retain the offer
            # until the original maintenance transaction actually commits.
            pending=next(r for r in platform.contract_views.list() if r['admissionId']==quote['admissionId'])
            assert pending['state']=='offered' and not pending['effective']
            with monkeypatch.context() as fault:
                def interrupted(*args):raise OSError('Isolated interruption before reliable closure notices')
                fault.setattr(broker,'_closed_notices',interrupted)
                with pytest.raises(OSError):broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',quote['admissionId'])['state']=='committed'
            assert platform.db.one('SELECT state FROM delivery_interactions WHERE id=?',quote['interactionId'])['state']=='open'
            assert not notices(platform,quote)
            broker.cleanup();broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',quote['admissionId'])['state']=='released'
            assert platform.db.one('SELECT closed FROM delegation_intents WHERE origin=(SELECT origin FROM delegation_admissions WHERE id=?)',quote['admissionId'])['closed']==1
            assert platform.db.one('SELECT state FROM delivery_interactions WHERE id=?',quote['interactionId'])['state']=='closed'
            delivered=notices(platform,quote)
            assert {row['recipient'] for row in delivered}=={provider.identities[0].agent_id,assistant.identities[0].agent_id} and len(delivered)==2
            assert all(json.loads(row['envelope'])['body']['reason']=='proposal-expired' for row in delivered)
            assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',fees[0]['ref'])==fees
            assert platform.db.one('SELECT result FROM delegation_offers WHERE admission=?',quote['admissionId'])
            assert platform.market.market.store.get('ledger',card_digest(quote['card'])) is None
        for account in (assistant.account.account,provider.account.account):
            response=client.get('/api/workbench',headers={'Authorization':'Bearer '+platform.auth.bearer(account['id'])})
            assert response.status_code==200
            data=response.json();records=data['orders']+[r for service in data['services'] for r in service['orders']]
            shown=next(r for r in records if r['admissionId']==quote['admissionId'])
            assert shown['state']=='unformed-closed' and shown['phase']=='未成约 · 已结束'
            assert shown['effective'] is False and shown['proposalFeePaid']=='1.00' and shown['paid']=='0.00'
            assert shown['total']==str(quote['card']['derived']['total'])
        response=client.get('/api/admin/orders',headers={'Authorization':'Bearer '+platform.auth.token('admin')})
        shown=next(r for r in response.json() if r['admissionId']==quote['admissionId'])
        assert shown['state']=='unformed-closed' and shown['effective'] is False
        damaged=next(r for r in platform.contract_views.rows() if r['admission']==quote['admissionId'])
        damaged['card']='{}'  # An unavailable display copy is not closure proof.
        unavailable=platform.contract_views.view(damaged)
        assert unavailable['state']=='unknown' and unavailable['phase']=='候选合同副本待恢复'
        assert unavailable['total'] is None and unavailable['effective'] is False


def test_exact_response_deadline_selection_and_its_original_receipt_prevent_expiry(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=0) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);wait_for(lambda:answered(platform,quote))
        response,end=windows(template,quote['card'])
        with platform.db.lock:
            prepared=selection(platform,assistant,quote['card'],response-1,monkeypatch)
            monkeypatch.setattr(platform.scopes,'clock',lambda:response)
            operation=uid()
            with platform.db.tx() as conn:receipt=platform.scopes.apply_in(conn,assistant.account.account['id'],operation,prepared)
            assert receipt['selectedCardDigest']==card_digest(quote['card'])
            monkeypatch.setattr(platform.scopes,'clock',lambda:end+1)
            monkeypatch.setattr(platform.admissions,'clock',lambda:end+1)
            platform.delivery.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',quote['admissionId'])['state']=='committed'
            with platform.db.tx() as conn:
                recovered=platform.scopes.prepare(assistant.account.account['id'],assistant.identities[0].agent_id,'scope.select',json.loads(prepared.payload))
                assert platform.scopes.apply_in(conn,assistant.account.account['id'],operation,recovered)==receipt
            assert not notices(platform,quote)


def test_unknown_or_missing_original_evidence_never_releases_a_proposal(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=0) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);wait_for(lambda:answered(platform,quote))
        other=candidate(assistant,provider,template,task);wait_for(lambda:answered(platform,other))
        _,end=windows(template,quote['card']);aid=quote['admissionId'];broker=platform.delivery
        _,other_end=windows(template,other['card'])
        with platform.db.lock:
            monkeypatch.setattr(platform.admissions,'clock',lambda:max(end,other_end)+1)
            interaction=platform.db.one('SELECT * FROM delivery_interactions WHERE id=?',quote['interactionId'])
            platform.db.run('DELETE FROM delivery_interactions WHERE id=?',quote['interactionId'])
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='committed'
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',other['admissionId'])['state']=='released'
            columns=('id','origin','sender','recipient','kind','interface','revision','deadline','max_requests','requests','state','closed_at')
            platform.db.run('INSERT INTO delivery_interactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',*(interaction[key] for key in columns))
            call=platform.db.one("SELECT * FROM delegation_protocol_calls WHERE admission=? AND op='request.submit'",aid)
            for state in ('pending','processing','working','auth-required'):
                platform.db.run('UPDATE delegation_protocol_calls SET state=? WHERE message=?',state,call['message'])
                broker.cleanup()
                assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='committed'
            platform.db.run('UPDATE delegation_protocol_calls SET state=? WHERE message=?',call['state'],call['message'])
            offer=platform.db.one('SELECT result FROM delegation_offers WHERE admission=?',aid)['result']
            bad=json.loads(offer);bad['card']['derived']['total']+=1
            platform.db.run('UPDATE delegation_offers SET result=? WHERE admission=?',json.dumps(bad),aid)
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='committed'
            platform.db.run('UPDATE delegation_offers SET result=? WHERE admission=?',offer,aid)
            source=platform.db.one('SELECT * FROM delegation_signed_requests WHERE admission=?',aid)
            platform.db.run('DELETE FROM delegation_signed_requests WHERE admission=?',aid)
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='committed'
            platform.db.run('INSERT INTO delegation_signed_requests(admission,message,request,started) VALUES(?,?,?,?)',aid,call['message'],source['request'],source['started'])
            envelope=platform.db.one('SELECT envelope FROM delivery_messages WHERE id=?',call['message'])['envelope']
            platform.db.run('UPDATE delivery_messages SET envelope=NULL WHERE id=?',call['message'])
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='committed'
            platform.db.run('UPDATE delivery_messages SET envelope=? WHERE id=?',envelope,call['message'])
            cd=card_digest(quote['card'])
            platform.db.run('INSERT INTO delegation_contracts(admission,digest,card,permit) VALUES(?,?,?,?)',aid,cd,json.dumps(quote['card']),end-1)
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='committed'
            platform.db.run('DELETE FROM delegation_contracts WHERE admission=?',aid)
            # A payment marker without its receipt is missing evidence, not proof
            # of no payment. Reconcile instead of releasing admission capacity.
            platform.db.run('INSERT INTO paid(ref,purpose,period,at) VALUES(?,?,?,?)',cd,'deposit',0,end-1)
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='committed'
            platform.db.run('DELETE FROM paid WHERE ref=?',cd)
            assert not notices(platform,quote)
            broker.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='released'


@pytest.mark.parametrize('fee',[0,1])
def test_unquoted_expiry_preserves_unknown_task_and_closes_only_free_request(platform,tmp_path,monkeypatch,fee):
    from submission_fixture import FixtureResult
    from test_resident_delegation import request,terminal
    from test_resident_delegation import QuoteHost
    class UnknownHost(QuoteHost):
        def execute(self,turn):
            turn.on_session('unknown-original')
            return FixtureResult('unknown','unknown-original',None,{'code':'host_timeout'})
    with participants(platform,tmp_path,fee=fee,agent_host=UnknownHost()) as (provider,assistant,host,template,task):
        begun,_=request(assistant,provider,template,task)
        ref=begun['data']['quoteRef']
        wait_for(lambda:terminal(assistant,ref))
        assert terminal(assistant,ref)['status']=='unknown'
        aid=platform.db.one('SELECT * FROM delegation_admissions WHERE service=? ORDER BY rowid DESC',provider.identities[0].agent_id)
        source=platform.db.one('SELECT * FROM delegation_signed_requests WHERE admission=?',aid['id'])
        original=json.loads(source['request'])
        due=(parse_time(header_of(original['signature'])['a2c-ts'])+parse_duration(template['deadlines']['respondWithin'])).timestamp()
        with platform.db.lock:
            monkeypatch.setattr(platform.admissions,'clock',lambda:due)
            platform.delivery.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid['id'])['state']=='committed'
            monkeypatch.setattr(platform.admissions,'clock',lambda:due+.01)
            if not fee:
                original_message=platform.db.one('SELECT envelope FROM delivery_messages WHERE id=?',source['message'])['envelope']
                platform.db.run('UPDATE delivery_messages SET envelope=NULL WHERE id=?',source['message'])
                platform.delivery.cleanup()
                assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid['id'])['state']=='committed'
                platform.db.run('UPDATE delivery_messages SET envelope=? WHERE id=?',original_message,source['message'])
                platform.db.run('INSERT INTO paid(ref,purpose,period,at) VALUES(?,?,?,?)',aid['binding'],'proposal-fee',0,due)
                platform.delivery.cleanup()
                assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid['id'])['state']=='committed'
                platform.db.run('DELETE FROM paid WHERE ref=?',aid['binding'])
                with monkeypatch.context() as fault:
                    def interrupted(*args):raise OSError('closure interrupted')
                    fault.setattr(platform.delivery,'_closed_notices',interrupted)
                    with pytest.raises(OSError):platform.delivery.cleanup()
                assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid['id'])['state']=='committed'
            platform.delivery.cleanup();platform.delivery.cleanup()
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid['id'])['state']==('released' if not fee else 'committed')
            assert platform.db.one('SELECT request FROM delegation_signed_requests WHERE admission=?',aid['id'])['request']==source['request']
            notifications=platform.db.rows("SELECT * FROM delivery_messages WHERE interaction=? AND kind='interaction.closed'",aid['interaction'])
            assert len(notifications)==(2 if not fee else 0)
        assert terminal(assistant,ref)['status']=='unknown'


def test_original_trade_form_retries_only_after_platform_closed_notice(platform,tmp_path,monkeypatch):
    from ioalab_sdk.runtime import ClientOperations
    from submission_fixture import FixtureResult
    from test_resident_delegation import QuoteHost
    from test_interaction_workflows import step
    from test_assistant_role import user_decision
    class OnceUnknown(QuoteHost):
        failed=False
        def execute(self,turn):
            if self.failed:return super().execute(turn)
            self.failed=True;turn.on_session('unknown-quote')
            return FixtureResult('unknown','unknown-quote',None,{'code':'host_timeout'})
    with participants(platform,tmp_path,fee=0,agent_host=OnceUnknown(),service_name="原报价无决定恢复专用服务") as (provider,assistant,host,template,task):
        api=ClientOperations(assistant.config.source)
        form=api.call('runtime.forms.open',{'kind':'delegation.trade'})['data']
        form=step(api,form,{'purpose':'验证原委托重报价','budget':50,'proposalFeeLimit':0,'query':'原报价无决定恢复专用服务'})
        user_decision(assistant,form['result']['data']['confirmationRef']);form=step(api,form)
        # Select the real published provider from semantic discovery; an opaque ID is not a text query.
        form=step(api,form,{'action':'select','service':provider.identities[0].agent_id})
        form=step(api,form,{'templateDigest':template['templateDigest']})
        form=step(api,form,{'spec':{'result':{}}})
        def refresh():return api.call('runtime.forms.submit',{'reference':form['reference'],'revision':form['revision']})['data']
        wait_for(lambda:refresh()['phase']=='unknown')
        form=refresh();old=platform.db.one('SELECT * FROM delegation_admissions WHERE service=? ORDER BY rowid DESC',provider.identities[0].agent_id)
        assert form['phase']=='unknown'
        source=platform.db.one('SELECT request FROM delegation_signed_requests WHERE admission=?',old['id'])
        due=(parse_time(header_of(json.loads(source['request'])['signature'])['a2c-ts'])+parse_duration(template['deadlines']['respondWithin'])).timestamp()
        with monkeypatch.context() as clock:
            clock.setattr(platform.admissions,'clock',lambda:due+1);platform.delivery.cleanup()
        wait_for(lambda:refresh()['title'].startswith('原报价请求已过期'))
        form=refresh();assert form['phase']=='draft'
        assert len(platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id))==1
        form=step(api,form,{'action':'retry'})
        wait_for(lambda:refresh()['title']=='比较报价与选择合同')
        form=refresh();assert form['phase']=='draft'
        rows=platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id)
        assert len(rows)==2 and {r['scope'] for r in rows}=={old['scope']}
        assert {r['state'] for r in rows}=={'released','committed'}


def test_expired_returned_quote_recovers_original_form_and_waits_for_safe_closure(platform,tmp_path,monkeypatch):
    from datetime import timedelta
    from ioalab_sdk.runtime import ClientOperations
    from test_interaction_workflows import step
    from test_assistant_role import user_decision
    from ioalab_sdk.assistant import candidates
    with participants(platform,tmp_path,fee=0,service_name="已过期报价原表单恢复专用服务") as (provider,assistant,host,template,task):
        api=ClientOperations(assistant.config.source)
        form=api.call('runtime.forms.open',{'kind':'delegation.trade'})['data']
        form=step(api,form,{'purpose':'原表单报价过期恢复','budget':50,'proposalFeeLimit':0,'query':'已过期报价原表单恢复专用服务'})
        user_decision(assistant,form['result']['data']['confirmationRef']);form=step(api,form)
        form=step(api,form,{'action':'select','service':provider.identities[0].agent_id})
        form=step(api,form,{'templateDigest':template['templateDigest']})
        form=step(api,form,{'spec':{'result':{}}})
        def current():return api.call('runtime.forms.get',{'reference':form['reference']})['data']
        wait_for(lambda:current()['title']=='比较报价与选择合同')
        form=current();quote=form['evidence']['报价'][0];card=quote['card'];response,end=windows(template,card)
        with monkeypatch.context() as clock:
            clock.setattr(candidates,'utcnow',lambda:datetime.fromtimestamp(response+1,timezone.utc))
            saved=api.call('runtime.forms.update',{'reference':form['reference'],'revision':form['revision'],'values':{'action':'select','quoteRef':quote['quoteRef']}})['data']
            target={'reference':form['reference'],'revision':saved['revision']}
            assert api.call('runtime.forms.preview',target)['status']=='completed'
            denied=api.call('runtime.forms.submit',target)
            assert denied['status']=='rejected' and denied['error']['code']=='expired'
            assert current()['canRecover']
            recovered=api.call('runtime.forms.recover',target)
            assert recovered['status']=='completed',recovered
            form=recovered['data'];assert form['phase']=='draft' and form['revision']==target['revision']+1
            assert '报价已过期' in form['title'] and form['result'] is None
            assert api.call('runtime.forms.recover',target)['data']==form
            assert api.call('runtime.forms.submit',target)['data']==form
        # Preparing recovery and repeated reads cannot reserve another slot.
        assert len(platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id))==1
        form=step(api,form,{'action':'retry'})
        assert form['phase']=='running' and form['result']['data']['waitingFor']=='original_quote_closure'
        assert len(platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id))==1
        with monkeypatch.context() as clock:
            clock.setattr(platform.admissions,'clock',lambda:end+1);platform.delivery.cleanup()
        wait_for(lambda:current()['title']=='比较报价与选择合同')
        fresh=current()['evidence']['报价'][-1]
        assert fresh['taskRef']==quote['taskRef'] and fresh['quoteRef']!=quote['quoteRef']
        assert current()['schema']['properties']['quoteRef']['enum']==[fresh['quoteRef']]
        assert current()['evidence']['历史过期报价'][0]['quoteRef']==quote['quoteRef']
        assert fresh['card']['spec']==card['spec']
        assert not fresh['card']['signatures']['1']
