"""Pure native nail-scene assembly from the current owned business assets."""
from copy import deepcopy
import ast
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]
SKILL = 'nail-miniapp'


def definition():
    """Reuse the current single definition owner without executing its samples."""
    spec = importlib.util.spec_from_file_location(
        'ioalab_nail_definitions', ROOT / 'official-services/scenario.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = next(value for _, value in module.nail_services()
                    if value['id'] == 'nail-mashangfei')
    # Samples and the legacy generator are not native production inputs.
    return {key: deepcopy(original[key]) for key in (
        'id', 'name', 'description', 'tags', 'category', 'formation', 'deliverable',
        'deadlines', 'pricing', 'steps', 'notCovered', 'verifier') if key in original}


def validate(value, profile):
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile):
        raise ValueError('Use an existing local service profile')
    verifier = value['verifier']
    criteria = verifier.get('criteria')
    if (not isinstance(criteria, list) or len(criteria) != 10
            or any(not isinstance(item, str) or not item for item in criteria)
            or len(set(criteria)) != 10):
        raise ValueError('The reliable scene requires ten distinct behavior criteria')
    if (not isinstance(verifier.get('source'), str) or '__CRITERIA__' in verifier['source']
            or len(verifier['source'].encode('utf-8')) > 65536):
        raise ValueError('Use the materialized owned behavior verifier source')
    try:
        tree = ast.parse(verifier['source'])
        declared = [ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == 'CRITERIA' for target in node.targets)]
    except (SyntaxError, ValueError, TypeError) as exc:
        raise ValueError('The owned verifier must contain valid literal behavior criteria') from exc
    if declared != [criteria]:
        raise ValueError('The reported criteria must exactly match the behavior verifier')
    if value['deliverable']['name'] != 'booking' or value['formation'] != 'proposal':
        raise ValueError('The native scene uses the booking proposal contract')
    for name in ('shop', 'open', 'close', 'capacity', 'level', 'service1Name',
                 'service1Minutes', 'service2Name', 'service2Minutes'):
        if name not in value['deliverable']['specSchema']:
            raise ValueError('The behavior verifier requires the declared booking scalar fields')
    return value


def template_input(value, profile):
    """Business terms only; provider.template.draft owns protocol metadata."""
    validate(value, profile)
    deadlines = value['deadlines']
    return {'profile': profile, 'terms': {
        'formation': 'proposal',
        'deliverables': [{key: deepcopy(value['deliverable'][key])
                          for key in ('name', 'description', 'specSchema')}],
        'inputs': [
            {'name': 'requirement_text', 'description': '本单店铺与预约业务需求说明',
             'required': True, 'specSchema': {'description': {'type': 'string', 'maxLength': 10000}}},
            {'name': 'function_spec', 'description': '本单功能、边界及交付规格说明',
             'required': True, 'specSchema': {'specification': {'type': 'string', 'maxLength': 5000}}},
        ],
        'deadlines': {'respondWithin': deadlines['respondWithin'],
                      'startWithin': deadlines['startWithin'],
                      'deliverWithin': {'booking': deadlines['deliverWithin']},
                      'authorizeWithin': deadlines.get('authorizeWithin', 'PT15M')},
        'pricing': {'currency': 'CNY', 'depositFormula': str(value['pricing'].get('deposit', 0)),
                    'proposalFee': value['pricing']['proposalFee'], 'paymentWeights': {'booking': 1}},
        'authorizableSteps': deepcopy(value['steps']), 'samples': [],
    }}


def acceptance_document(value):
    validate(value, 'miniapp')
    verifier = value['verifier']
    criteria = '\n'.join(f'{index}. {name}' for index, name in enumerate(verifier['criteria'], 1))
    reads = json.dumps(verifier['reads'], ensure_ascii=False)
    amount = value['pricing']['range']
    if len(amount) != 2 or amount[0] != amount[1]:
        raise ValueError('Configure the actual deterministic scene quote amount')
    return f'''# 本单报价与真实行为判据

仅用于 quote 阶段；读取本单模板、规格和两份实际材料。交付件名称必须为 booking，文件为 booking.json。
本场景报价为 {amount[0]} 虚拟币；拟卡费是模板中的另一笔费用，不能作为交付总价。
验收时长使用 {value['deadlines']['acceptWithin']}，不是模板响应期限。

先用 provider.task.verifier.publish 上传下方完整源码：verifierId 为 {verifier['id']}，kind 为 script，
reads 为 {reads}。该回调绑定当前任务，不传 profile、UUID 或 Session。
只使用实际成功回执的 id、uri、digest 填入 provider.task.quote 的 acceptance.booking.verifiers.script。
lineItems.booking 为 {amount[0]}，acceptWithin.booking 为 {value['deadlines']['acceptWithin']}。
下列程序是本业务的实际行为标准；不能改为关键词、文件存在或配置布尔值检查。
未知上传结果停止本轮并保留原任务，不能编造引用或换源码。

## 十项实际检查

{criteria}

## 待打包的完整源码

```python
{verifier['source'].rstrip()}
```

## 未覆盖范围

''' + '\n'.join('- ' + text for text in value['notCovered']) + '\n'


def execution_input(value):
    validate(value, 'miniapp')
    return {'instructions': '按本单阶段读取 nail-miniapp 主文档和对应业务页。'
            'quote 阶段读取 acceptance.md 中的完整行为源码，通过本单验收器回调取得真实引用后报价；'
            'production 阶段按合同和 production.md 生成新的实际成果。不能使用样件替代生产。',
            'skills': [SKILL], 'native_tools': {'write_file': 'allow'}}
