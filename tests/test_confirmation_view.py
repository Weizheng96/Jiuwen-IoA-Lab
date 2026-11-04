"""Display is derived from the bound preview without changing its signed facts."""
import copy

from ioalab_sdk.runtime.confirmation_view import present


def test_confirmation_view_keeps_original_and_actual_nested_amounts():
    item={'action':'card.confirm','bindingDigest':'original','binding':{'preview':{
        'title':'确认合同','budget':'100.00','card':{'derived':{'total':'86.00'},
        'deliverables':[{'name':'source','acceptance':{'notCovered':['deployment']}}]},
        'materials':{'brief':{'sendAt':'work.start'}}}}}
    original=copy.deepcopy(item);view=present(item)
    assert item==original and view['binding']==original['binding'] and view['bindingDigest']=='original'
    sections={s['label']:s['value'] for s in view['view']['sections']}
    assert sections['总预算上限']=='100.00'
    assert sections['合同总额']=='86.00'
    assert sections['本次金额']=='未提供'
    assert sections['合同对象']['合同金额及期限']['合同总额']=='86.00'


def test_actual_candidate_confirmation_binds_template_delivery_and_acceptance(platform,tmp_path):
    from test_resident_delegation import participants
    from test_resident_contracts import candidate,confirm
    from a2c.template import acceptance_of
    with participants(platform,tmp_path,formation='proposal') as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task)
        pending,_,_=confirm(assistant,quote,approve=False)
        item=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)
        preview=item['binding']['preview'];card=quote['card']
        assert preview['deliverables'][0]['description']==template['deliverables'][0]['description']
        assert preview['deliverables'][0]['acceptance']==acceptance_of(template,card)[template['deliverables'][0]['name']]
        assert preview['derived']==card['derived'] and preview['proposalFee']==template['pricing']['proposalFee']
        assert preview['authorizableSteps']==template.get('authorizableSteps',[])
        sections={x['label']:x['value'] for x in present(item)['view']['sections']}
        assert sections['合同总额']==card['derived']['total']
        assert sections['交付物'][0]['说明']==template['deliverables'][0]['description']
