"""Insufficient allowance preserves acceptance and requires fresh owner approval."""
import copy
from decimal import Decimal
from ioalab_sdk.runtime.ipc import LocalClient
from _resident_payment import ready_for_accept
from test_assistant_role import user_decision
from test_resident_contracts import status
from test_resident_fulfillment import invoke,operation
from conftest import wait_for


def test_insufficient_then_pay_installment(platform,tmp_path):
    with ready_for_accept(platform,tmp_path) as (provider,assistant,ref,accept_key,pending):
        rpc=LocalClient(assistant.config.home)
        assert rpc.call('assistant.delegation.pay',{'contractRef':ref,'period':1})['status']=='rejected'
        owner=assistant.account.account['id']
        platform.wallets.adjust(owner,-platform.wallets.balance(owner),by='test',note='Isolated insufficient allowance')
        user_decision(assistant,pending['data']['confirmationRef'])
        rpc.call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=accept_key)
        wait_for(lambda:(operation(assistant,ref,accept_key) or {}).get('status')=='completed')
        result=operation(assistant,ref,accept_key)['data']
        assert result['acceptance']=='pass' and result['payment']=='unpaid'
        protocol,_=assistant.modules[0].fulfillment.client(ref)
        signed=copy.deepcopy(protocol.card(ref)['signatures']['7'])
        assert signed and not platform.db.one("SELECT 1 FROM paid WHERE ref=?",ref)
        platform.wallets.adjust(owner,Decimal('100'),by='test',note='Explicit test allowance for retry')
        key,waiting=invoke(assistant,ref,'pay',period=1)
        assert waiting['status']=='waiting'
        user_decision(assistant,waiting['data']['confirmationRef'])
        args={'contractRef':ref,'period':1}
        rpc.call('assistant.delegation.pay',args,operation_id=key)
        wait_for(lambda:(operation(assistant,ref,key) or {}).get('status')=='completed')
        paid=operation(assistant,ref,key)['data']
        assert paid['payment']=='paid' and paid['paymentNotified']
        wait_for(lambda:status(assistant,ref)['data']['closed'])
        balance=platform.wallets.balance(owner)
        replay=rpc.call('assistant.delegation.pay',args,operation_id=key)
        assert replay['status']=='completed' and replay['data']==paid
        assert platform.wallets.balance(owner)==balance and protocol.card(ref)['signatures']['7']==signed
        assert len(platform.db.rows("SELECT * FROM transfers WHERE ref=? AND purpose='installment'",ref))==1
