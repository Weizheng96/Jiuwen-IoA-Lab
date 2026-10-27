import json


def verify(ctx):
    try:
        d=json.loads(ctx.read('booking.json').decode('utf-8'))
        s=ctx.values
        checks=[]
        def test(name,ok): checks.append({'check':name,'ok':bool(ok)})
        test('店名符合需求',d.get('shop')==s['spec.booking.shop'])
        test('营业时间一致',d.get('open')==s['spec.booking.open'] and d.get('close')==s['spec.booking.close'])
        test('容量为约定整数',type(d.get('capacity')) is int and d['capacity']==s['spec.booking.capacity'])
        services=d.get('services',[])
        test('服务项目与时长一致',services==json.loads(s['spec.booking.services']))
        test('本地配置格式',d.get('format')=='ioalab.booking' and len(services)>0)
        if s['spec.booking.level']=='reliable':
            test('支持取消释放名额',d.get('allowCancel') is True)
            test('重复请求幂等',d.get('idempotent') is True)
            test('容量边界',1<=d.get('capacity',0)<=10)
            test('营业时段合法',0<=d.get('open',-1)<d.get('close',25)<=24)
            test('项目唯一且时长合法',len({x['name'] for x in services})==len(services) and all(type(x['minutes']) is int and 15<=x['minutes']<=240 for x in services))
        return {'status':'pass' if all(x['ok'] for x in checks) else 'fail','checks':checks}
    except Exception:
        return {'status':'fail','checks':[{'check':'合法预约配置','ok':False}]}
