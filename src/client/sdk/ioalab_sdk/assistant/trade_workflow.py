"""User choices and deterministic delegation hand-offs over the existing A2C owners."""
from copy import deepcopy

from ..runtime import WorkflowDefinition, RuntimeFault
from ..runtime import form_schema as schema, form_text as text, form_next as next_step
from .messaging import stable


BRIEF = schema({
    'purpose': text('目标、交付物、限制与验收要求', minLength=1, maxLength=2000,
                    description='填写通用业务需求；随后会按服务方模板逐项核对规格和材料。'),
    'budget': {'type': 'number', 'title': '总预算（含拟卡费）', 'minimum': 0},
    'proposalFeeLimit': {'type': 'number', 'title': '拟卡费上限', 'minimum': 0},
    'query': text('搜索服务的关键词', minLength=1, maxLength=1000),
    'validDays': {'type': 'number', 'title': '授权有效天数', 'minimum': 1/86400, 'maximum': 7},
}, ['purpose', 'budget', 'proposalFeeLimit', 'query'])

GROUNDS = {'acceptance-failed': '交付未通过原验收标准', 'start-overdue': '逾期开工',
           'deliver-overdue': '逾期交付', 'fetch-failed': '原取件失败',
           'termination-refund': '解除后的退款争议', 'declaration-mismatch': '交付与声明不符'}


def spec_fields(domains):
    # A2C specSchema is a field-domain map. Constants belong to the template,
    # and must never be copied into the buyer's filled spec.
    return schema({key: deepcopy(domain) for key, domain in domains.items() if 'const' not in domain})


def delegation_form(role):
    def call(name, operation, args, suffix=''):
        return role._operation('delegation.' + name)(role.runtime, stable(operation, name, suffix), args)

    def settled(result):
        job = (result.get('data') or {}).get('job')
        if job and job.get('reference'):
            job = role.runtime.scheduler.get(job['reference'])
            if job['status'] in ('completed', 'failed', 'rejected', 'unknown'):
                return {'status': job['status'], 'data': job.get('data') or {}, 'error': job.get('error')}
            if job['status'] == 'waiting' and (job.get('data') or {}).get('confirmationRef'):
                return {'status': 'waiting', 'data': job['data']}
        return result

    def search(operation, context, query):
        result = call('search', operation, {'taskRef': context['taskRef'], 'query': query})
        if result['status'] != 'completed': return result
        hits = result['data'].get('hits', [])
        fields = {'action': text('下一步', enum=['search', 'select', 'close'] if hits else ['search', 'close'],
                                enumNames=['重新搜索', '选择服务', '关闭任务授权'] if hits else ['重新搜索', '关闭任务授权']),
                  'query': text('搜索关键词', minLength=1)}
        if hits: fields['service'] = text('服务方', enum=[hit['id'] for hit in hits],
                                          enumNames=[hit.get('name', hit['id']) for hit in hits])
        return next_step('选择候选服务', schema(fields, ['action']), {**context, 'stage': 'service', 'hits': hits, 'query': query},
                         values={'action': 'select' if hits else 'search', 'query': query}, evidence=result['data'])

    def quotes(context, evidence=None):
        return next_step('比较报价与选择合同', schema({
            'action': text('下一步', enum=['compare', 'select', 'more', 'close'],
                           enumNames=['核对候选依据', '选择此报价并确认合同', '征集更多候选', '关闭任务授权']),
            'quoteRef': text('候选报价', enum=[item['quoteRef'] for item in context['quotes']],
                             enumNames=[item['service'] for item in context['quotes']]),
        }, ['action']), {**context, 'stage': 'quotes'}, values={'action': 'compare'},
            evidence=evidence or {'报价': context['quotes'], '原需求': context['brief']})

    def monitor(operation, context, evidence=None):
        status = call('status', operation, {'contractRef': context['contractRef']})
        if status['status'] != 'completed': return status
        facts = status['data']
        case = call('dispute_status', operation, {'caseRef': context['caseRef']}) if context.get('caseRef') else None
        choices = ['refresh', 'termination_quote', 'terminate', 'dispute']
        if facts.get('stepRequestDigest'): choices.append('answer_step')
        if facts.get('availablePeriods'): choices.extend(['fetch', 'verify', 'accept', 'pay'])
        fields = {'action': text('下一步', enum=choices, enumNames=[{
            'refresh': '刷新履约状态', 'termination_quote': '查询协商解除报价', 'terminate': '申请解除', 'dispute': '申请仲裁',
            'answer_step': '处理当前合同环节', 'fetch': '下载本期交付', 'verify': '查看本期验收预览',
            'accept': '申请本期验收与付款确认', 'pay': '恢复已验收期付款'}[item] for item in choices])}
        if facts.get('availablePeriods'):
            fields['period'] = {'type': 'integer', 'title': '交付期数', 'enum': list(range(1, facts['availablePeriods'] + 1))}
        return next_step('合同履约、验收与结算', schema(fields, ['action']),
                         {**context, 'stage': 'monitor', 'facts': facts}, values={'action': 'refresh'},
                         evidence={'合同状态': facts, **({'操作结果': evidence} if evidence else {}), **({'仲裁状态': case} if case else {})})

    def preview(operation, values, context):
        return {'values': deepcopy(values), 'preview': {'阶段': context.get('stage', '委托授权'), **values}}

    def submit(operation, prepared, context):
        values = prepared['values']; stage = context.get('stage', 'brief')
        if stage == 'brief':
            begun = call('begin', operation, {key: value for key, value in values.items() if key != 'query'})
            if begun['status'] != 'completed': return begun
            return search(operation, {'taskRef': begun['data']['taskRef'], 'brief': values['purpose'], 'quotes': []}, values['query'])
        if stage == 'service':
            action = values['action']
            if action == 'close': return call('close', operation, {'taskRef': context['taskRef']})
            if action == 'search': return search(operation, context, values.get('query', context['query']))
            service = values.get('service')
            if service not in [item['id'] for item in context['hits']]:
                raise RuntimeFault('invalid_input', 'Choose a discovered service')
            inspected = call('inspect', operation, {'service': service})
            if inspected['status'] != 'completed': return inspected
            templates = inspected['data']['templates']
            if not templates: raise RuntimeFault('not_ready', 'This service has no current template; search again')
            return next_step('选择服务条款', schema({'templateDigest': text('服务模板',
                enum=[item['templateDigest'] for item in templates],
                enumNames=['、'.join(item['description'] for item in template['deliverables']) for template in templates])}),
                {**context, 'stage': 'template', 'service': service, 'templates': templates}, evidence=inspected['data'])
        if stage == 'template':
            template = next(item for item in context['templates'] if item['templateDigest'] == values['templateDigest'])
            specs = {item['name']: {**spec_fields(item['specSchema']), 'title': item.get('description', item['name'])} for item in template['deliverables']}
            materials = {item['name']: schema({'text': text('材料正文'), 'spec': {**spec_fields(item.get('specSchema', {})), 'title': '材料规格'}}, ['text', 'spec'])
                         for item in template.get('inputs', [])}
            fields = {'spec': {**schema(specs), 'title': '按原需求核对交付规格'}}
            if materials: fields['materials'] = {**schema(materials, [item['name'] for item in template['inputs'] if item.get('required')]), 'title': '提供材料'}
            required = ['spec'] + (['materials'] if any(item.get('required') for item in template.get('inputs', [])) else [])
            return next_step('填写交付规格与材料', schema(fields, required), {**context, 'stage': 'spec', 'template': template},
                             evidence={'原需求': context['brief'], '真实模板': template})
        if stage == 'spec':
            result = call('request', operation, {'taskRef': context['taskRef'], 'service': context['service'],
                'templateDigest': context['template']['templateDigest'], **values})
            if result['status'] in ('accepted', 'running'):
                result = call('status', operation, {'quoteRef': result['data']['quoteRef']})
            if result['status'] != 'completed': return result
            return quotes({**context, 'quotes': [*context['quotes'], result['data']]})
        if stage == 'quotes':
            action = values['action']
            if action == 'close': return call('close', operation, {'taskRef': context['taskRef']})
            if action == 'more': return search(operation, context, context['query'])
            if action == 'compare':
                result = settled(call('compare', operation, {'taskRef': context['taskRef'], 'quoteRefs': [q['quoteRef'] for q in context['quotes']]}))
                return quotes(context, result['data']) if result['status'] == 'completed' else result
            ref = values.get('quoteRef')
            if ref not in [item['quoteRef'] for item in context['quotes']]: raise RuntimeFault('invalid_input', 'Choose an original quote')
            result = call('confirm', operation, {'quoteRef': ref})
            if result['status'] in ('accepted', 'running'): result = call('status', operation, {'contractRef': result['data']['contractRef']})
            if result['status'] != 'completed': return result
            if not result['data'].get('formed'): raise RuntimeFault('not_ready', 'The original contract has not formed')
            context = {**context, 'contractRef': result['data']['contractRef']}
            started = settled(call('start', operation, {'contractRef': context['contractRef']}))
            return monitor(operation, context, started) if started['status'] == 'completed' else started
        if stage == 'monitor':
            action = values['action']
            if action == 'refresh': return monitor(operation, context)
            if action == 'terminate':
                return next_step('解除合同', schema({'mode': text('解除方式', enum=['agreed', 'unilateral'], enumNames=['协商解除', '单方解除']),
                    'reason': text('原因', maxLength=1000)}, ['mode']), {**context, 'stage': 'terminate'}, evidence=context.get('termination', {}))
            if action == 'dispute':
                return next_step('选择仲裁依据', schema({'ground': text('申请依据', enum=list(GROUNDS), enumNames=list(GROUNDS.values()))}),
                                 {**context, 'stage': 'dispute_reason'}, evidence=context['facts'])
            args = {'contractRef': context['contractRef']}
            if action == 'answer_step': args['stepRequestDigest'] = context['facts']['stepRequestDigest']
            elif action in ('fetch', 'verify', 'accept', 'pay'):
                if 'period' not in values: raise RuntimeFault('invalid_input', 'Choose the original delivery period')
                args['period'] = values['period']
                if action in ('verify', 'accept'):
                    fetched = settled(call('fetch', operation, args))
                    if fetched['status'] != 'completed': return fetched
            result = settled(call(action, operation, args))
            if result['status'] != 'completed': return result
            if action == 'termination_quote': context = {**context, 'termination': result['data']}
            return monitor(operation, context, result['data'])
        if stage == 'dispute_reason':
            fields = {'materials': {'type': 'object', 'title': '原始证据引用（可选）'}}
            required = []
            if values['ground'] != 'start-overdue':
                fields['period'] = {'type': 'integer', 'minimum': 1, 'title': '涉及期数'}; required.append('period')
            return next_step('核对仲裁对象与材料', schema(fields, required),
                             {**context, 'stage': 'dispute', 'ground': values['ground']}, evidence={'依据': GROUNDS[values['ground']], '合同': context['facts']})
        if stage in ('terminate', 'dispute'):
            args = {'contractRef': context['contractRef'], **values}
            if stage == 'dispute': args['ground'] = context['ground']
            if stage == 'terminate' and values['mode'] == 'agreed':
                ref = context.get('termination', {}).get('terminationQuoteRef')
                if not ref: raise RuntimeFault('not_ready', 'First obtain an actual termination quote')
                args['terminationQuoteRef'] = ref
            result = settled(call(stage, operation, args))
            if stage == 'dispute' and result['status'] == 'completed': context = {**context, 'caseRef': result['data']['caseRef']}
            return monitor(operation, context, result['data']) if result['status'] == 'completed' else result
        raise RuntimeFault('protocol_error', 'Unknown delegation workflow stage')

    return WorkflowDefinition('delegation.trade', '发起委托交易', BRIEF, preview, submit)


def case_form(role):
    """An original request may need a dispute while its main form is still waiting."""
    initial = schema({'object': text('原业务对象', enum=['quote', 'contract'], enumNames=['原报价请求', '已成立合同']),
                      'reference': text('原报价或合同引用', minLength=1)})

    def call(name, operation, args):
        return role._operation('delegation.' + name)(role.runtime, stable(operation, 'case', name), args)

    def preview(operation, values, context):
        return {'values': deepcopy(values), 'preview': values}

    def submit(operation, prepared, context):
        values = prepared['values']; stage = context.get('stage', 'object')
        if stage == 'object':
            target = {values['object'] + 'Ref': values['reference']}
            result = call('status', operation, target)
            if result['status'] not in ('completed', 'accepted', 'running'): return result
            grounds = GROUNDS if values['object'] == 'contract' else {'proposal-overdue': '已付拟卡费后逾期未报价'}
            return next_step('选择争议依据', schema({'ground': text('申请依据', enum=list(grounds), enumNames=list(grounds.values()))}),
                             {'stage': 'reason', 'target': target}, evidence=result['data'])
        if stage == 'reason':
            fields = {'materials': {'type': 'object', 'title': '原始证据引用（可选）'}}; required = []
            if values['ground'] not in ('start-overdue', 'proposal-overdue'):
                fields['period'] = {'type': 'integer', 'title': '涉及期数', 'minimum': 1}; required.append('period')
            return next_step('核对争议材料', schema(fields, required),
                             {**context, 'stage': 'submit', 'ground': values['ground']}, evidence=context['target'])
        if stage == 'submit':
            result = call('dispute', operation, {**context['target'], 'ground': context['ground'], **values})
            if result['status'] != 'completed': return result
            context = {**context, 'stage': 'status', 'caseRef': result['data']['caseRef']}
        result = call('dispute_status', operation, {'caseRef': context['caseRef']})
        if result['status'] != 'completed': return result
        return next_step('原案件处理状态', schema({'action': text('下一步', enum=['refresh'], enumNames=['刷新案件'])}),
                         context, values={'action': 'refresh'}, evidence=result['data'])

    return WorkflowDefinition('delegation.case', '处理原委托争议', initial, preview, submit)
