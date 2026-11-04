"""Owned scene variants; business documents never select a winning provider."""
from copy import deepcopy
from functools import lru_cache
import ast
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
DEFAULT_SERVICE = 'nail-mashangfei'
SKILL = 'nail-miniapp'
SKILLS = {key: (SKILL if key == DEFAULT_SERVICE else key) for key in (
    'nail-weiye', 'nail-jisu', 'nail-yungou', 'nail-zunxiang', 'nail-lingxi', 'nail-kuaida', DEFAULT_SERVICE)}
MATERIALS = [
    {'name': 'requirement_text', 'description': '本单店铺与预约业务需求说明',
     'required': True, 'specSchema': {'description': {'type': 'string', 'maxLength': 10000}}},
    {'name': 'function_spec', 'description': '本单功能、边界及交付规格说明',
     'required': True, 'specSchema': {'specification': {'type': 'string', 'maxLength': 5000}}},
]


@lru_cache(maxsize=1)
def _scene_definitions():
    spec = importlib.util.spec_from_file_location(
        'ioalab_nail_definitions', HERE / 'definitions.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {value['id']: value for _, value in module.nail_services()}


def definition(service_id=DEFAULT_SERVICE):
    """Reuse existing assets without samples or legacy production generators."""
    if service_id not in SKILLS:
        raise ValueError('Choose an explicit supported nail service id')
    original = _scene_definitions()[service_id]
    value = {key: deepcopy(original[key]) for key in (
        'id', 'name', 'description', 'tags', 'formation', 'deliverable', 'inputs',
        'deadlines', 'pricing', 'steps', 'notCovered', 'verifier') if key in original}
    value['deadlines']['respondWithin'] = 'PT30M'
    value['inputs'] = deepcopy(MATERIALS) + value.get('inputs', [])
    if value['formation'] == 'preapproval':
        value['formation'] = 'countersign'

    return value


def skill_name(value):
    return SKILLS[value['id']]


def validate(value, profile):
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile):
        raise ValueError('Use an existing local service profile')
    if not isinstance(value, dict):
        raise ValueError('Use an owned scene definition')
    expected = definition(value.get('id'))
    verifier = value['verifier']
    criteria = verifier.get('criteria')
    if (not isinstance(criteria, list) or not criteria
            or any(not isinstance(item, str) or not item for item in criteria)
            or len(set(criteria)) != len(criteria)):
        raise ValueError('Use the exact distinct criteria belonging to this service')
    source = verifier.get('source')
    if (not isinstance(source, str) or '__CRITERIA__' in source or '__APP_KIND__' in source
            or len(source.encode('utf-8')) > 65536):
        raise ValueError('Use the materialized owned verifier source')
    try:
        tree = ast.parse(source)
        declared = [ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == 'CRITERIA' for target in node.targets)]
    except (SyntaxError, ValueError, TypeError) as exc:
        raise ValueError('The owned verifier must contain valid literal criteria') from exc
    if declared != [criteria]:
        raise ValueError('The reported criteria must exactly match the verifier')
    if value != expected:
        raise ValueError('Keep the selected service scope, criteria, source, materials and price unchanged')
    return value


def verifier_input(value, profile):
    validate(value, profile)
    verifier = value['verifier']
    return {'profile': profile, 'verifierId': verifier['id'], 'kind': verifier.get('kind', 'script'),
            'reads': deepcopy(verifier['reads']), 'source': verifier['source']}


def published_verifier_reference(value, reference):
    """Validate a real upload reference without fetching or inventing its digest."""
    if not isinstance(reference, dict) or reference.get('id') != value['verifier']['id']:
        raise ValueError('Use the actual management verifier upload reference for this service')
    from a2c.template import check_verifiers
    from a2c.errors import A2CError
    result = deepcopy(reference)
    if result.get('uri') != 'ioalab://pkg/' + str(result.get('digest')):
        raise ValueError('Use the exact package URI and digest returned by the management upload')
    kind = value['verifier'].get('kind', 'script')
    if kind == 'skill':
        models = value['verifier']['disputeModels']
        if 'disputeModels' in result and result['disputeModels'] != models:
            raise ValueError('Keep the owned verifier dispute models unchanged')
        result['disputeModels'] = deepcopy(models)
    try:
        check_verifiers({kind: result}, 'scene.acceptance')
    except A2CError as exc:
        raise ValueError(exc.message) from exc
    return result


def template_input(value, profile, *, verifier_reference=None):
    """Business terms only; original A2C validates actual uploaded references."""
    validate(value, profile)
    deadlines = value['deadlines']
    terms = {
        'formation': value['formation'],
        'deliverables': [{key: deepcopy(value['deliverable'][key])
                          for key in ('name', 'description', 'specSchema')}],
        'inputs': deepcopy(value['inputs']),
        'deadlines': {'respondWithin': deadlines['respondWithin'], 'startWithin': deadlines['startWithin'],
                      'deliverWithin': {'booking': deadlines['deliverWithin']}},
        'pricing': {'currency': 'CNY', 'depositFormula': str(value['pricing'].get('deposit', 0)),
                    'paymentWeights': {'booking': 1}}, 'samples': []}
    if value['steps']:
        terms['authorizableSteps'] = deepcopy(value['steps'])
        terms['deadlines']['authorizeWithin'] = deadlines.get('authorizeWithin', 'PT15M')
    if value['formation'] == 'proposal':
        if verifier_reference is not None:
            # A prepublished business resource does not fix proposal-card acceptance.
            published_verifier_reference(value, verifier_reference)
        terms['pricing']['proposalFee'] = value['pricing']['proposalFee']
    else:
        reference = published_verifier_reference(value, verifier_reference)
        kind = value['verifier'].get('kind', 'script')
        terms['deadlines']['acceptWithin'] = {'booking': deadlines['acceptWithin']}
        terms['pricing'].update(priceFactors={'booking': {}}, priceFormula={'booking': str(value['pricing']['price'])})
        terms['acceptance'] = {'booking': {'verifiers': {kind: reference}, 'notCovered': deepcopy(value['notCovered'])}}
    return {'profile': profile, 'terms': terms}


def acceptance_document(value, *, verifier_reference=None):
    validate(value, 'miniapp')
    verifier = value['verifier']
    kind = verifier.get('kind', 'script')
    criteria = '\n'.join(f'{index}. {name}' for index, name in enumerate(verifier['criteria'], 1))
    reads = json.dumps(verifier['reads'], ensure_ascii=False)
    if value['formation'] == 'proposal':
        amount = value['pricing']['range'][0]
        if verifier_reference is not None:
            reference = published_verifier_reference(value, verifier_reference)
            preparation = f'''本场景已通过所有者的 provider.verifier.publish 预发布验收器。
直接复用以下实际上传回执，填入 provider.task.quote 的 acceptance.booking.verifiers.{kind}：

```json
{json.dumps(reference, ensure_ascii=False, indent=2)}
```

先核本单模板、规格与该验收器仍匹配；匹配时不逐单重新转抄下方源码，不调用 provider.task.verifier.publish。
原包不可用或本单判据需变化时停止并说明，不能猜引用、以同名包替代或修改已签判据。'''
        else:
            preparation = f'''优先复用所有者已配置且适合本单的真实验收引用；当前文档尚未绑定预发布回执。
若无可复用引用，先用 provider.task.verifier.publish 上传下方完整源码：verifierId 为 {verifier['id']}，kind 为 {kind}，
reads 为 {reads}。该回调绑定当前任务，不传 profile、UUID 或 Session。
只使用实际成功回执的 id、uri、digest 填入 provider.task.quote 的 acceptance.booking.verifiers.{kind}。
未知上传结果停止本轮并保留原任务，不能编造引用或换源码。'''
        route = f'''仅用于 quote 阶段；读取本单模板、规格和全部实际必需材料。
本场景报价为 {amount} 虚拟币；拟卡费是模板中的另一笔费用，不能作为交付总价。
{preparation}
lineItems.booking 为 {amount}，acceptWithin.booking 为 {value['deadlines']['acceptWithin']}。
acceptance.booking.notCovered 必须逐项列出下文未覆盖范围。'''
    else:
        route = f'''待批准合同固定价为 {value['pricing']['price']} 虚拟币，不收取提案拟卡费。
发布前由所有者通过 provider.verifier.publish 上传下方源码，使用实际回执生成模板 acceptance.booking.verifiers.{kind}。
上传输入的 verifierId 为 {verifier['id']}，kind 为 {kind}，reads 为 {reads}。
尚无真实引用时只有准备材料，不能发布含占位验收器的模板。
本单 decision 仅审核原合同与必需材料，不报价、不上传或替换验收器；缺少材料则拒绝，不能伪造。
本单验收时长为 {value['deadlines']['acceptWithin']}。'''
    observation = ('''这是三项 L2 模型观察：WASM 调用模型观察源码中的预约、取消和容量控制，并检查观察布尔值。
它不运行交付源码的确定性行为测试，不得宣称五项或十项行为已通过。
skill 引用另外配置 disputeModels 为 ''' + json.dumps(verifier['disputeModels'], ensure_ascii=False) + '''。
必须先核目标平台实际支持该仲裁模型且宿主观察可用；定义中的名称不是可用性证明。
不支持时明确未就绪，不替换模型、不降级为 script、不伪造观察。'''
                   if kind == 'skill' else
                   '源码只在 A2C python.wasm 内调用交付核心；不能改为关键词、文件存在或配置布尔值检查。')
    return f'''# {value['name']}：本单验收依据

交付件名称为 booking，文件为 booking.json。严格遵守本服务公开范围，不扩充为其他候选的能力。
{route}

{observation}

## {len(verifier['criteria'])} 项实际判据

{criteria}

## 验收器完整源码

```python
{verifier['source'].rstrip()}
```

## 未覆盖范围

''' + '\n'.join('- ' + text for text in value['notCovered']) + '\n'


def production_document(value):
    validate(value, 'miniapp')
    original = _scene_definitions()[value['id']]
    from ioalab_services.programming import production_document as owned_document
    return ('# ' + value['name'] + '：真实生产\n\n' + owned_document(original)
            + '\n仅在production阶段按已确认合同生产，不使用样件。先核全部必需材料和authorizableSteps；'
              '需要环节时provider.task.request_step等待真实granted，不替用户同意。'
              '实名注册仅演练授权，不提交真实资料或声称上线。'
              '使用provider.task.selftest与provider.task.deliver提交同一本单artifactDirectory的booking.json。'
              '失败修正源码，不改验收依据。未覆盖项不声明已验证。\n')


def execution_input(value, *, verifier_reference=None):
    validate(value, 'miniapp')
    skill = skill_name(value)
    if value['formation'] == 'proposal':
        if verifier_reference is not None:
            published_verifier_reference(value, verifier_reference)
            preparation = 'quote 阶段读取 acceptance.md 并复用其中的真实预发布引用，不逐单转抄或重新上传验收源码；'
        else:
            preparation = 'quote 阶段优先复用本服务适合本单的已配置真实验收引用；没有时读取 acceptance.md 的完整源码，用本单回调取得实际引用后报价；'
    else:
        preparation = 'decision 阶段仅审查原合同和全部必需材料，不上传验收器、不重新报价；'
    return {'instructions': f'按本单阶段读取 {skill} 主文档和对应业务页。' + preparation +
            'production 阶段按合同和 production.md 生成新的实际成果。不能使用样件替代生产。',
            'skills': [skill], 'native_tools': {'write_file': 'allow'}}
