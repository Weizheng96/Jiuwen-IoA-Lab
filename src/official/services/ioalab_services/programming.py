"""Pure access to owned programming assets; no platform or Demo imports."""
from copy import deepcopy
from importlib import resources
import json


def definition(service_id):
    from .catalog import materialize
    domain = json.loads(resources.files(__package__).joinpath('assets/domains/08-dev.json').read_text(encoding='utf-8'))
    value = next((item for item in domain['services'] if item['id'] == service_id), None)
    if value is None or not service_id.startswith('code-'):
        raise ValueError('Unknown owned programming service')
    return materialize(deepcopy(value), '08-dev')


def production_document(value):
    """Business instructions belong to the same asset used for publication."""
    return value['generator']['system'] + '\n' + '\n'.join(
        f'{index}. {criterion}' for index, criterion in enumerate(value['verifier']['criteria'], 1))


def variant(service_id, *, scope=None):
    """Concrete owned scope variants; callers supply only scene names/prices."""
    from .catalog import materialize
    value = definition(service_id)
    if scope == 'display':
        value['deliverable']['specSchema']['carrier'] = {'const': 'display'}
        value['deliverable']['specSchema']['mode'] = {'const': 'booking'}
        value['verifier']['criteria'][3] = '不提供自助取消接口'
        value['verifier']['id'] = 'code_website_display@1.0.0'
        value['description'] = '预约展示页源码及登记核心，不提供顾客自助取消和名额释放，不交付微信小程序。'
        value['deliverable']['description'] = value['description']
        value['notCovered'] += ['顾客自助取消与取消后名额释放']
        value['sample']['displayPage'] = value['sample'].pop('website')
        source = value['sample']['engineSource']
        start = source.index('    def cancel(')
        end = source.index('    def list(', start)
        value['sample']['engineSource'] = source[:start] + source[end:]
        value['sample']['allowCancel'] = False
        value['sample_spec'].pop('mode')
        value['generator']['system'] = ('交付booking.json：按合同生成预约配置及engineSource，BookingEngine提供reserve/list，'
            '不定义cancel；allowCancel=false；displayPage含index.html/app.js/style.css真实展示页源码。'
            'shop/open/close/capacity/mode与合同一致，mode=booking，services按合同两项名称时长生成。'
            '不交付miniProgram，不提供顾客自助取消。仅在A2C WASM中运行核心源码；源码结构不证明浏览器行为。')
        value['tags'] = ['软件开发', '展示网站']
    elif scope == 'architecture':
        value['inputs'] = [{'name': 'architecture', 'description': '委托方须提供软件架构图，签约前必须审核',
                            'required': True, 'reviewBeforeSigning': True, 'specSchema': {}}]
    elif scope is not None:
        raise ValueError('Unknown programming scope')
    return materialize(value, 'owned-programming-variant')
