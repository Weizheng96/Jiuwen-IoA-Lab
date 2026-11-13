"""Generic private-interaction workflow over the original messaging operations."""
from copy import deepcopy

from ..runtime import WorkflowDefinition, RuntimeFault
from ..runtime import form_schema as schema, form_text as text, form_next as next_step
from .messaging import stable


def interaction_form(role):
    initial = schema({'account': text('对方准确账号', minLength=3, maxLength=128)})
    def call(name, operation, args):
        return role._operation('messaging.' + name)(role.runtime, stable(operation, name), args)

    def preview(operation, values, context):
        return {'values': deepcopy(values), 'preview': {'操作': context.get('stage', '选择联系对象'), **values}}

    def follow(context, status):
        choices = ['refresh']
        if not status.get('closed'):
            choices.append('close')
            if status.get('interactionRef'): choices.append('reply')
        return next_step('查看回复与继续交互', schema({'action': text('下一步', enum=choices,
                         enumNames=['刷新结果' if x == 'refresh' else '结束交互' if x == 'close' else '继续发送' for x in choices])}),
                         {**context, 'stage': 'follow', 'interactionRef': status.get('interactionRef')},
                         values={'action': 'refresh'}, evidence=status, data=status)

    def submit(operation, prepared, context):
        values = prepared['values']
        stage = context.get('stage', 'account')
        if stage == 'account':
            interfaces = call('interfaces', operation, values)['data']
            if not interfaces:
                return next_step('没有可用接口，请检查账号或联系对方', initial, {}, values=values,
                                 evidence={'说明': '平台未返回对您开放的接口。'})
            return next_step('选择消息接口', schema({'interfaceRef': text('对方提供的服务',
                enum=[item['interfaceRef'] for item in interfaces],
                enumNames=[item.get('description') or item['name'] for item in interfaces])}),
                {'stage': 'interface', 'interfaces': interfaces}, evidence={'接口': interfaces})
        if stage == 'interface':
            interface = next((item for item in context['interfaces'] if item['interfaceRef'] == values['interfaceRef']), None)
            if interface is None: raise RuntimeFault('invalid_input', 'Select an interface from the original discovery result')
            return next_step('填写消息内容', schema({'input': {**interface['inputSchema'], 'title': '消息内容'}}),
                {'stage': 'send', 'interface': interface}, evidence={'用途': interface.get('description'), '回复格式': interface['outputSchema']})
        if stage == 'send':
            args = {'input': values['input'], 'interfaceRef': context['interface']['interfaceRef']}
            if context.get('interactionRef'): args['interactionRef'] = context['interactionRef']
            sent = call('send', operation, args)
            if sent['status'] not in ('accepted', 'completed'): return sent
            message = sent['data']['messageRef']
            status = call('status', operation, {'messageRef': message})['data']
            return follow({**context, 'messageRef': message}, status)
        if stage == 'follow':
            status = call('status', operation, {'messageRef': context['messageRef']})['data']
            if values['action'] == 'refresh': return follow(context, status)
            if values['action'] == 'close':
                result = call('close', operation, {'messageRef': context['messageRef']})
                if result['status'] == 'accepted':
                    return {'status': 'completed', 'data': {'closeRequested': True, 'receipt': result, 'messageRef': context['messageRef']}}
                return result
            if status.get('closed') or not status.get('interactionRef'):
                raise RuntimeFault('closed', 'Original interaction is not open for another message')
            return next_step('继续发送消息', schema({'input': {**context['interface']['inputSchema'], 'title': '消息内容'}}),
                {**context, 'stage': 'send', 'interactionRef': status['interactionRef']}, evidence=status)
        raise RuntimeFault('protocol_error', 'Unknown private-interaction stage')

    return WorkflowDefinition('messaging.interaction', '发起私人交互', initial, preview, submit)
