"""Owned software assets: true core behavior in WASM, explicit client limits."""
from copy import deepcopy
import json

import pytest
from ioalab_services.programming import definition, production_document


def verifier(value):
    from a2c.codec import digest_bytes
    from a2c.verifier.package import build, load
    from a2c.verifier.runner import run
    v = value['verifier']
    blob = build({'verify.py': v['source']}, {'id': v['id'], 'kind': 'script',
        'runtime': 'cpython-3.13-wasi', 'entry': 'verify:verify', 'reads': v['reads']})
    package = load(blob, {'id': v['id'], 'uri': 'ioalab://pkg/' + digest_bytes(blob), 'digest': digest_bytes(blob)}, 'script')
    def check(data, *, specification=None):
        contract = value if specification is None else specification
        filled = {**contract['sample_spec'], **{key: domain['const'] for key, domain in contract['deliverable']['specSchema'].items() if 'const' in domain}}
        return run(package, target='booking', values={'spec.booking.' + k: v for k, v in filled.items()},
                   files={'booking.json': json.dumps(data, ensure_ascii=False).encode()}, period=1)
    return check


# Each former test_nail_flow.py case is owned by the corresponding carrier case.
# Keep both actual scene specifications and every original negative assertion;
# only duplicate package build/load is eliminated, not WASM behavior checks.
SCENE_REGRESSIONS = {'code-website': 'kuaida', 'code-miniapp-reliable': 'mashangfei'}


def scene_regressions(value, check, candidate_key):
    from _nail_scene import nail_services
    scene = next(d for _, d in nail_services() if d['id'] == 'nail-' + candidate_key)
    # Reusing a package is valid only while the scene consumes the exact asset.
    assert scene['verifier'] == value['verifier']
    good = deepcopy(scene['sample'])
    def verify(data):
        return check(data, specification=scene)
    baseline = verify(good)
    assert baseline.status == 'pass' and len(baseline.checks) == len(scene['verifier']['criteria'])
    for field in ('open', 'close'):
        wrong = deepcopy(good); wrong[field] += 1
        result = verify(wrong)
        assert result.status == 'fail' and result.checks[0]['ok'] is False, field
    for field, replacement in (('name', '与合同不同'), ('minutes', 15)):
        wrong = deepcopy(good); wrong['services'][0][field] = replacement
        result = verify(wrong)
        assert result.status == 'fail' and result.checks[0]['ok'] is False, field
    if candidate_key == 'mashangfei':
        for manifest_text in ('invalid JSON, deliberately long enough', json.dumps({'pages': ['pages/missing/missing']})):
            wrong = deepcopy(good); wrong['miniProgram']['app.json'] = manifest_text
            result = verify(wrong)
            assert result.status == 'fail' and result.checks[4]['ok'] is False, manifest_text
    else:
        wrong = deepcopy(good); wrong['website'].pop('app.js')
        result = verify(wrong)
        assert result.status == 'fail' and result.checks[4]['ok'] is False
    wrong = deepcopy(good); wrong.pop('engineSource')
    assert verify(wrong).status == 'fail'


@pytest.mark.parametrize('service', ['code-miniapp-reliable', 'code-miniapp-basic', 'code-website', 'code-android'])
def test_owned_carriers_accept_real_core_and_reject_wrong_carrier_or_broken_source(service):
    value = definition(service)
    check = verifier(value)
    assert check(value['sample']).status == 'pass'
    broken = deepcopy(value['sample']); broken['engineSource'] = 'class BookingEngine: pass'
    assert check(broken).status == 'fail'
    wrong = deepcopy(value['sample'])
    field = {'wechat-miniapp': 'miniProgram', 'website': 'website', 'android': 'androidApp'}[value['deliverable']['specSchema']['carrier']['const']]
    wrong.pop(field)
    wrong['unrelatedCarrier'] = {'index.html': 'Cannot substitute another carrier'}
    assert check(wrong).status == 'fail'
    assert '客户端界面实际行为' in value['notCovered'] and '公网部署' in value['notCovered']
    assert all(c in production_document(value) for c in value['verifier']['criteria'])
    if service in SCENE_REGRESSIONS:
        scene_regressions(value, check, SCENE_REGRESSIONS[service])


def test_non_nail_website_catalog_checks_actual_filtering_without_booking_requirement():
    value = definition('code-website')
    value['sample_spec'].update(shop='社区课程中心', mode='catalog', service1Name='陶艺体验', service2Name='摄影入门')
    value['sample'].update(shop='社区课程中心', mode='catalog', services=[{'name': '陶艺体验', 'minutes': 60}, {'name': '摄影入门', 'minutes': 90}])
    value['sample']['engineSource'] = '''class Catalog:
    def __init__(self, items): self.items = items
    def list(self): return self.items
    def search(self, keyword): return [x for x in self.items if keyword in x['name']]
'''
    value['sample']['website']['index.html'] = '<html><head><link rel="stylesheet" href="style.css"></head><body><input id="query"><pre id="items"></pre><script src="app.js"></script></body></html>'
    value['sample']['website']['app.js'] = 'const items=[{name:"陶艺体验",minutes:60},{name:"摄影入门",minutes:90}]; const q=document.getElementById("query"); const render=()=>{document.getElementById("items").textContent=JSON.stringify(items.filter(x=>x.name.includes(q.value)));}; q.oninput=render;render();'
    check = verifier(value)
    passed = check(value['sample'])
    assert passed.status == 'pass' and len(passed.checks) == 5
    broken = deepcopy(value['sample'])
    broken['engineSource'] = broken['engineSource'].replace("if keyword in x['name']", 'if True')
    failed = check(broken)
    assert failed.status == 'fail' and failed.checks[2]['ok'] is False
    # This is a core-source and client-structure proof; no browser was executed.
    assert '客户端界面实际行为' in value['notCovered']


def test_resource_definitions_are_detached_and_reject_unknown_service():
    value = definition('code-website')
    value['deliverable']['specSchema'].clear()
    assert definition('code-website')['deliverable']['specSchema']
    with pytest.raises(ValueError):
        definition('../private')
