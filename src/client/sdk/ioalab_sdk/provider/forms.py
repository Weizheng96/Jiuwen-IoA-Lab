"""Provider management and publication, without a scenario-specific service schema."""
from copy import deepcopy
import uuid

from ..runtime import WorkflowDefinition, RuntimeFault, encode
from ..runtime import form_schema as schema, form_text as text, form_next as next_step


TERMS = schema({
    'formation': text('交易方式', enum=['proposal', 'countersign'], enumNames=['按需求拟定方案报价', '按固定模板报价']),
    'deliverables': {'type': 'array', 'title': '交付物及规格 Schema', 'minItems': 1, 'items': schema({
        'name': text('交付标识'), 'description': text('交付说明'), 'specSchema': {'type': 'object', 'title': '用户填写的规格'}})},
    'inputs': {'type': 'array', 'title': '需要用户提供的材料', 'items': {'type': 'object'}},
    'pricing': {**schema({'currency': text('币种'), 'proposalFee': {'type': 'number', 'title': '拟卡费', 'minimum': 0},
        'depositFormula': text('订金公式'), 'paymentWeights': {'type': 'object', 'title': '各交付物付款权重'},
        'priceFormula': {'type': 'object', 'title': '固定模板价格公式'}, 'priceFactors': {'type': 'object', 'title': '价格参数'}},
        ['currency', 'depositFormula', 'paymentWeights']), 'title': '计价条款'},
    'deadlines': {**schema({'respondWithin': text('报价响应时限（如 PT10M）'), 'startWithin': text('开工时限'),
        'deliverWithin': {'type': 'object', 'title': '各交付物交付时限'}, 'acceptWithin': {'type': 'object', 'title': '验收时限'},
        'authorizeWithin': text('合同环节授权时限')}, ['respondWithin', 'startWithin', 'deliverWithin']), 'title': '期限'},
    'acceptance': {'type': 'object', 'title': '固定模板验收器引用及未覆盖范围'},
    'authorizableSteps': {'type': 'array', 'title': '需要另行用户授权的合同环节', 'items': {'type': 'object'}},
}, ['formation', 'deliverables', 'pricing', 'deadlines'])

GROUNDS = {'accept-overdue': '用户逾期未验收', 'payment-overdue': '用户逾期未付款',
           'authorize-overdue': '合同环节授权逾期', 'authorize-refused': '合同环节授权被拒绝',
           'termination-compensation': '解除补偿争议', 'declaration-mismatch': '声明不符'}


def management_form(role):
    initial = schema({'profile': text('本机服务身份', enum=list(role.identities))})

    def call(name, operation, args):
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, encode(['provider-form', operation, name])))
        result = role._handler(name)(role.runtime, identifier, args)
        job = (result.get('data') or {}).get('job')
        if job and job.get('reference'):
            job = role.runtime.scheduler.get(job['reference'])
            if job['status'] in ('completed', 'failed', 'rejected', 'unknown'):
                result = {'status': job['status'], 'data': job.get('data') or {}}
        return result

    def menu(operation, context, evidence=None):
        profile = context['profile']
        status = call('status', operation, {'profile': profile})
        if status['status'] != 'completed': return status
        return next_step('管理服务与履约任务', schema({'action': text('下一步',
            enum=['publish', 'pause', 'resume', 'tasks', 'status'],
            enumNames=['发布或更新服务', '暂停接单', '恢复接单', '查看与处理任务', '刷新服务状态'])}),
            {**context, 'stage': 'menu'}, values={'action': 'status'},
            evidence={'服务状态': status['data'], **({'操作结果': evidence} if evidence else {})})

    def tasks(operation, context, evidence=None):
        result = call('tasks.get', operation, {'profile': context['profile']})
        if result['status'] != 'completed': return result
        items = result['data']['items']
        fields = {'action': text('下一步', enum=['inspect', 'resume', 'dispute', 'back'] if items else ['back'],
            enumNames=['查看原任务', '按原任务恢复', '申请本单仲裁', '返回服务管理'] if items else ['返回服务管理'])}
        if items: fields['taskRef'] = text('原任务', enum=[item['taskRef'] for item in items],
            enumNames=[item['phase'] + ' / ' + item['status'] + ' / ' + item['taskRef'] for item in items])
        return next_step('查看或恢复履约任务', schema(fields, ['action']), {**context, 'stage': 'tasks'},
            values={'action': 'inspect' if items else 'back'}, evidence={'任务': result['data'], **({'操作结果': evidence} if evidence else {})})

    def preview(operation, values, context):
        if context.get('stage') == 'publish':
            drafted = call('template.draft', operation, {'profile': context['profile'], 'terms': values['terms']})
            if drafted['status'] != 'completed': raise RuntimeFault('not_ready', 'Template draft is not ready')
            service = {key: value for key, value in values.items() if key != 'terms'}
            service.update(profile=context['profile'], templates=[drafted['data']['template']])
            # Explicit category selection makes this a bounded read/preview, not an async UI guess.
            result = call('preview', operation, {'service': service})
            if result['status'] != 'completed': raise RuntimeFault('not_ready', 'Wait for the original publication preview')
            if not result['data'].get('service'): raise RuntimeFault('invalid_input', 'Choose a current service category')
            return result['data']
        return {'values': deepcopy(values), 'preview': {'操作': context.get('stage', '选择服务'), **values}}

    def submit(operation, prepared, context):
        values = prepared.get('values', {}); stage = context.get('stage', 'profile')
        if stage == 'profile': return menu(operation, {'profile': values['profile']})
        profile = context['profile']
        if stage == 'menu':
            action = values['action']
            if action == 'publish':
                categories = call('categories', operation, {})
                if categories['status'] != 'completed': return categories
                options = categories['data']['categories']
                if not options: raise RuntimeFault('not_ready', 'The platform has no current service categories')
                return next_step('填写服务能力与交易条款', schema({
                    'name': text('服务名称', minLength=1, maxLength=120),
                    'description': text('通用能力说明', minLength=1, maxLength=8000),
                    'tags': {'type': 'array', 'title': '搜索关键词', 'items': {'type': 'string'}, 'maxItems': 20},
                    'categoryId': text('服务分类', enum=[item['id'] for item in options],
                                       enumNames=[item.get('name', item['id']) for item in options]),
                    'terms': {**TERMS, 'title': '能力规格与交易条款'}}),
                    {**context, 'stage': 'publish'}, evidence={'可用分类': categories['data']})
            if action == 'tasks':
                return tasks(operation, context)
            result = call(action, operation, {'profile': profile})
            return menu(operation, context, result['data']) if result['status'] == 'completed' else result
        if stage == 'publish':
            result = call('publish', operation, {key: prepared[key] for key in ('service', 'contentDigest', 'expectedRevision')})
            return menu(operation, context, result['data']) if result['status'] == 'completed' else result
        if stage == 'tasks':
            if values['action'] == 'back': return menu(operation, context)
            if not values.get('taskRef'): raise RuntimeFault('invalid_input', 'Select the original task reference')
            if values['action'] == 'dispute':
                return next_step('申请本单仲裁', schema({'ground': text('申请依据', enum=list(GROUNDS), enumNames=list(GROUNDS.values())),
                    'period': {'type': 'integer', 'title': '涉及期数', 'minimum': 1},
                    'materials': {'type': 'object', 'title': '原始证据引用（可选）'}}, ['ground', 'period']),
                    {**context, 'stage': 'dispute', 'taskRef': values['taskRef']}, evidence={'原任务': values['taskRef']})
            result = call('tasks.get' if values['action'] == 'inspect' else 'task.resume', operation,
                          {'profile': profile, 'taskRef': values['taskRef']})
            if result['status'] not in ('completed', 'accepted'): return result
            return tasks(operation, context, result)
        if stage == 'dispute':
            result = call('dispute', operation, {'profile': profile, 'taskRef': context['taskRef'], **values})
            if result['status'] != 'completed': return result
            context = {**context, 'stage': 'case', 'caseRef': result['data']['caseRef']}
        if context.get('stage') == 'case':
            if values.get('action') == 'back': return tasks(operation, context)
            result = call('dispute_status', operation, {'profile': profile, 'caseRef': context['caseRef']})
            if result['status'] != 'completed': return result
            return next_step('原案件处理状态', schema({'action': text('下一步', enum=['refresh', 'back'], enumNames=['刷新案件', '返回任务'])}),
                             context, values={'action': 'refresh'}, evidence={'案件': result['data']})
        raise RuntimeFault('protocol_error', 'Unknown provider management stage')

    return WorkflowDefinition('services.management', '服务方管理', initial, preview, submit)
