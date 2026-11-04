"""Real HTTP protocol loop for named services; explicit sample production only in this test."""
import threading
import time
from decimal import Decimal
import pytest
from conftest import free_port,wait_for


@pytest.mark.parametrize('candidate_key',['kuaida','mashangfei'])
def test_scene_verifier_rejects_mismatched_spec_and_broken_source_structure(candidate_key):
    """Explicit fixture source runs only inside WASM, never as real production."""
    from copy import deepcopy
    import json
    from _nail_scene import nail_services
    from a2c.codec import digest_bytes
    from a2c.verifier.package import build,load
    from a2c.verifier.runner import run
    definition=next(d for _,d in nail_services() if d['id']=='nail-'+candidate_key)
    verifier=definition['verifier']
    manifest={'id':verifier['id'],'kind':'script','runtime':'cpython-3.13-wasi','entry':'verify:verify','reads':verifier['reads']}
    blob=build({'verify.py':verifier['source']},manifest)
    reference={'id':manifest['id'],'digest':digest_bytes(blob),'uri':'ioalab://pkg/'+digest_bytes(blob)}
    package=load(blob,reference,'script')
    spec={**definition['sample_spec'],**{key:domain['const'] for key,domain in definition['deliverable']['specSchema'].items() if 'const' in domain}}
    values={'spec.booking.'+key:value for key,value in spec.items()}
    def verify(data):
        return run(package,target='booking',values=values,files={'booking.json':json.dumps(data,ensure_ascii=False).encode('utf-8')},period=1)
    good=deepcopy(definition['sample']);baseline=verify(good)
    assert baseline.status=='pass' and len(baseline.checks)==len(verifier['criteria'])
    for field in ('open','close'):
        wrong=deepcopy(good);wrong[field]+=1;result=verify(wrong)
        assert result.status=='fail' and result.checks[0]['ok'] is False
    for field,value in (('name','与合同不同'),('minutes',15)):
        wrong=deepcopy(good);wrong['services'][0][field]=value;result=verify(wrong)
        assert result.status=='fail' and result.checks[0]['ok'] is False
    for manifest_text in ('invalid JSON, deliberately long enough',json.dumps({'pages':['pages/missing/missing']})):
        wrong=deepcopy(good);wrong['miniProgram']['app.json']=manifest_text;result=verify(wrong)
        assert result.status=='fail' and result.checks[4]['ok'] is False
    wrong=deepcopy(good);wrong.pop('engineSource');assert verify(wrong).status=='fail'
