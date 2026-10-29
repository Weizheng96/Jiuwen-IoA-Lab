"""Local user delegation scopes; no protocol or signing rules are reimplemented."""
from decimal import Decimal, InvalidOperation
import time
import os
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
    def delegation_policy(self):
        try:minimum=int(os.getenv('IOALAB_MIN_QUOTES','1'))
        except ValueError:raise ValueError('IOALAB_MIN_QUOTES must be an integer from 1 to 8') from None
        if not 1<=minimum<=8:raise ValueError('IOALAB_MIN_QUOTES must be an integer from 1 to 8')
        return {'minimumQuotes':minimum}

    def require_scope_for_policy(self, scope_id):
        if not scope_id and self.delegation_policy()['minimumQuotes']>1:
            raise ValueError('Configured comparison policy requires authorize and scope_id before forming candidate contracts')

    def quote_progress(self, scope_id):
        state=self.scope(scope_id)
        fees=sum((amount(v['fee']) for v in state['requests'].values()),Decimal(0))
        offers={}
        for cd,sid in self.store.all('card_scopes').items():
            if sid!=scope_id:continue
            try:
                card=self.client.card(cd);template=self.client.template(card['templateDigest'])
            except (KeyError,ValueError):continue
            if amount(card['derived']['total'])+fees>amount(state['budget']):continue
            if template['formation']=='proposal' and not card['signatures'].get('2'):continue
            offers[card['provider']]=cd
        unresolved=[key for key,v in state['requests'].items() if v['state']!='returned']
        required=state.get('minimumQuotes',1)
        ready=len(offers)>=required and not unresolved
        next_action='reconcile' if unresolved else ('compare' if ready else 'collect')
        instruction=('先核实未确定的拟卡费，不重复付费或请求合同确认。' if unresolved else
                     ('最低比选数量已达到；比较真实合同和总费用后，请用户确认具体合同。' if ready else
                      f'还需{required-len(offers)}个不同服务端点的有效候选；在授权费用内继续征集，不请求用户确认合同。'))
        return {'scopeId':scope_id,'minimumQuotes':required,'receivedQuotes':len(offers),
                'proposalFees':str(fees),'pendingRequests':unresolved,'readyForConfirmation':ready,
                'providers':list(offers),'cardDigests':list(offers.values()),'nextAction':next_action,'instruction':instruction}

    def with_quote_progress(self, result, scope_id):
        return {**result,'quoteProgress':self.quote_progress(scope_id)} if scope_id else result

    def authorize(self, purpose: str, budget, proposal_fee_limit=0, valid_days=7, *, minimum_quotes=None, confirmed=False):
        self.require_confirmation(confirmed)
        if not isinstance(purpose, str) or not 1 <= len(purpose) <= 2000:
            raise ValueError('A bounded delegation purpose is required')
        if not 0 < float(valid_days) <= 7:
            raise ValueError('Authorization may last at most seven days')
        minimum=self.delegation_policy()['minimumQuotes']
        if minimum_quotes is not None:
            if type(minimum_quotes) is not int or not 1<=minimum_quotes<=8:raise ValueError('minimum_quotes must be an integer from 1 to 8')
            minimum=max(minimum,minimum_quotes)
        budget, fees = amount(budget), amount(proposal_fee_limit)
        if fees > budget:
            raise ValueError('Proposal fees are part of the total budget')
        sid = uuid.uuid4().hex
        state = dict(scopeId=sid, purpose=purpose, budget=str(budget), proposalFeeLimit=str(fees),
                     expiresAt=time.time()+float(valid_days)*86400, minimumQuotes=minimum, requests={}, selected=None)
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
        if state['selected'] is None:
            progress=self.quote_progress(sid)
            if not progress['readyForConfirmation']:
                raise ValueError(f"Comparison policy is not ready: {progress['receivedQuotes']}/{progress['minimumQuotes']} distinct providers. "+progress['instruction'])
            state['comparisonCards']=progress['cardDigests']
        state['selected']=cd
        self.store.put('scopes',sid,state)
