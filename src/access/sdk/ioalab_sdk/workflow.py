"""Participant-local orchestration of existing SDK methods, with durable human gates."""
from decimal import Decimal
import json
from pathlib import Path
import uuid
import time
import httpx
from a2c.errors import A2CError

from .scope import amount


class Workflow:
    def __init__(self, client):
        self.c=client

    def save(self, task):
        self.c.store.put('delegations',task['taskId'],task)
        return self.view(task)

    def load(self, task_id):
        task=self.c.store.get('delegations',task_id)
        if not task: raise ValueError('Unknown local delegation task. Use delegate action=status without task_id to list owned tasks; do not begin another task')
        return task

    def view(self, task):
        # Compatibility renderer: domain facts come only from actual cards and requests.
        keys=('taskId','gate','need','budget','spec','proposalFeeLimit','scopeId','excluded','quotes',
              'recommended','cardDigest','stepRequest','period','report','files','payment','payments','errors')
        gate=task['gate'];recommended=task.get('recommended',{});message='';facts=None
        if task.get('cardDigest'):
            info=self.c.inspect(task['cardDigest']);card=info['card'];template=info['template']
            facts={'spec':card.get('spec',{}),'deliverables':template['deliverables'],
                   'derived':card['derived'],'acceptance':card.get('acceptance',template.get('acceptance',{})),
                   'deadlines':card.get('deadlines',template.get('deadlines',{})),
                   'signers':card.get('signers',{}),'inputs':card.get('inputs',{})}
        if gate=='authorization':
            message=f"委托需求：{task['need']}。总预算{task['budget']}虚拟币，其中拟卡费上限{task['proposalFeeLimit']}，授权最长7天。按公示规则支付拟卡费，未采纳提案不代表退费；具体合同及每期验收仍须分别确认。是否授权？"
        elif gate=='contract':
            quotes='；'.join(q['provider']+' '+q['total']+'虚拟币' for q in task['quotes'])
            message=f"已形成合同候选：{quotes}。推荐{recommended['provider']}，合同价{recommended['total']}，拟卡费{recommended['proposalFees']}，合计{recommended['totalCost']}虚拟币。推荐理由：{recommended['reason']}。\n实际合同要点："+json.dumps(facts,ensure_ascii=False)+'。是否确认这份合同？'
        elif gate=='step':
            message='服务方请求合同环节授权：'+json.dumps(task['stepRequest'],ensure_ascii=False)+'。是否同意本次请求？'
        elif gate=='acceptance':
            installment=facts['derived']['periods'][task['period']-1]['amount']
            message=f"第{task['period']}期实际交付已取回，本地WASM验收通过。请确认本期验收；确认后签字并按合同支付{installment}虚拟币。"
        elif gate=='completed':
            message='各期验收签字和付款已完成。合同价'+recommended['total']+'，拟卡费'+recommended['proposalFees']+'，合计'+recommended['totalCost']+'虚拟币。'
        result={'userMessage':message,**{k:task[k] for k in keys if k in task}}
        if facts:result['contractFacts']=facts
        result['next']={
            'authorization':'等待用户确认委托范围与拟卡费上限。',
            'collecting':'继续poll征集真实合同，不重复授权。',
            'contract':'展示真实合同、费用、规格与资料范围，等待用户确认。',
            'step':'展示实际stepRequest，等待用户授权，不假设环节类型。',
            'acceptance':'展示本期报告与金额，等待本期验收确认。',
            'working':'继续poll等待下一真实环节或存证。',
            'completed':'全部期验收与付款已核实。',
            'declined':'停止推进；既有费用及合同记录保留，不补造解除或退款。',
            'verification-failed':'不签验收、不付款；与用户讨论修正或仲裁。',
        }.get(gate,'结果待核实，不重复付款或重新创建任务。')
        return result

    def begin(self, need, budget, plan_file, spec=None):
        if not isinstance(need,str) or not 1<=len(need)<=2000:raise ValueError('A bounded business request is required')
        plan=json.loads(Path(plan_file).read_text(encoding='utf-8-sig'))
        allowed={'format','query','target','defaults','rejectDescriptions','formation','proposalFeeLimit','ranking'}
        if not isinstance(plan,dict) or set(plan)-allowed or plan.get('format')!='ioalab.delegate.plan':
            raise ValueError('Only declarative delegation plans are accepted')
        if not isinstance(plan.get('query'),str) or not 1<=len(plan['query'])<=1000:raise ValueError('Invalid discovery query')
        reject=plan.get('rejectDescriptions',[])
        if not isinstance(reject,list) or len(reject)>12 or any(not isinstance(s,str) or not 1<=len(s)<=200 for s in reject):raise ValueError('Invalid bounded exclusion phrases')
        ranking=plan.get('ranking',['price'])
        if not isinstance(ranking,list) or not ranking or len(set(ranking))!=len(ranking) or any(k not in ('price','script','coverage') for k in ranking):raise ValueError('Invalid ranking criteria')
        values={**plan.get('defaults',{}),**(spec or {})}
        if any(not isinstance(k,str) or not isinstance(v,(str,int,float,bool)) for k,v in values.items()):raise ValueError('Only scalar specification values are supported')
        target=plan['target'];limit=amount(budget)
        task={'taskId':uuid.uuid4().hex,'gate':'authorization','need':need,'budget':str(limit),
              'spec':{target:values},'candidates':[],'excluded':[],'quotes':[],'plan':plan,'proposalFeeLimit':'0'}
        for existing in self.c.store.all('delegations').values():
            if existing['gate'] not in ('completed','declined') and all(existing.get(k)==task.get(k) for k in ('need','budget','spec','plan')):
                return self.view(existing)
        task['proposalFeeLimit']=str(amount(plan.get('proposalFeeLimit',0)))
        if amount(task['proposalFeeLimit'])>limit:raise ValueError('Proposal fee limit exceeds the total budget')
        self.c.observe('need.create',taskId=task['taskId'])
        return self.save(task)

    def discover(self, task):
        plan=task['plan'];target=plan['target'];limit=amount(task['budget']);reject=plan.get('rejectDescriptions',[])
        task['candidates']=[];task['excluded']=[]
        hits=self.c.search(plan['query'],top_k=20)['hits']
        seen=set()
        for hit in hits:
            url=hit.get('card',{}).get('url') or hit.get('url')
            if not url or url in seen:continue
            seen.add(url)
            try:
                reputation=self.c.reputation(url)
                templates=self.c.templates(url)
            except (httpx.HTTPError,OSError,A2CError):
                task['excluded'].append({'provider':hit.get('name') or url,'reason':'服务端点不可用或模板无法核验'})
                continue
            for template in templates:
                if not any(d['name']==target for d in template['deliverables']):continue
                name=hit.get('name') or url
                description='\n'.join(d.get('description','') for d in template['deliverables'])
                reason=next((p for p in reject if p in description),None)
                if not reason and any(i.get('required',True) for i in template.get('inputs',[])):reason='需要用户尚未提供的材料'
                if not reason and template['formation']!='proposal':
                    formulas=list(template['pricing'].get('priceFormula',{}).values())
                    try:
                        if sum((amount(v) for v in formulas),Decimal(0))>limit:reason='公示定价超过预算'
                    except ValueError:pass  # Nonconstant pricing remains for the A2C evaluator, never eval().
                if not reason and plan.get('formation') and template['formation']!=plan['formation']:reason='不符合本轮成约方式'
                if reason:
                    task['excluded'].append({'provider':name,'reason':reason,'reputation':{'completed':reputation['completed'],'successRate':reputation['successRate']}})
                    continue
                if len(task['candidates'])>=8:raise ValueError('Narrow the query to at most eight eligible services')
                task['candidates'].append({'provider':name,'url':url,'templateDigest':template['templateDigest'],'formation':template['formation']})
        if not task['candidates']:raise ValueError('No service can satisfy the current declaration; do not invent a proposal')
        fees=sum((amount(self.c.client.template(v['templateDigest'])['pricing'].get('proposalFee',0)) for v in task['candidates']),Decimal(0))
        if fees>amount(task['proposalFeeLimit']):raise ValueError('Actual published proposal fees exceed the authorized limit; request a new authorization')
        task['discovered']=True
        self.save(task)

    def run(self, action, task_id=None, confirmed=False, **kwargs):
        if action=='begin':return self.begin(**kwargs)
        if action=='status' and task_id is None:
            return {'tasks':[{'taskId':t['taskId'],'gate':t['gate'],'need':t['need']} for t in self.c.store.all('delegations').values()]}
        task=self.load(task_id)
        if action=='status':return self.view(task)
        if action=='poll':
            if task['gate']=='collecting':return self.collect(task)
            if task['gate']!='working':return self.view(task)
            return self.poll(task,kwargs.get('timeout',60))
        if action=='decline':
            self.c.require_confirmation(confirmed)
            if task['gate']=='step':self.c.answer_step(task['cardDigest'],False,confirmed=True)
            if task['gate'] in ('authorization','contract','step','acceptance'):
                task['gate']='declined';return self.save(task)
            raise ValueError('This stage cannot be declined without reconciling its result')
        if action!='advance':raise ValueError('Use begin, status, advance, decline or poll')
        self.c.require_confirmation(confirmed)
        gate=task['gate']
        if gate=='authorization':
            if 'scopeId' not in task:
                scope=self.c.authorize(task['need'],task['budget'],task['proposalFeeLimit'],confirmed=True)
                task['scopeId']=scope['scopeId'];self.c.store.put('workflow_scopes',scope['scopeId'],task['taskId']);self.save(task)
            task['gate']='collecting';self.save(task)
            return self.collect(task)
        if gate=='contract':
            cd=task['cardDigest']
            if task['recommended']['formation']=='proposal':self.c.adopt(cd,confirmed=True)
            else:self.c.sign(cd,confirmed=True)
            self.c.start(cd,confirmed=True)
            task['gate']='working';self.save(task)
            return self.await_boundary(task,kwargs.get('timeout',240))
        if gate=='step':
            self.c.answer_step(task['cardDigest'],True,confirmed=True)
            task['gate']='working';self.save(task)
            return self.await_boundary(task,kwargs.get('timeout',240))
        if gate=='acceptance':
            result=self.c.accept(task['cardDigest'],task['period'],confirmed=True)
            task['payment']=result
            paid=(result.get('status')=='pass' and result.get('voucher')) or (result.get('status')=='already-accepted' and result.get('payment')=='recorded')
            if not paid:task['gate']='payment-unknown'
            else:
                task.setdefault('payments',{})[str(task['period'])]=result
                task['gate']='completed' if task['period']==len(self.c.client.card(task['cardDigest'])['derived']['periods']) else 'working'
                if task['gate']=='working':
                    for key in ('report','files','stepRequest'):task.pop(key,None)
            return self.save(task)
        if gate=='completed':return self.view(task)
        raise ValueError('This stage does not accept a new confirmation; inspect status')

    def collect(self, task):
        if not task.get('discovered'):self.discover(task)
        for candidate in task['candidates']:
            if any(q['providerUrl']==candidate['url'] for q in task['quotes']):continue
            if candidate['formation']=='proposal':
                reply=self.c.request(candidate['templateDigest'],task['spec'],budget=task['budget'],scope_id=task['scopeId'],confirmed=True)
            else:
                reply=self.c.draft(candidate['templateDigest'],task['spec'],budget=task['budget'],scope_id=task['scopeId'])
                self.c.store.put('card_scopes',reply['cardDigest'],task['scopeId'])
            if reply.get('returned') or reply.get('overBudget'):raise ValueError('Proposal was declined or exceeds budget; inspect the saved scope before continuing')
            self.c.store.put('workflow_cards',reply['cardDigest'],task['taskId'])
            card=self.c.client.card(reply['cardDigest'])
            template=self.c.client.template(card['templateDigest'])
            from a2c.template import acceptance_of
            acceptance=acceptance_of(template,card)
            kinds=sorted({k for block in acceptance.values() for k in block['verifiers']})
            not_covered=[v for block in acceptance.values() for v in block.get('notCovered',[])]
            quote={'provider':candidate['provider'],'providerUrl':candidate['url'],'cardDigest':reply['cardDigest'],
                   'total':str(card['derived']['total']),'verifierKinds':kinds,'notCovered':not_covered,'formation':candidate['formation']}
            task['quotes'].append(quote);self.save(task)
        fees=sum((amount(v['fee']) for v in self.c.scope(task['scopeId'])['requests'].values()),Decimal(0))
        eligible=[q for q in task['quotes'] if amount(q['total'])+fees<=amount(task['budget'])]
        if not eligible:raise ValueError('Contract plus proposal fees exceeds the authorized total')
        ranking=task['plan'].get('ranking',['price'])
        def rank(q):
            values={'script':0 if q['verifierKinds']==['script'] else 1,'coverage':len(q['notCovered']),'price':amount(q['total'])}
            return tuple(values[k] for k in ranking)
        chosen=min(eligible,key=rank)
        task['recommended']={**chosen,'proposalFees':str(fees),'totalCost':str(amount(chosen['total'])+fees),
                             'reason':'按本次声明的排序规则 '+','.join(ranking)+' 比较；最终由用户确认'}
        task['cardDigest']=chosen['cardDigest'];task['gate']='contract'
        providers=list(dict.fromkeys(q['providerUrl'] for q in task['quotes']))
        if len(providers)>1:self.c.compare(providers,selected=chosen['providerUrl'],reason=task['recommended']['reason'],cd=chosen['cardDigest'])
        return self.save(task)

    def await_boundary(self, task, timeout):
        until=time.monotonic()+min(240,max(1,float(timeout)))
        while True:
            result=self.poll(task,min(60,max(1,until-time.monotonic())))
            if task['gate']!='working' or time.monotonic()>=until:return result

    def poll(self, task, timeout):
        record=self.c.store.get('cards',task['cardDigest'])
        pending=[s['request'] for s in record.get('steps',[]) if s.get('answer') is None]
        if pending:
            task['stepRequest']=pending[-1];task['gate']='step';return self.save(task)
        result=self.c.watch(task['cardDigest'],timeout=timeout)
        if task.get('report',{}).get('status')=='pending':
            task['report']=self.c.verify(task['cardDigest'],task['period'])
            if task['report']['status']=='pass':task['gate']='acceptance'
            elif task['report']['status'] in ('fail','error'):task['gate']='verification-failed'
            return self.save(task)
        for event in result['events']:
            if event['type']=='step-request':
                task['stepRequest']=event['stepRequest'];task['gate']='step'
                return self.save(task)
            if event['type']=='anchored':
                period=event['period'];task['period']=period
                task['files']=self.c.fetch(task['cardDigest'],period)
                task['report']=self.c.verify(task['cardDigest'],period)
                if task['report']['status']=='pass':task['gate']='acceptance'
                elif task['report']['status'] in ('fail','error'):task['gate']='verification-failed'
                return self.save(task)
        return self.save(task)
