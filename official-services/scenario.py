"""Seven named services from the authored nail-booking screenplay; separate from default domains."""
from copy import deepcopy
from ioalab_services.catalog import definitions, materialize

BASIC_CRITERIA=['店名与容量符合合同', '可以预约并查询', '满额后拒绝新增预约', '取消后释放名额', '小程序源码与预约配置齐全']
EXTRA_CRITERIA=['重复提交不重复占位', '连续竞争请求不超容量', '相同请求重试返回原预约', '不同时段独立计数', '重复取消不误减名额']
OBSERVATION_CRITERIA=['模型观察预约逻辑', '模型观察取消逻辑', '模型观察容量控制逻辑']

ENGINE='''class BookingEngine:
    def __init__(self, capacity):
        self.capacity=capacity
        self.rows={}
        self.requests={}
    def reserve(self, request, slot):
        if request in self.requests:
            return self.requests[request]
        if sum(x['slot']==slot for x in self.rows.values()) >= self.capacity:
            return None
        row={'id':request,'slot':slot}
        self.rows[request]=row
        self.requests[request]=row
        return row
    def cancel(self, ident):
        self.rows.pop(ident,None)
    def list(self):
        return list(self.rows.values())
'''

def nail_services():
    original=next(d for d in definitions() if d['id']=='nail-booking-reliable')
    services=[]
    rows=[('weiye','微页通',180,'preapproval'),('jisu','极速APP工坊',199,'preapproval'),
          ('yungou','云构工作室',219,'preapproval'),('zunxiang','尊享定制',1280,'preapproval'),
          ('lingxi','灵犀小程序',239,'proposal'),('kuaida','快搭建站',259,'proposal'),('mashangfei','码上飞',299,'proposal')]
    for key,name,price,formation in rows:
        d=deepcopy(original);d.update(id='nail-'+key,name=name,formation=formation)
        d['description']=name+'：美甲预约工具及微信小程序源码，支持每时段2人和顾客取消；注册、审核仅演示环节授权，不连接微信。'
        d['tags']=['美甲预约','微信小程序','软件开发'];d['pricing']={'proposalFee':1,'range':[price,price]} if formation=='proposal' else {'price':price}
        d['deliverable']['description']='booking.json：预约配置、engineSource（Python BookingEngine类源码）、miniProgram（微信小程序文件映射）。Python仅在WASM中测试，本地使用固定运行器；不声称已微信注册或上线。'
        d['deliverable']['specSchema']['level']={'const':'reliable' if key=='mashangfei' else 'basic'}
        d['verifier']={'id':'nail_'+key+'@1.0.1','reads':original['verifier']['reads'],'script':'nail_contract.py','criteria':BASIC_CRITERIA+(EXTRA_CRITERIA if key=='mashangfei' else [])}
        d['steps']=[{'name':'realname_registration','description':'授权以本店名义办理小程序实名注册。当前本地演示仅记录授权，不提交真实身份资料，不申请微信账号或审核。'}]
        d['notCovered']=['微信真实主体认证与上线审核','未经授权的个人资料传输','真实多线程压力与网络故障注入']
        d['sample']['shop']='小美美甲店'
        d['sample_spec']['shop']='小美美甲店'
        d['sample']['engineSource']=ENGINE
        d['sample']['miniProgram']={'app.json':'{"pages":["pages/index/index"]}',
            'pages/index/index.js':'Page({data:{bookings:[]},book(){},cancel(){}})',
            'pages/index/index.wxml':'<view>预约列表<button bindtap="book">预约</button><button bindtap="cancel">取消</button></view>'}
        d['sample']['level']='reliable' if key=='mashangfei' else 'basic'
        d['generator']['system']='''生成本单预约工具配置和源码，输出一个JSON对象。shop/open/close/capacity原样遵守规格，services两项按service1Name/service1Minutes及service2Name/service2Minutes原样填写。format为ioalab.booking，allowCancel和idempotent均为true。engineSource为纯Python源码，定义BookingEngine(capacity)：reserve(request,slot)返回{id,slot}字典或满员返回None，list()返回当前未取消记录，cancel(id)可重复调用且不报错。相同request必须返回相同预约而不重复占位，各slot独立计数。不要import外部模块。miniProgram为微信小程序文本文件映射，至少含app.json、pages/index/index.js、pages/index/index.wxml，包含预约和取消页面，必须是真实源码字符串而非说明。源码通过ioa_selftest的text_fields传入，每份源码用path路径段和lines逐行数组，data仅含其他配置，不嵌套长源码字符串。自测pass后ioa_deliver空参数提交同份成果；不声称已接入微信或真实注册。'''
        d['generator']['output']='{"format":"ioalab.booking","shop":"按规格","open":10,"close":20,"capacity":2,"services":[{"name":"按规格","minutes":60},{"name":"按规格","minutes":90}],"allowCancel":true,"idempotent":true,"engineSource":"class BookingEngine: ...","miniProgram":{"app.json":"...","pages/index/index.js":"...","pages/index/index.wxml":"..."}}'
        d['generator']['quote_system']='本服务按约定需求定价'+str(price)+'虚拟币。只输出JSON：{"price":'+str(price)+',"summary":"'+name+'的预约工具与源码交付"}。'
        if key=='weiye':
            d['description']='微页通：预约展示页；不包含顾客自助取消或名额回收，适用于电话登记。'
            d['deliverable']['description']='仅预约展示页，不支持顾客取消预约，不提供容量释放逻辑。'
        if key=='jisu':
            d['description']='极速APP工坊：原生APP开发，不承接微信小程序。'
            d['deliverable']['description']='Android原生APP，不提供微信小程序或微信端源码。'
        if key=='yungou':
            d['formation']='countersign'
            d['inputs']=[{'name':'architecture','description':'委托方须提供软件架构图，签约前必须审核','required':True,'reviewBeforeSigning':True,'specSchema':{}}]
            d['description']='云构工作室：按委托方提供的软件架构图开发小程序，架构图为签约前必要材料。'
        if key=='kuaida': d['notCovered']+=['重复提交幂等','请求重试一致性','跨时段隔离','重复取消幂等']
        if key=='lingxi':
            d['verifier'].update(kind='skill',script='nail_skill.py',disputeModels=['qwen-plus'],criteria=OBSERVATION_CRITERIA)
            d['description']+='采用skill验收，单一仲裁模型qwen-plus；不承诺十项确定性行为测试。'
        elif key in ('kuaida','mashangfei'):
            d['description']+=('十项行为测试，含竞争请求容量、重复提交及请求重试；直接调用源码的顺序模拟，不是网络或多线程压力试验。' if key=='mashangfei' else '五项基础行为测试。')
        services.append(('ISP_008',materialize(d,'nail-booking-scenario')))
    return services
