"""Ordinary A2UI values cannot manufacture a protected SDK approval."""
import pytest
from ioalab_jiuwen.a2ui import requirement_form, candidate_form


def test_requirement_form_submits_only_the_seven_editable_value_paths():
    form=requirement_form()
    assert list(form[0])==['beginRendering']
    components=form[1]['surfaceUpdate']['components']
    button=next(x['component']['Button'] for x in components if x['id']=='submit')
    assert button['action']['name']=='ioa_requirements_submit'
    context=button['action']['context']
    assert {x['key'] for x in context}=={'purpose','budget','proposalFeeLimit','carrier','requiredFeatures','materials','evidenceLevel'}
    assert all(x['value']=={'path':'/'+x['key']} for x in context)


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
