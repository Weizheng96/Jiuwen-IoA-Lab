"""Model-facing argument errors should explain the contract's actual field shape."""
import pytest
from ioalab_sdk.client import LabClient

def test_unique_deliverable_flat_array_fields_and_actionable_errors():
    template={'deliverables':[{'name':'table','specSchema':{'medicines':{'type':'array'},'days':{'type':'integer'}}}]}
    flat={'medicines':['vitamin-c'],'days':'7'}
    cleaned,notices=LabClient.clean_spec(template,flat)
    assert cleaned=={'table':{'medicines':['vitamin-c'],'days':7}}
    assert notices
    assert LabClient.clean_spec(template,{'table':{'medicines':['vitamin-c'],'days':7}})[0]==cleaned
    with pytest.raises(ValueError,match='must be an object'):
        LabClient.clean_spec(template,{'table':['vitamin-c']})
    with pytest.raises(ValueError,match='keyed by deliverable'):
        LabClient.clean_spec(template,['vitamin-c'])
