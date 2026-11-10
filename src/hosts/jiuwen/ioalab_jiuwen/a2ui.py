"""Ordinary Web A2UI 0.8 forms. No SDK approval authority is accessible here."""
import json
import argparse
from pathlib import Path


def requirement_form():
    fields=[('purpose','希望交付什么成果'),('budget','总预算上限（虚拟币，含拟卡费）'),
            ('proposalFeeLimit','拟卡费上限'),('carrier','成果载体'),
            ('requiredFeatures','不可缺少的功能'),('materials','已有及缺失材料'),
            ('evidenceLevel','需要证明到源码、行为、构建还是部署')]
    components=[{'id':'root','component':{'Column':{'children':{'explicitList':[
        'intro',*[name for name,_ in fields],'submit']}}}},
        {'id':'intro','component':{'Text':{'text':{'literalString':'填写需求后准备IoA委托；这不是签名授权，SDK将另行展示可信确认。'}}}}]
    for name,label in fields:
        components.append({'id':name,'component':{'TextField':{
            'label':{'literalString':label},'text':{'path':'/'+name}}}})
    components.extend([
        {'id':'submit','component':{'Button':{'child':'submit-label','action':{
            'name':'ioa_requirements_submit','context':[
                {'key':name,'value':{'path':'/'+name}} for name,_ in fields]}}}},
        {'id':'submit-label','component':{'Text':{'text':{'literalString':'准备IoA委托（下一步单独授权）'}}}}])
    return [{'beginRendering':{'surfaceId':'ioa-requirements','root':'root'}},
            {'surfaceUpdate':{'surfaceId':'ioa-requirements','components':components}},
            {'dataModelUpdate':{'surfaceId':'ioa-requirements','path':'/','contents':[
                {'key':name,'valueString':''} for name,_ in fields]}}]


def candidate_form(task_ref,candidates):
    """Project actual completed compare rows, with no approval or secret fields."""
    def check(value):
        if isinstance(value,dict):
            if any(key.lower() in ('confirmed','granted','token','password','privatekey','secret','authority','authorization') for key in value):
                raise ValueError('Secrets and approval fields are not candidate display input')
            for item in value.values():check(item)
        elif isinstance(value,list):
            for item in value:check(item)
    if not isinstance(task_ref,str) or not task_ref or not isinstance(candidates,list) or not 1<=len(candidates)<=32:
        raise ValueError('Use the actual taskRef and 1-32 original compare rows')
    check(candidates)
    components=[];children=[]
    for index,row in enumerate(candidates):
        if not isinstance(row,dict) or row.get('state') not in ('offered','unsigned'):
            raise ValueError('Only current eligible original candidates can be selected')
        if any(not isinstance(row.get(key),str) or not row[key] for key in ('quoteRef','service','validUntil')):
            raise ValueError('Original candidate references and validity are required')
        if not isinstance(row.get('derived'),dict) or not isinstance(row.get('deliverables'),list):
            raise ValueError('Original amounts and delivery evidence are required')
        coverage=[]
        for delivery in row['deliverables']:
            acceptance=delivery.get('acceptance',{})
            coverage.append({'name':delivery.get('name'),'description':delivery.get('description'),
                'spec':delivery.get('spec'),'criteria':acceptance.get('criteria'),
                'notCovered':acceptance.get('notCovered'),'acceptWithin':delivery.get('acceptWithin')})
        label='candidate-'+str(index);button=label+'-select';children.extend([label,button])
        text='服务：'+row['service']+'\n合同总额：'+str(row['derived'].get('total','未知'))+'\n交付与验收：'+json.dumps(coverage,ensure_ascii=False)
        components.extend([
            {'id':label,'component':{'Text':{'text':{'literalString':text}}}},
            {'id':button,'component':{'Button':{'child':button+'-label','action':{
                'name':'ioa_candidate_select','context':[
                    {'key':'taskRef','value':{'literalString':task_ref}},
                    {'key':'quoteRef','value':{'literalString':row['quoteRef']}},
                    {'key':'validUntil','value':{'literalString':row['validUntil']}}]}}}},
            {'id':button+'-label','component':{'Text':{'text':{'literalString':'选择此候选，另行确认具体合同'}}}}])
    components.insert(0,{'id':'root','component':{'Column':{'children':{'explicitList':children}}}})
    return [{'beginRendering':{'surfaceId':'ioa-candidates','root':'root'}},
            {'surfaceUpdate':{'surfaceId':'ioa-candidates','components':components}}]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates-file',type=Path)
    args=parser.parse_args()
    if args.candidates_file:
        value=json.loads(args.candidates_file.read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict) or set(value)!={'taskRef','candidates'}:parser.error('Use only original taskRef and selected original candidates')
        messages=candidate_form(value['taskRef'],value['candidates'])
    else:messages=requirement_form()
    print('<a2ui-json>'+json.dumps(messages,ensure_ascii=True)+'</a2ui-json>')
