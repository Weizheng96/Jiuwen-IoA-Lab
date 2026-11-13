"""Role-owned rule form: common input, original rule preview and confirmed publication."""
from ..runtime import FormDefinition, WorkflowDefinition, RuntimeFault
from ..runtime import form_schema as schema, form_text as text, form_next as next_step
from .messaging import stable


RULE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'name': {'type': 'string', 'title': '接口标识', 'pattern': '^[A-Za-z][A-Za-z0-9_.-]{0,63}$'},
        'description': {'type': 'string', 'title': '用途说明', 'minLength': 1, 'maxLength': 1000},
        'allowedSenders': {'type': ['array', 'null'], 'title': '谁可以发送',
                           'items': {'type': 'string'}, 'uniqueItems': True, 'maxItems': 100},
        'inputSchema': {'type': 'object', 'title': '消息格式（JSON Schema）'},
        'outputSchema': {'type': 'object', 'title': '回复格式（JSON Schema）'},
        'instructions': {'type': 'string', 'title': '收到消息后怎么做', 'minLength': 1, 'maxLength': 16384},
        'tools': {'type': 'array', 'title': '允许使用的本地工具', 'items': {'type': 'string'}, 'uniqueItems': True},
        'enabled': {'type': 'boolean', 'title': '发布后启用'},
    },
    'required': ['name', 'description', 'inputSchema', 'outputSchema', 'instructions', 'allowedSenders', 'tools', 'enabled'],
}


def rule_form(role):
    def preview(operation, values):
        declaration = {key: value for key, value in values.items() if key != 'instructions'}
        result = role._operation('rules.draft')(role.runtime, stable(operation, 'draft'),
                                              {'declaration': declaration, 'instructions': values['instructions']})
        return role._operation('rules.preview')(role.runtime, stable(operation, 'preview'),
                                                {'draftRef': result['data']['draftRef']})['data']

    def submit(operation, prepared):
        return role._operation('rules.publish')(role.runtime, stable(operation, 'publish'), {
            'draftRef': prepared['draftRef'], 'contentDigest': prepared['contentDigest'],
            'expectedRevision': prepared['preview']['previousRevision'],
        })

    return FormDefinition('messaging.interface', '定义消息接口', RULE_SCHEMA, preview, submit)


def interface_management(role):
    initial = schema({'action': text('下一步', enum=['create', 'manage'], enumNames=['定义新接口', '管理已有接口'])})
    definition = rule_form(role)
    def preview(operation, values, context):
        if context.get('stage') == 'edit':
            result = definition.preview(operation, values)
            if context.get('expectedRevision') is not None and result['preview']['previousRevision'] != context['expectedRevision']:
                raise RuntimeFault('id_conflict', 'The selected interface changed; inspect its latest revision')
            return result
        return {'values': values, 'preview': values}
    def submit(operation, prepared, context):
        values = prepared.get('values', {}); stage = context.get('stage', 'start')
        if stage == 'edit': return definition.submit(operation, prepared)
        if stage == 'start' and values['action'] == 'create':
            return next_step('定义消息接口', RULE_SCHEMA, {'stage': 'edit'},
                values={'allowedSenders': [], 'tools': [], 'enabled': True},
                evidence={'本地可用工具': [tool.policy() for tool in role.tools.values()]})
        if stage == 'start':
            versions = role.rules.list()['versions']; latest = {}
            for version in versions:
                name = version['name']
                if name not in latest or version['revision'] > latest[name]['revision']: latest[name] = version
            if not latest: return next_step('尚无接口，请定义新接口', initial, {}, values={'action': 'create'})
            return next_step('选择要更新或停用的接口', schema({'name': text('接口', enum=list(latest)),
                'action': text('操作', enum=['edit', 'disable'], enumNames=['更新接口', '停用接口'])}),
                {'stage': 'select', 'versions': latest}, evidence={'已发布接口': list(latest.values())})
        selected = context['versions'][values['name']]
        version = role.rules.version(values['name'], selected['revision'], validate_tools=False)
        fields = {**version['declaration'], 'instructions': version['instructions']}
        fields.setdefault('enabled', True)
        if values['action'] == 'disable': fields['enabled'] = False
        return next_step('核对接口更新与权限范围', RULE_SCHEMA, {'stage': 'edit', 'expectedRevision': selected['revision']},
                         values=fields, evidence={'原修订': selected['revision'], '本地可用工具': [tool.policy() for tool in role.tools.values()]})
    return WorkflowDefinition('registry.interfaces', '管理消息接口', initial, preview, submit)
