"""Local user delegation scopes; no protocol or signing rules are reimplemented."""
from decimal import Decimal, InvalidOperation
import time
import uuid


def amount(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid virtual currency amount') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Amount must be finite and nonnegative')
    return result


class DelegationScopes:
    def authorize(self, purpose: str, budget, proposal_fee_limit=3, valid_days=7, *, confirmed=False):
        self.require_confirmation(confirmed)
        if not isinstance(purpose, str) or not 1 <= len(purpose) <= 2000:
            raise ValueError('A bounded delegation purpose is required')
        if not 0 < float(valid_days) <= 7:
            raise ValueError('Authorization may last at most seven days')
        budget, fees = amount(budget), amount(proposal_fee_limit)
        if fees > budget:
            raise ValueError('Proposal fees are part of the total budget')
        sid = uuid.uuid4().hex
        state = dict(scopeId=sid, purpose=purpose, budget=str(budget), proposalFeeLimit=str(fees),
                     expiresAt=time.time()+float(valid_days)*86400, requests={}, selected=None)
        self.store.put('scopes',sid,state)
        self.observe('need.authorized',scopeId=sid,budget=str(budget))
        return state

    def scope(self, scope_id):
        state = self.store.get('scopes',scope_id)
        if not state:
            raise ValueError('Unknown local delegation scope')
        return state

    def live_scope(self, scope_id):
        state = self.scope(scope_id)
        if state['expiresAt'] <= time.time():
            raise ValueError('Delegation authorization expired')
        return state

    def reserve_proposal(self, scope_id, template, spec_digest=None):
        state=self.live_scope(scope_id)
        key=template['templateDigest'] if 'templateDigest' in template else __import__('a2c.objects',fromlist=['object_digest']).object_digest(template)
        old=state['requests'].get(key)
        if old:
            if old.get('specDigest')!=spec_digest:
                raise ValueError('This scope already requested different terms from this template')
            if old['state']=='returned': return state,key,old['result']
            raise ValueError('Proposal payment outcome needs reconciliation; do not automatically retry')
        if state['selected']:
            raise ValueError('This delegation already selected a contract')
        fee=amount(template['pricing'].get('proposalFee',0))
        total=sum((amount(v['fee']) for v in state['requests'].values()),Decimal(0))+fee
        if total>amount(state['proposalFeeLimit']) or total>amount(state['budget']):
            raise ValueError('Proposal fees exceed the authorized limit')
        state['requests'][key]={'fee':str(fee),'state':'pending','specDigest':spec_digest}
        self.store.put('scopes',scope_id,state)  # Before the external payment, including crash recovery.
        return state,key,None

    def settle_proposal(self, state, key, result):
        state['requests'][key].update(state='returned',result=result)
        self.store.put('scopes',state['scopeId'],state)
        if result.get('cardDigest'):
            self.store.put('card_scopes',result['cardDigest'],state['scopeId'])

    def check_scope_selection(self, cd):
        sid=self.store.get('card_scopes',cd)
        if not sid: return
        state=self.live_scope(sid)
        if state['selected'] not in (None,cd):
            raise ValueError('Only one contract may be adopted per delegation')
        fees=sum((amount(v['fee']) for v in state['requests'].values()),Decimal(0))
        if amount(self.client.card(cd)['derived']['total'])+fees>amount(state['budget']):
            raise ValueError('Contract and proposal fees exceed the authorized total budget')
        state['selected']=cd
        self.store.put('scopes',sid,state)
