"""Scene parameters consume owned programming capabilities; no parallel business rules."""
from ioalab_services import variant


def nail_services():
    rows = [('weiye', '微页通', 180, 'countersign', 'code-website', 'display'),
            ('jisu', '极速APP工坊', 199, 'countersign', 'code-android', None),
            ('yungou', '云构工作室', 219, 'countersign', 'code-miniapp-basic', 'architecture'),
            ('zunxiang', '尊享定制', 1280, 'countersign', 'code-miniapp-basic', None),
            ('lingxi', '灵犀小程序', 239, 'proposal', 'code-miniapp-basic', None),
            ('kuaida', '快搭建站', 259, 'proposal', 'code-website', None),
            ('mashangfei', '码上飞', 299, 'proposal', 'code-miniapp-reliable', None)]
    result = []
    for key, name, price, formation, service, scope in rows:
        value = variant(service, scope=scope)
        value.update(id='nail-' + key, name=name, formation=formation)
        value['pricing'] = {'proposalFee': 1, 'range': [price, price]} if formation == 'proposal' else {'price': price}
        value['sample_spec'].update(shop='小美美甲店', service1Name='纯色美甲', service2Name='延长甲')
        value['sample'].update(shop='小美美甲店', services=[{'name': '纯色美甲', 'minutes': 60}, {'name': '延长甲', 'minutes': 90}])
        value['steps'] = ([{'name': 'realname_registration', 'description': '仅演练小程序实名注册的授权，不提交真实资料，不申请微信账号或审核。'}]
                          if value['deliverable']['specSchema']['carrier']['const'] == 'wechat-miniapp' else [])
        result.append(('ISP_008', value))
    return result
