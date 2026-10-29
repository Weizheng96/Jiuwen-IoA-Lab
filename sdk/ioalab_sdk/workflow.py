"""Participant-local orchestration of existing SDK methods, with durable human gates."""
from decimal import Decimal
import json
from pathlib import Path
import uuid
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

    @staticmethod
    def view(task):
        # Keep full signed originals in the A2C store, rather than repeating them in model context.
        keys=('taskId','gate','need','budget','spec','proposalFeeLimit','scopeId','excluded','quotes',
              'recommended','cardDigest','stepRequest','report','files','payment','errors')
        gate=task['gate'];spec=next(iter(task.get('spec',{}).values()),{});recommended=task.get('recommended',{})
        message=''
        if gate=='authorization':
            message=f"将为您寻找预约小程序服务。预算上限{task['budget']}虚拟币，包含最终合同和最多{task['proposalFeeLimit']}虚拟币拟卡费；授权有效7天。确认后先支付公示拟卡费，不采纳提案也不退拟卡费。合同与验收均由您分别确认。是否确认授权？"
        elif gate=='contract':
            quotes='；'.join(q['provider']+' '+q['total']+'虚拟币（'+('确定性脚本验收' if q['verifierKinds']==['script'] else '模型观察、程序判定')+'）' for q in task['quotes'])
            message=f"已取得真实签名提案：{quotes}。推荐{recommended['provider']}，合同价{recommended['total']}，已付拟卡费{recommended['proposalFees']}，总支出{recommended['totalCost']}虚拟币，未超过{task['budget']}预算。推荐依据：{recommended['reason']}。\n"
            message+=f"待确认规格：{spec.get('shop')}，{spec.get('open')}至{spec.get('close')}点，每时段{spec.get('capacity')}人；{spec.get('service1Name')} {spec.get('service1Minutes')}分钟、{spec.get('service2Name')} {spec.get('service2Minutes')}分钟。未指定项目和营业时间采用以上待确认默认值。交付预约配置、预约引擎源码及微信小程序页面源码；本地测试不等于微信上线。\n未覆盖："+'；'.join(recommended['notCovered'])+'。是否确认这份合同及资料范围？'
        elif gate=='step':
            message='服务方请求实名注册环节授权。当前本地演示只记录授权，不提交真实身份资料，不申请微信账号或审核。是否授权实名注册？'
        elif gate=='acceptance':
            message='已取回实际交付，本地WASM验收通过。请确认验收；确认后签署验收并按合同支付'+recommended['total']+'虚拟币。'
        elif gate=='completed':
            message='验收签字和付款已完成。合同付款'+recommended['total']+'，另有拟卡费'+recommended['proposalFees']+'，本单总支出'+recommended['totalCost']+'虚拟币。'
        result={'userMessage':message,**{k:task[k] for k in keys if k in task}}
        prompts={
            'authorization':'请确认发起委托：授权后将先支付公示的拟卡费，最终合同及验收仍分别由您确认。',
            'collecting':'已获委托授权，正在征集真实提案；继续poll，不需要用户重复授权。',
            'contract':'请确认推荐合同、总费用、规格及资料范围；确认前不签约、不开工。',
            'step':'请确认合同预先声明的实名注册环节授权；当前演示不提交真实身份资料。',
            'acceptance':'本地WASM验收已通过，请确认验收。确认后签署验收并按合同金额支付虚拟币。',
            'working':'正在履约；继续poll等待真实环节请求或存证，不需要用户重复授权。',
            'completed':'验收签字及实际虚拟币付款已完成。',
            'declined':'已记录拒绝，停止本地阶段推进；已有提案费用和合同记录保留，不补造解除或退款。',
            'verification-failed':'验收未通过，不签验收、不付款；请与用户讨论修正或仲裁。',
        }
        result['next']=prompts.get(task['gate'],'结果待核实；不要重复付费或重新创建任务。')
        return result

    def begin(self, need, budget, plan_file, spec=None):
        if not isinstance(need,str) or not 1<=len(need)<=2000:raise ValueError('A bounded business request is required')
        plan=json.loads(Path(plan_file).read_text(encoding='utf-8-sig'))
        allowed={'format','query','target','defaults','rejectDescriptions','formation','proposalFeeLimit'}
        if not isinstance(plan,dict) or set(plan)-allowed or plan.get('format')!='ioalab.delegate.plan':
            raise ValueError('Only declarative delegation plans are accepted')
        if not isinstance(plan.get('query'),str) or not 1<=len(plan['query'])<=1000:raise ValueError('Invalid discovery query')
        reject=plan.get('rejectDescriptions',[])
        if not isinstance(reject,list) or len(reject)>12 or any(not isinstance(s,str) or not 1<=len(s)<=200 for s in reject):raise ValueError('Invalid bounded exclusion phrases')
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
            return self.poll(task,kwargs.get('timeout',60))
        if gate=='step':
            self.c.answer_step(task['cardDigest'],True,confirmed=True)
            task['gate']='working';self.save(task)
            return self.poll(task,kwargs.get('timeout',60))
        if gate=='acceptance':
            result=self.c.accept(task['cardDigest'],task['period'],confirmed=True)
            task['payment']=result
            task['gate']='completed' if result.get('status')=='pass' and result.get('voucher') else 'payment-unknown'
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
                reply=self.c.draft(candidate['templateDigest'],task['spec'],budget=task['budget'])
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
        chosen=min(eligible,key=lambda q:(0 if q['verifierKinds']==['script'] else 1,len(q['notCovered']),amount(q['total'])))
        task['recommended']={**chosen,'proposalFees':str(fees),'totalCost':str(amount(chosen['total'])+fees),
                             'reason':'优先确定性脚本验收，再比较未覆盖项和定数价格；请用户确认最终合同'}
        task['cardDigest']=chosen['cardDigest'];task['gate']='contract'
        self.c.compare([q['providerUrl'] for q in task['quotes']],selected=chosen['providerUrl'],reason=task['recommended']['reason'],cd=chosen['cardDigest'])
        return self.save(task)

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
