"""Input cleanup and completion claims remain checked outside the platform."""
import pytest
from ioalab_sdk import LabClient

def test_clean_spec():
    t={'deliverables':[{'name':'posts','specSchema':{'shop':{'type':'string'},'count':{'const':3},'n':{'type':'integer'},'p':{'type':'number'}}}]}
    spec,notes=LabClient.clean_spec(t,{'posts':{'shop':'x','count':3,'n':'7','p':'1.5','extra':1}})
    assert spec=={'posts':{'shop':'x','n':7,'p':1.5}}
    assert any('const' in n for n in notes) and any('extra' in n for n in notes)
    assert LabClient.clean_spec(t,{'post':{'shop':'x'}})[0]=={'posts':{'shop':'x'}}
    assert LabClient.clean_spec(t,{})[0]=={'posts':{}}

def test_template_resolution_and_draft(providers,local_client):
    d=providers.selected[0];url=providers.public_url+'/a2a/providers/svc-'+d['id']
    t=local_client.resolve_template(url)
    assert local_client.resolve_template(t['templateDigest'])==t
    with pytest.raises(Exception): local_client.resolve_template('sha256:'+'0'*64)
    out=local_client.draft(t['templateDigest'],{d['deliverable']['name']:{**d['sample_spec'],'extra':'ignored'}})
    assert out['cardDigest'] and any('extra' in n for n in out['notices'])

def test_missing_swarm_fails_explicitly():
    # A final model sentence cannot substitute for a service SDK submission.
    from swarm import SwarmEngine
    with pytest.raises(RuntimeError,match='not installed'):
        SwarmEngine(['definitely-not-a-jiuwen-command']).check()
