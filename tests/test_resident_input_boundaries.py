"""Strict role inputs reject malformed deliverable maps before paid/model effects."""
import pytest
from ioalab_sdk.runtime.ipc import LocalClient
from test_resident_delegation import participants,uid


@pytest.mark.parametrize('spec',[[],{'result':[]},{'unknown_deliverable':{}}])
def test_candidate_input_must_match_declared_deliverables_without_implicit_conversion(platform,tmp_path,spec):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        owner=assistant.account.account['id'];before=platform.wallets.balance(owner)
        result=LocalClient(assistant.config.home).call('assistant.delegation.request',{
            'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],'spec':spec},operation_id=uid())
        assert result['status']=='rejected' and result['error']['message']
        assert platform.wallets.balance(owner)==before and host.turns==[]
        assert not platform.db.rows("SELECT * FROM transfers WHERE payer_id=?",owner)

@pytest.mark.parametrize('fees',[{}, {'proposalFeeLimit':''}],ids=['missing','empty'])
def test_prepare_requires_an_explicit_valid_proposal_fee_limit_before_any_scope_or_fee(platform,tmp_path,fees):
    from test_assistant_role import runtime_at,ScriptedHost
    host=ScriptedHost()
    with runtime_at(platform,tmp_path/'assistant',host) as (runtime,client,role,user):
        before=runtime.store.checkpoints(role.delegation.namespace)
        owner=runtime.account.account['id'];balance=platform.wallets.balance(owner)
        result=client.call('assistant.delegation.prepare',{'purpose':'拒绝缺失或空费用限制','budget':50,**fees})
        assert result['status']=='rejected' and result['error']['code']=='invalid_input'
        assert runtime.store.checkpoints(role.delegation.namespace)==before
        assert runtime.confirmations.list()==[] and host.turns==[]
        assert not platform.db.rows('SELECT * FROM delegation_scopes WHERE actor=?',role.identity.agent_id)
        assert not platform.db.rows('SELECT * FROM transfers WHERE payer_id=?',owner)
        assert platform.wallets.balance(owner)==balance


def test_explicit_zero_fee_scope_requires_owner_approval_and_never_creates_a_fee(platform,tmp_path):
    from decimal import Decimal
    from test_assistant_role import runtime_at,user_decision,ScriptedHost
    host=ScriptedHost()
    with runtime_at(platform,tmp_path/'assistant',host) as (runtime,client,role,user):
        owner=runtime.account.account['id'];balance=platform.wallets.balance(owner)
        prepared=client.call('assistant.delegation.prepare',{'purpose':'明确零提案费用','budget':50,'proposalFeeLimit':0})
        assert prepared['status']=='completed' and not prepared['data']['authorized']
        task=prepared['data']['taskRef'];operation=uid()
        pending=client.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)
        assert pending['status']=='waiting'
        assert pending['data']['preview']['proposalFeeLimit']=='0.00'
        assert not platform.db.rows('SELECT * FROM delegation_scopes WHERE actor=?',role.identity.agent_id)
        user_decision(runtime,pending['data']['confirmationRef'])
        authorized=client.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)
        assert authorized['status']=='completed'
        scope=authorized['data']['authorization']
        assert scope['state']=='active' and scope['proposalFeeLimit']=='0.00'
        assert Decimal(scope['proposalFeesPaid'])==0 and Decimal(scope['spent'])==0
        assert not platform.db.rows('SELECT * FROM transfers WHERE payer_id=?',owner)
        assert platform.wallets.balance(owner)==balance and host.turns==[]


@pytest.mark.parametrize('count',[0,33],ids=['empty','over-32'])
def test_comparison_rejects_out_of_range_quote_counts_before_creating_jobs(platform,tmp_path,count):
    from test_assistant_role import runtime_at,ScriptedHost
    host=ScriptedHost()
    with runtime_at(platform,tmp_path/'assistant',host) as (runtime,client,role,user):
        prepared=client.call('assistant.delegation.prepare',{'purpose':'候选数量边界','budget':50,'proposalFeeLimit':0})
        assert prepared['status']=='completed'
        actor=role.identity.agent_id
        before=runtime.scheduler.records(actor,('assistant.compare',))
        confirmations=runtime.confirmations.list()
        result=client.call('assistant.delegation.compare',{'taskRef':prepared['data']['taskRef'],
                                                          'quoteRefs':[uid() for _ in range(count)]})
        assert result['status']=='rejected' and result['error']['code']=='invalid_input'
        assert runtime.scheduler.records(actor,('assistant.compare',))==before
        assert runtime.confirmations.list()==confirmations and host.turns==[]
        assert not platform.db.rows('SELECT * FROM transfers WHERE payer_id=?',runtime.account.account['id'])
