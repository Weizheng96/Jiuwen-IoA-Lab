"""Ordinary A2UI values cannot manufacture a protected SDK approval."""
import pytest
from ioalab_jiuwen.a2ui import requirement_form, requirement_values, candidate_form


def test_requirement_form_binds_editable_values_and_rejects_approval_fields():
    form=requirement_form()
    assert list(form[0])==['beginRendering']
    components=form[1]['surfaceUpdate']['components']
    button=next(x['component']['Button'] for x in components if x['id']=='submit')
    assert button['action']['name']=='ioa_requirements_submit'
    assert all('path' in x['value'] for x in button['action']['context'])
    event={'event':{'userAction':{'name':'ioa_requirements_submit','context':{'budget':'20'}}}}
    assert requirement_values(event)=={'budget':'20'}
    event['event']['userAction']['context']['confirmed']=True
    with pytest.raises(ValueError):requirement_values(event)


def test_candidate_form_preserves_original_choice_and_separates_proof_levels():
    import json
    row={'quoteRef':'original-quote','service':'svc-original','state':'offered','validUntil':'2030-01-01T00:00:00Z',
        'derived':{'total':'9.00'},'deliverables':[{'name':'source','description':'source only',
            'acceptance':{'criteria':'source behavior','notCovered':['actual deployment']}}]}
    form=candidate_form('original-task',[row]);text=json.dumps(form)
    button=next(x['component']['Button'] for x in form[1]['surfaceUpdate']['components'] if 'Button' in x['component'])
    context={x['key']:x['value']['literalString'] for x in button['action']['context']}
    assert context['taskRef']=='original-task' and context['quoteRef']=='original-quote'
    assert '9.00' in text and 'actual deployment' in text and button['action']['name']=='ioa_candidate_select'
    with pytest.raises(ValueError):candidate_form('original-task',[{**row,'confirmed':True}])
    with pytest.raises(ValueError):candidate_form('original-task',[{**row,'state':'expired'}])
