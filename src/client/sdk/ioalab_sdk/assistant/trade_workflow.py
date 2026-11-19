"""User choices and deterministic delegation hand-offs over the existing A2C owners."""
from decimal import Decimal, InvalidOperation
from copy import deepcopy
from a2c.card import card_digest
from a2c.codec import fmt_time, parse_duration, parse_time
from a2c.jws import header_of
from .candidates import checked_candidate

from ..runtime import FormDefinition, WorkflowDefinition, RuntimeFault
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


RENEW_DAYS = {'type': 'number', 'title': '续期天数（不改变合同期限）', 'minimum': 1/86400, 'maximum': 7}


def renewal_form(role):
    def preview(operation, values):
        status = role.delegation.status(values['taskRef'])['data']
        return {'values': deepcopy(values), 'preview': {'title': '续期原委托授权', **status,
            'validDays': values['validDays'], '说明': '保留原预算、范围和已发生支出；新有效期以独立确认页为准，不修改合同期限，不包含付款批准。'}}

    def submit(operation, prepared):
        return role.delegation.authorize(operation, prepared['values'], renew=True)

    return FormDefinition('delegation.renew', '续期原委托授权', schema({
        'taskRef': text('原委托引用', minLength=1), 'validDays': RENEW_DAYS}, ['taskRef', 'validDays']), preview, submit)


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
        return next_step('选择候选服务', schema(fields, ['action']), {**context, 'stage': 'service', 'hits': hits, 'query': query,
                          'serviceNames': {**context.get('serviceNames', {}),
                                           **{hit['id']: hit.get('name') for hit in hits}}},
                         values={'action': 'select' if hits else 'search', 'query': query}, evidence=result['data'])

    def active_quotes(context):
        return [item for item in context['quotes'] if item['quoteRef'] not in context.get('expiredQuotes', [])]

    def presentation(context):
        names = {hit['id']: hit.get('name') for hit in context.get('hits', [])}
        names.update(context.get('serviceNames', {}))
        labels = {}
        for index, quote in enumerate(context.get('quotes', []), 1):
            name = names.get(quote['service']) or '未命名服务'
            derived = (quote.get('card') or {}).get('derived') or {}
            try:
                amount = Decimal(str(derived.get('total')))
                total = f'{amount:.2f} 虚拟币' if amount.is_finite() else '金额待核对'
            except InvalidOperation:
                total = '金额待核对'
            count = len(derived.get('periods') or [])
            periods = '单期交付' if count == 1 else f'{count}期交付' if count else '期数待核对'
            labels[quote['quoteRef']] = f'{name} · {total} · {periods} · 报价{index}'
        result = {'choices': {'quoteRef': labels}}
        if context.get('stage') == 'monitor':
            result['directActions'] = {'refresh': '刷新合同状态'}
            if context.get('facts', {}).get('availablePeriods'):
                result['directActions'].update(fetch='下载本期交付', verify='查看本期验收结果')
        return result

    def quotes(context, evidence=None):
        available = active_quotes(context)
        return next_step('比较报价与选择合同', schema({
            'action': text('下一步', enum=['compare', 'select', 'more', 'close'],
                           enumNames=['核对候选依据', '选择此报价并确认合同', '征集更多候选', '关闭任务授权']),
            'quoteRef': text('候选报价', enum=[item['quoteRef'] for item in available],
                             enumNames=[item['service'] for item in available]),
        }, ['action']), {**context, 'stage': 'quotes'}, values={'action': 'compare'},
            evidence=evidence or {'报价': available, '原需求': context['brief'],
                                  '历史过期报价': [item for item in context['quotes'] if item not in available]})

    def monitor(operation, context, evidence=None):
        status = call('status', operation, {'contractRef': context['contractRef']})
        if status['status'] != 'completed': return status
        facts = status['data']
        authorization = call('status', operation, {'taskRef': context['taskRef']}, 'scope')
        if authorization['status'] != 'completed': return authorization
        case = call('dispute_status', operation, {'caseRef': context['caseRef']}) if context.get('caseRef') else None
        choices = ['refresh', 'renew', 'termination_quote', 'terminate', 'dispute']
        if facts.get('stepRequestDigest'): choices.append('answer_step')
        if facts.get('availablePeriods'): choices.extend(['fetch', 'verify', 'accept', 'pay'])
        fields = {'action': text('下一步', enum=choices, enumNames=[{
            'refresh': '刷新履约状态', 'renew': '续期原委托授权', 'termination_quote': '查询协商解除报价', 'terminate': '申请解除', 'dispute': '申请仲裁',
            'answer_step': '处理当前合同环节', 'fetch': '下载本期交付', 'verify': '查看本期验收预览',
            'accept': '申请本期验收与付款确认', 'pay': '恢复已验收期付款'}[item] for item in choices])}
        if facts.get('availablePeriods'):
            fields['period'] = {'type': 'integer', 'title': '交付期数', 'enum': list(range(1, facts['availablePeriods'] + 1))}
        return next_step('合同履约、验收与结算', schema(fields, ['action']),
                         {**context, 'stage': 'monitor', 'facts': facts}, values={'action': 'refresh'},
                         evidence={'合同状态': facts, '委托授权': authorization['data'], **({'操作结果': evidence} if evidence else {}), **({'仲裁状态': case} if case else {})})

    def renew_step(context):
        status = call('status', stable(context['taskRef'], 'renew-preview'), {'taskRef': context['taskRef']})
        if status['status'] != 'completed': return status
        return next_step('续期原委托授权', schema({'validDays': RENEW_DAYS}, ['validDays']),
            {**context, 'stage': 'renew'}, values={'validDays': 1}, evidence={'原授权': status['data'],
                '说明': '仅续期原范围，预算与合同期限不变；续期完成后仍须独立确认付款。'})

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
        if stage in ('spec', 'quote_retry'):
            if stage == 'quote_retry' and context.get('waitForQuoteClosure'):
                iid = context['waitForQuoteClosure']
                closed = any(frame['message'].get('kind') == 'interaction.closed'
                    and frame['message'].get('sender') == 'urn:ioa:platform'
                    and frame['message'].get('interactionId') == iid
                    and frame['message'].get('body', {}).get('reason') == 'proposal-expired'
                    for frame in role.runtime.store.messages(role.identity.agent_id))
                if not closed:
                    return {'status': 'running', 'data': {'waitingFor': 'original_quote_closure',
                        'message': '已记录重新报价请求。正在等待旧报价安全结束；无需重复提交。',
                        'safeCloseAfter': context['safeCloseAfter']}}
            request_values = values if stage == 'spec' else context['requestValues']
            result = call('request', operation, {'taskRef': context['taskRef'], 'service': context['service'],
                'templateDigest': context['template']['templateDigest'], **request_values})
            if result['status'] in ('accepted', 'running', 'unknown', 'failed', 'rejected') and (result.get('data') or {}).get('quoteRef'):
                result = call('status', operation, {'quoteRef': result['data']['quoteRef']})
            if (result.get('data') or {}).get('canRequestAgain'):
                return next_step('原报价请求已过期，可沿用原需求重新申请',
                    schema({'action': text('下一步', enum=['retry'], enumNames=['沿用原委托重新申请报价'])}),
                    {**context, 'stage': 'quote_retry', 'requestValues': request_values,
                     'expiredQuotes': [*context.get('expiredQuotes', []), result['data']['quoteRef']]},
                    values={'action': 'retry'}, evidence={'原请求': result['data'], '原需求': context['brief'], '规格与材料': request_values})
            if result['status'] != 'completed': return result
            return quotes({**context, 'requestValues': deepcopy(request_values), 'waitForQuoteClosure': None,
                           'quotes': [*context['quotes'], result['data']]})
        if stage == 'quotes':
            action = values['action']
            if action == 'close': return call('close', operation, {'taskRef': context['taskRef']})
            if action == 'more': return search(operation, context, context['query'])
            if action == 'compare':
                result = settled(call('compare', operation, {'taskRef': context['taskRef'], 'quoteRefs': [q['quoteRef'] for q in active_quotes(context)]}))
                return quotes(context, result['data']) if result['status'] == 'completed' else result
            ref = values.get('quoteRef')
            if ref not in [item['quoteRef'] for item in active_quotes(context)]: raise RuntimeFault('invalid_input', 'Choose an original quote')
            result = call('confirm', operation, {'quoteRef': ref})
            if result['status'] in ('accepted', 'running'): result = call('status', operation, {'contractRef': result['data']['contractRef']})
            if result['status'] != 'completed': return result
            if not result['data'].get('formed'): raise RuntimeFault('not_ready', 'The original contract has not formed')
            context = {**context, 'contractRef': result['data']['contractRef']}
            started = settled(call('start', operation, {'contractRef': context['contractRef']}))
            return monitor(operation, context, started) if started['status'] == 'completed' else started
        if stage == 'renew':
            result = call('renew', operation, {'taskRef': context['taskRef'], 'validDays': values['validDays']})
            if result['status'] != 'completed': return result
            return monitor(operation, context, result['data'])
        if stage == 'monitor':
            action = values['action']
            if action == 'renew': return renew_step(context)
            if action == 'pay':
                scope = call('status', operation, {'taskRef': context['taskRef']})
                if scope['status'] != 'completed': return scope
                if not scope['data']['authorized']: return renew_step(context)
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

    def recover(record):
        context = record.get('context', {})
        action = record['values'].get('action')
        if context.get('stage') == 'monitor' and action in ('accept', 'pay'):
            label = '付款' if action == 'pay' else '验收'
            data = (record.get('result') or {}).get('data') or {}
            if data.get('reason') != 'expired' or not data.get('confirmationRef'):
                raise RuntimeFault('not_ready', 'Only an expired original acceptance confirmation can be renewed')
            decision = role.runtime.confirmations.get(data['confirmationRef'], detail=True)
            subject = {'contractRef': context['contractRef'], 'period': record['values'].get('period')}
            binding = decision['binding']
            if (decision['status'] != 'expired' or binding['actor'] != role.identity.agent_id
                    or binding['stage'] != 'delegation.' + action or binding['action'] != action
                    or binding['subject'] != subject or binding['operationId'] != data.get('operationId')):
                raise RuntimeFault('identity_conflict', 'Original expired acceptance confirmation does not match')
            following = monitor(stable(data['operationId'], 'recover'), context,
                {'提示': '原' + label + '确认已过期。原合同及记录保留；请重新预览并申请确认，旧批准不会复用。'})
            if 'next' not in following:
                raise RuntimeFault('not_ready', 'Cannot verify the original contract status')
            next_form = following['next']; facts = next_form['context']['facts']; period = subject['period']
            if action == 'pay':
                if not facts.get('formed') or type(period) is not int or not 1 <= period <= facts.get('acceptedPeriods', 0):
                    raise RuntimeFault('not_ready', '原期尚未验收，不能恢复付款。')
                client, _ = role.fulfillment.client(context['contractRef'])
                payment = client.transport.payment_recovery(context['contractRef'], period)
                if payment['response'] is not None:
                    next_form['evidence']['提示'] = '已查到原付款凭证，仅核对原结果，不重新付款。'
                    return next_form
                if payment['attempts'] and not payment['noPending']:
                    raise RuntimeFault('unknown_result', '原付款尝试尚未核实，不能重新申请付款。')
                if facts.get('closed'):
                    raise RuntimeFault('not_ready', '原合同已关闭，请核对结算结果。')
                next_form['title'] = '付款确认已过期：重新申请确认'
                next_form['values'] = {'action': 'pay', 'period': period}
                return next_form
            if (not facts.get('formed') or facts.get('closed') or type(period) is not int
                    or period <= facts.get('acceptedPeriods', 0) or period > facts.get('availablePeriods', 0)):
                raise RuntimeFault('not_ready', '原合同已关闭、该期已验收或尚未交付，请核对原履约结果，不能重复验收。')
            next_form['title'] = '验收确认已过期：重新申请确认'
            next_form['values'] = {'action': 'accept', 'period': period}
            return next_form
        ref = record['values'].get('quoteRef')
        if context.get('stage') != 'quotes' or ref not in [item['quoteRef'] for item in context['quotes']]:
            raise RuntimeFault('not_ready', 'Recover the original selected quote')
        source = role.trading.store.checkpoint(role.trading.namespace, 'quote:' + ref)
        if not source or source['taskRef'] != context['taskRef']:
            raise RuntimeFault('identity_conflict', 'Original quote and task differ')
        verified = checked_candidate(role.trading, source, require_current=False)
        if verified['current'] or source['template']['formation'] != 'proposal':
            raise RuntimeFault('not_ready', 'This quote is not an expired proposal')
        if role.runtime.store.checkpoint(role.contracts.namespace, card_digest(verified['card'])):
            raise RuntimeFault('not_ready', '原合同确认或签署已有记录，请先核对原结果，不能重复报价。')
        request_values = context.get('requestValues')
        if not request_values or request_values['spec'] != verified['card']['spec']:
            raise RuntimeFault('not_ready', '原规格或材料未完整保存，请先核对原业务，不能猜测重建。')
        safe_close = (parse_time(header_of(verified['card']['signatures']['3'])['a2c-ts'])
                      + parse_duration(source['template']['deadlines']['respondWithin'])
                      + parse_duration(source['template']['deadlines']['startWithin']))
        return next_step('报价已过期：沿用原需求重新报价',
            schema({'action': text('下一步', enum=['retry'], enumNames=['沿用原需求重新报价'])}),
            {**context, 'stage': 'quote_retry', 'requestValues': deepcopy(request_values),
             'waitForQuoteClosure': source['interactionId'], 'safeCloseAfter': fmt_time(safe_close),
             'expiredQuotes': [*context.get('expiredQuotes', []), ref]}, values={'action': 'retry'},
            evidence={'提示': '原报价已过期，不能继续签约。原需求与授权保留；重新报价不会复用旧合同批准。',
                      '安全结束时间': fmt_time(safe_close), '原需求': context['brief'], '规格与材料': request_values})['next']

    return WorkflowDefinition('delegation.trade', '发起委托交易', BRIEF, preview, submit, recover=recover, presentation=presentation)



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
