"""Bounded manual decisions cannot replace original program or money criteria."""
import pytest
from ioalab.delegation.arbitration_review import decision,request
from ioalab.errors import LabError


def basis(outcome=None):
    return {'ground':'termination-compensation','period':1,'marketOutcome':outcome,
        'card':{'derived':{'periods':[{'amount':9}]}},'template':{'pricing':{'currency':'CNY'}}}


def payload(**changes):
    return {'operationId':'original-review','expectedEvidenceDigest':'sha256:'+'a'*64,
        'reason':'核对原期次证据','liable':'client','compensation':{'amount':'9','currency':'CNY'},**changes}


@pytest.mark.parametrize('amount,currency',[
    ('9.01','CNY'),('-1','CNY'),('1+1','CNY'),('NaN','CNY'),(9,'CNY'),('9','USD'),
])
def test_compensation_is_a_fixed_nonnegative_amount_bounded_by_original_period(amount,currency):
    with pytest.raises(LabError) as invalid:
        decision(basis(),'review',None,payload(compensation={'amount':amount,'currency':currency}),'independent-admin')
    assert invalid.value.code=='invalid_input'


@pytest.mark.parametrize('amount',['0','9'])
def test_compensation_including_zero_is_a_document_only(amount):
    result=decision(basis(),'review',None,payload(compensation={'amount':amount,'currency':'CNY'}),'independent-admin')
    assert result['compensation']=={'amount':amount,'currency':'CNY'}
    assert set(result)=={'rulingKind','liable','operator','reason','evidence','compensation'}


def test_unknown_program_cannot_be_manually_invented_or_known_result_reversed():
    source=basis();source['ground']='acceptance-failed'
    args=payload();args.pop('compensation')
    with pytest.raises(LabError):decision(source,'recompute',None,args,'independent-admin')
    with pytest.raises(LabError):decision(source,'recompute',{'liable':'provider'},args,'independent-admin')
    result=decision(source,'recompute',{'liable':'provider'},{**args,'liable':'none'},'independent-admin')
    assert result['liable']=='none' and result['evidence']['originalEvaluation']=={'liable':'provider'}


def test_existing_conclusion_allows_only_neutral_and_review_rejects_model_shortcuts():
    args=payload();args.pop('compensation')
    with pytest.raises(LabError):decision(basis({'outcome':'completed'}),'review',None,args,'independent-admin')
    for extra in ({'confirmed':True},{'operator':'the-model'},{'reason':'   '}):
        with pytest.raises(LabError):request('review',payload(**extra))
