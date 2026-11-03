"""Native scene build inputs reuse owned behavior source without private configuration."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / 'demo/delegation-provider/src/scenario/build.py'


def builder():
    spec = importlib.util.spec_from_file_location('native_nail_builder', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_scene_build_is_deterministic_and_uses_exact_owned_source(tmp_path):
    from scenario import nail_services
    owned = next(value for _, value in nail_services() if value['id'] == 'nail-mashangfei')
    module = builder()
    report = module.build(tmp_path / 'first')
    again = module.build(tmp_path / 'second')
    assert report == again
    assert report['criteria'] == owned['verifier']['criteria'] and len(report['criteria']) == 10
    assert report['verifierSourceSha256'] == hashlib.sha256(module.utf8(owned['verifier']['source'])).hexdigest()
    with zipfile.ZipFile(tmp_path / 'first/nail-miniapp.zip') as archive:
        assert sorted(archive.namelist()) == ['nail-miniapp/SKILL.md', 'nail-miniapp/references/acceptance.md',
                                              'nail-miniapp/references/production.md']
        document = archive.read('nail-miniapp/references/acceptance.md').decode('utf-8')
        assert '```python\n' + owned['verifier']['source'].rstrip() + '\n```' in document
        assert 'provider.task.verifier.publish' in document and '__CRITERIA__' not in document
        assert all(name in document for name in report['criteria'])
        for name in archive.namelist():
            text = archive.read(name).decode('utf-8')
            assert '\r' not in text and 'ioa_selftest' not in text and 'ioa_deliver' not in text
            assert '__SCENE_ACCEPTANCE__' not in text
    draft = json.loads((tmp_path / 'first/template-draft-input.json').read_text(encoding='utf-8'))
    assert draft['profile'] == 'miniapp' and draft['terms']['formation'] == 'proposal'
    terms = draft['terms']
    assert terms['deliverables'][0]['name'] == 'booking'
    assert terms['deliverables'][0]['specSchema'] == owned['deliverable']['specSchema']
    assert terms['pricing']['proposalFee'] == 1 and terms['authorizableSteps'] == owned['steps']
    assert terms['deadlines']['respondWithin'] == 'PT30M'
    assert owned['deadlines']['respondWithin'] == 'PT10M'
    assert {k: v for k, v in module.definition()['deadlines'].items() if k != 'respondWithin'} == {
        k: v for k, v in owned['deadlines'].items() if k != 'respondWithin'}
    assert terms['samples'] == [] and not {'a2cVersion', 'kind', 'validUntil', 'provider', 'signatures'} & terms.keys()
    assert 'paymentProviders' not in terms['pricing']
    assert [item['name'] for item in terms['inputs']] == ['requirement_text', 'function_spec']
    assert all(item['required'] for item in terms['inputs'])
    execution = json.loads((tmp_path / 'first/execution-fields.json').read_text(encoding='utf-8'))
    assert execution['skills'] == ['nail-miniapp'] and execution['native_tools'] == {'write_file': 'allow'}
    assert not {'sample', 'sample_spec', 'engineSource', 'resources'} & execution.keys()
    upload = json.loads((tmp_path / 'first/verifier-publish-input.json').read_text(encoding='utf-8'))
    assert upload == module.verifier_input(module.definition(), 'miniapp')
    assert upload['source'] == owned['verifier']['source']
    assert report['verifierPreparation'] == 'task_upload' and report['verifierReference'] is None


@pytest.mark.parametrize('fault', ['profile', 'criteria', 'criterion_names', 'source', 'syntax', 'deliverable', 'schema', 'quote', 'deadline'])
def test_invalid_scene_input_is_rejected_before_creating_output(tmp_path, fault):
    module = builder()
    value = deepcopy(module.definition())
    profile = 'miniapp'
    if fault == 'profile': profile = '../another'
    elif fault == 'criteria': value['verifier']['criteria'] = value['verifier']['criteria'][:-1]
    elif fault == 'criterion_names': value['verifier']['criteria'][0] = 'unimplemented claim'
    elif fault == 'source': value['verifier']['source'] = '__CRITERIA__'
    elif fault == 'syntax': value['verifier']['source'] = 'def verify(: invalid'
    elif fault == 'deliverable': value['deliverable']['name'] = 'miniprogram_project'
    elif fault == 'schema': value['deliverable']['specSchema'].pop('capacity')
    elif fault == 'deadline': value['deadlines']['respondWithin'] = 'PT2H'
    else: value['pricing']['range'] = [299, 500]
    with pytest.raises(ValueError):
        module.build(tmp_path / 'never-created', profile=profile, source=value)
    assert not (tmp_path / 'never-created').exists()


def test_generated_business_terms_pass_original_a2c_template_validation():
    from a2c.template import validate, template_digest
    from ioalab_sdk.templates import draft_from_terms
    module = builder()
    request = module.template_input(module.definition(), 'miniapp')
    payment = {'id': 'https://lab.example/pay', 'jwksUri': 'https://lab.example/pay/.well-known/jwks.json'}
    template = draft_from_terms(request['terms'], payment_provider=payment)
    template.update(provider='urn:ioalab:service:native-nail-fixture', signatures={'provider': None})
    template['templateDigest'] = template_digest(template)
    validate(template, endorsed=False)


def test_existing_output_is_preserved_and_cli_has_explicit_failure(tmp_path):
    module = builder()
    output = tmp_path / 'existing'; output.mkdir()
    original = output / 'prior.txt'; original.write_text('preserve')
    with pytest.raises(ValueError, match='new output directory'):
        module.build(output)
    assert list(output.iterdir()) == [original] and original.read_text() == 'preserve'
    result = subprocess.run([sys.executable, str(BUILDER), '--out', str(tmp_path / 'invalid'), '--profile', '../other'],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 1 and json.loads(result.stdout)['status'] == 'rejected'
    assert not (tmp_path / 'invalid').exists()


def test_cli_build_success_needs_no_account_configuration(tmp_path):
    result = subprocess.run([sys.executable, str(BUILDER), '--out', str(tmp_path / 'built')],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'completed'
    assert (tmp_path / 'built/template-draft-input.json').is_file()


def fixture_package(value):
    """An explicit local test package, never a published production receipt."""
    from a2c.codec import digest_bytes
    from a2c.verifier.package import build, load
    verifier = value['verifier']
    kind = verifier.get('kind', 'script')
    manifest = {'id': verifier['id'], 'kind': kind, 'runtime': 'cpython-3.13-wasi',
                'entry': 'verify:verify', 'reads': verifier['reads']}
    blob = build({'verify.py': verifier['source']}, manifest)
    reference = {'id': verifier['id'], 'uri': 'ioalab://pkg/' + digest_bytes(blob), 'digest': digest_bytes(blob)}
    return load(blob, reference, kind), reference


@pytest.mark.parametrize('service_id,amount,kind,count,skill', [
    ('nail-lingxi', 239, 'skill', 3, 'nail-lingxi'),
    ('nail-kuaida', 259, 'script', 5, 'nail-kuaida'),
    ('nail-mashangfei', 299, 'script', 10, 'nail-miniapp'),
])
def test_three_proposals_keep_distinct_business_skills_and_original_verifiers(tmp_path, service_id, amount, kind, count, skill):
    from scenario import nail_services
    owned = next(value for _, value in nail_services() if value['id'] == service_id)
    module = builder()
    value = module.definition(service_id)
    report = module.build(tmp_path / service_id, service_id=service_id, profile=service_id)
    assert report['templateStatus'] == 'draft_ready' and report['verifierKind'] == kind
    assert report['skill'] == skill and len(report['criteria']) == count
    assert value['verifier'] == owned['verifier']  # source, reads, id and dispute model unchanged
    assert not {'sample', 'sample_spec', 'generator'} & value.keys()
    assert report['verifierSourceSha256'] == hashlib.sha256(module.utf8(owned['verifier']['source'])).hexdigest()
    with zipfile.ZipFile(tmp_path / service_id / (skill + '.zip')) as archive:
        acceptance = archive.read(skill + '/references/acceptance.md').decode('utf-8')
        production = archive.read(skill + '/references/production.md').decode('utf-8')
        main = archive.read(skill + '/SKILL.md').decode('utf-8')
        assert f'name: {skill}\n' in main and value['name'] in main
        assert f'## {count} 项实际判据' in acceptance and f'lineItems.booking 为 {amount}' in acceptance
        assert 'acceptance.booking.verifiers.' + kind in acceptance
        assert '```python\n' + owned['verifier']['source'].rstrip() + '\n```' in acceptance
        assert all(item in acceptance and item in production for item in value['verifier']['criteria'])
        assert all(item in acceptance for item in value['notCovered'])
        assert '__SCENE_' not in main + acceptance + production
        assert '推荐码上飞' not in main + acceptance + production
        if kind == 'skill':
            assert 'qwen-plus' in acceptance and '不支持时明确未就绪' in acceptance
            assert report['modelAvailability'] == 'not_checked' and report['disputeModels'] == ['qwen-plus']
        elif count == 5:
            assert '重复提交幂等' in acceptance and '不声明已验证' in production
    request = json.loads((tmp_path / service_id / 'template-draft-input.json').read_text(encoding='utf-8'))
    assert request['terms']['formation'] == 'proposal' and request['terms']['pricing']['proposalFee'] == 1
    assert 'acceptance' not in request['terms']
    assert json.loads((tmp_path / service_id / 'execution-fields.json').read_text(encoding='utf-8'))['skills'] == [skill]
    assert json.loads((tmp_path / service_id / 'publication-fields.json').read_text(encoding='utf-8'))['name'] == value['name']


@pytest.mark.parametrize('service_id', ['nail-mashangfei', 'nail-kuaida', 'nail-lingxi'])
def test_proposals_reuse_real_upload_reference_without_fixing_template_acceptance(tmp_path, service_id):
    module = builder()
    value = module.definition(service_id)
    _, reference = fixture_package(value)
    original = deepcopy(reference)
    output = tmp_path / service_id
    report = module.build(output, service_id=service_id, profile='owned', verifier_reference=reference)
    expected = deepcopy(reference)
    if value['verifier'].get('kind') == 'skill':
        expected['disputeModels'] = value['verifier']['disputeModels']
    assert reference == original  # The real upload receipt remains unchanged.
    assert report['verifierReference'] == expected and report['verifierPreparation'] == 'reuse_published'
    assert report['templateStatus'] == 'draft_ready'
    draft = json.loads((output / 'template-draft-input.json').read_text(encoding='utf-8'))
    assert draft['terms']['formation'] == 'proposal' and 'acceptance' not in draft['terms']
    upload = json.loads((output / 'verifier-publish-input.json').read_text(encoding='utf-8'))
    assert upload == module.verifier_input(value, 'owned') and upload['source'] == value['verifier']['source']
    with zipfile.ZipFile(output / (report['skill'] + '.zip')) as archive:
        document = archive.read(report['skill'] + '/references/acceptance.md').decode('utf-8')
        encoded = document.split('```json\n', 1)[1].split('\n```', 1)[0]
        assert json.loads(encoded) == expected
        assert '不逐单重新转抄' in document and '不调用 provider.task.verifier.publish' in document
        assert '```python\n' + value['verifier']['source'].rstrip() + '\n```' in document
        assert all(item in document for item in value['notCovered'])
    execution = json.loads((output / 'execution-fields.json').read_text(encoding='utf-8'))
    assert '复用其中的真实预发布引用' in execution['instructions']
    assert '不逐单转抄或重新上传' in execution['instructions']


@pytest.mark.parametrize('fault', ['id', 'digest', 'uri', 'missing', 'script_models', 'skill_models', 'extra', 'source'])
def test_proposal_reuse_rejects_invalid_reference_or_source_before_writing(tmp_path, fault):
    module = builder()
    value = module.definition('nail-lingxi' if fault == 'skill_models' else 'nail-mashangfei')
    _, reference = fixture_package(value)
    if fault == 'id': reference['id'] = 'another_service@1.0.0'
    elif fault == 'digest': reference['digest'] = 'invalid'
    elif fault == 'uri': reference['uri'] = 'ioalab://pkg/sha256:' + '0' * 64
    elif fault == 'missing': reference.pop('digest')
    elif fault == 'script_models': reference['disputeModels'] = ['qwen-plus']
    elif fault == 'skill_models': reference['disputeModels'] = ['another-model']
    elif fault == 'extra': reference['source'] = 'def verify(ctx): return True'
    else: value['verifier']['source'] = 'def verify(: invalid'
    with pytest.raises(ValueError):
        module.build(tmp_path / 'invalid', source=value, verifier_reference=reference)
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('service_id,amount', [('nail-weiye', 180), ('nail-jisu', 199),
                                             ('nail-yungou', 219), ('nail-zunxiang', 1280)])
def test_countersign_requires_real_upload_and_retains_material_price_and_scope(tmp_path, service_id, amount):
    from a2c.template import validate, template_digest
    from ioalab_sdk.templates import draft_from_terms
    module = builder()
    value = module.definition(service_id)
    package, reference = fixture_package(value)
    prepared = module.build(tmp_path / 'prepared', service_id=service_id, profile=service_id)
    assert prepared['formation'] == 'countersign' and prepared['templateStatus'] == 'verifier_reference_required'
    assert not (tmp_path / 'prepared/template-draft-input.json').exists()
    upload = json.loads((tmp_path / 'prepared/verifier-publish-input.json').read_text(encoding='utf-8'))
    assert upload == module.verifier_input(value, service_id) and upload['source'] == value['verifier']['source']
    with pytest.raises(ValueError, match='actual management verifier upload'):
        module.template_input(value, service_id)
    completed = module.build(tmp_path / 'referenced', service_id=service_id, profile=service_id,
                             verifier_reference=reference)
    assert completed['templateStatus'] == 'draft_ready'
    terms = json.loads((tmp_path / 'referenced/template-draft-input.json').read_text(encoding='utf-8'))['terms']
    assert terms['formation'] == 'countersign' and terms['pricing']['priceFormula'] == {'booking': str(amount)}
    assert 'proposalFee' not in terms['pricing'] and terms['deadlines']['acceptWithin'] == {'booking': 'PT15M'}
    assert terms['acceptance']['booking'] == {'verifiers': {'script': reference}, 'notCovered': value['notCovered']}
    assert [item['name'] for item in terms['inputs']][:2] == ['requirement_text', 'function_spec']
    if service_id == 'nail-yungou':
        assert terms['inputs'][-1] == {'name': 'architecture', 'description': '委托方须提供软件架构图，签约前必须审核',
                                      'required': True, 'reviewBeforeSigning': True, 'specSchema': {}}
    if service_id == 'nail-weiye':
        assert '不提供顾客自助取消' in terms['deliverables'][0]['description']
        assert '取消后释放名额' not in value['verifier']['criteria']
    elif service_id == 'nail-jisu':
        assert '不提供微信小程序' in terms['deliverables'][0]['description']
        assert 'Android源码结构与预约配置齐全' in value['verifier']['criteria']
    if service_id in ('nail-weiye', 'nail-jisu'):
        assert 'authorizableSteps' not in terms
        assert '微信小程序' not in value['tags']
    assert all(item in terms['acceptance']['booking']['notCovered']
               for item in ('重复提交幂等', '请求重试一致性', '跨时段隔离', '重复取消幂等'))
    template = draft_from_terms(terms, payment_provider={'id': 'https://lab.example/pay',
        'jwksUri': 'https://lab.example/pay/.well-known/jwks.json'})
    template.update(provider='urn:ioalab:service:isolated-fixture', signatures={'provider': None})
    template['templateDigest'] = template_digest(template)
    validate(template, endorsed=False)
    assert package.manifest['kind'] == 'script'


@pytest.mark.parametrize('service_id', ['nail-lingxi', 'nail-kuaida'])
@pytest.mark.parametrize('fault', ['kind', 'source', 'criteria', 'level', 'price', 'materials'])
def test_variants_cannot_borrow_capabilities_or_drop_contract_requirements(tmp_path, service_id, fault):
    module = builder()
    value = module.definition(service_id)
    if fault == 'kind': value['verifier']['kind'] = 'script' if service_id == 'nail-lingxi' else 'skill'
    elif fault == 'source': value['verifier']['source'] = module.definition()['verifier']['source']
    elif fault == 'criteria': value['verifier']['criteria'][0] = 'unimplemented claim'
    elif fault == 'level': value['deliverable']['specSchema']['level']['const'] = 'reliable'
    elif fault == 'price': value['pricing']['range'][1] += 1
    else: value['inputs'] = []
    with pytest.raises(ValueError):
        module.build(tmp_path / 'invalid', source=value, service_id=service_id)
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('fault', ['id', 'digest', 'missing', 'uri', 'proposal'])
def test_wrong_upload_reference_is_rejected_without_partial_artifacts(tmp_path, fault):
    module = builder()
    value = module.definition('nail-yungou')
    _, reference = fixture_package(value)
    if fault == 'id': reference['id'] = 'other_service@1.0.0'
    elif fault == 'digest': reference['digest'] = 'not-a-digest'
    elif fault == 'missing': reference.pop('uri')
    elif fault == 'uri': reference['uri'] = 'ioalab://pkg/sha256:' + '0' * 64
    else: value = module.definition('nail-lingxi')
    with pytest.raises(ValueError):
        module.build(tmp_path / 'invalid', source=value, verifier_reference=reference)
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'unresolved'])
def test_invalid_business_document_template_is_rejected_before_writing(tmp_path, monkeypatch, fault):
    module = builder()
    source_root = module.HERE / 'skills/nail-miniapp'
    copied = tmp_path / 'templates/skills/nail-miniapp'
    (copied / 'references').mkdir(parents=True)
    for relative in ('SKILL.md', 'references/acceptance.md', 'references/production.md'):
        (copied / relative).write_text((source_root / relative).read_text(encoding='utf-8'), encoding='utf-8')
    acceptance = copied / 'references/acceptance.md'
    text = acceptance.read_text(encoding='utf-8')
    if fault == 'missing': text = text.replace('__SCENE_ACCEPTANCE__', 'not the actual criteria')
    elif fault == 'duplicate': text += '\n__SCENE_ACCEPTANCE__'
    else: text += '\n__SCENE_UNKNOWN__'
    acceptance.write_text(text, encoding='utf-8')
    monkeypatch.setattr(module, 'HERE', tmp_path / 'templates')
    with pytest.raises(ValueError, match='placeholder'):
        module.build(tmp_path / 'invalid')
    assert not (tmp_path / 'invalid').exists()


def test_cli_service_selection_and_reference_failures_preserve_existing_data(tmp_path):
    module = builder()
    package, reference = fixture_package(module.definition('nail-yungou'))
    receipt = tmp_path / 'fixture-reference.json'; receipt.write_text(json.dumps(reference), encoding='utf-8')
    args = [sys.executable, str(BUILDER), '--service-id', 'nail-yungou', '--profile', 'yungou',
            '--verifier-reference', str(receipt)]
    good = subprocess.run([*args, '--out', str(tmp_path / 'built')], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert good.returncode == 0, good.stdout + good.stderr
    assert json.loads(good.stdout)['data']['templateStatus'] == 'draft_ready'
    receipt.write_text('{broken json', encoding='utf-8')
    bad = subprocess.run([*args, '--out', str(tmp_path / 'invalid')], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert bad.returncode == 1 and json.loads(bad.stdout)['status'] == 'rejected'
    assert not (tmp_path / 'invalid').exists()
    unknown = subprocess.run([sys.executable, str(BUILDER), '--out', str(tmp_path / 'unknown'), '--service-id', 'nail-unknown'],
                             capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert unknown.returncode == 2 and not (tmp_path / 'unknown').exists()
    assert package.manifest['kind'] == 'script'


@pytest.mark.parametrize('service_id', ['nail-weiye', 'nail-jisu'])
def test_limited_scope_verifier_runs_true_behaviors_and_rejects_false_flags_in_wasm(service_id):
    """Explicit fixtures only; no generated production, platform or model is started."""
    from scenario import nail_services
    from a2c.verifier.runner import run
    module = builder()
    value = module.definition(service_id)
    package, _ = fixture_package(value)
    fixture = deepcopy(next(item for _, item in nail_services() if item['id'] == service_id))
    spec = {**fixture['sample_spec'], 'capacity': 2, 'level': 'basic'}
    values = {'spec.booking.' + key: data for key, data in spec.items()}
    good = fixture['sample']; good.pop('miniProgram')
    if service_id == 'nail-weiye':
        removal = '    def cancel(self, ident):\n        self.rows.pop(ident,None)\n'
        assert removal in good['engineSource']
        good['engineSource'] = good['engineSource'].replace(removal, '')
        good['allowCancel'] = False
        good['displayPage'] = {'index.html': '<html><body>预约查询</body></html>'}
    else:
        good['androidApp'] = {'AndroidManifest.xml': '<manifest package="isolated.fixture"/>',
                              'MainActivity.kt': 'class MainActivity : Activity() { }'}

    def verify(data):
        return run(package, target='booking', values=values,
                   files={'booking.json': json.dumps(data, ensure_ascii=False).encode('utf-8')}, period=1)

    passed = verify(good)
    assert passed.status == 'pass', passed
    assert [row['check'] for row in passed.checks] == value['verifier']['criteria']
    wrong = deepcopy(good); wrong['open'] += 1
    assert verify(wrong).checks[0]['ok'] is False
    wrong = deepcopy(good); wrong['engineSource'] = 'class BookingEngine:\n    def __init__(self, capacity): pass'
    assert verify(wrong).status == 'fail'
    wrong = deepcopy(good)
    if service_id == 'nail-weiye':
        wrong['allowCancel'] = True  # A flag cannot create the missing cancellation capability.
    else:
        wrong['engineSource'] = wrong['engineSource'].replace('self.rows.pop(ident,None)', 'pass')
    assert verify(wrong).checks[3]['ok'] is False
    wrong = deepcopy(good); wrong['miniProgram'] = {'app.json': 'false advertised platform'}
    assert verify(wrong).checks[4]['ok'] is False


def test_three_verifier_kinds_keep_real_difference_in_coverage_in_wasm():
    from scenario import nail_services
    from a2c.verifier.runner import run
    module = builder()
    fixture = deepcopy(next(value for _, value in nail_services() if value['id'] == 'nail-mashangfei'))
    source = fixture['sample']['engineSource']
    needle = "sum(x['slot']==slot for x in self.rows.values()) >= self.capacity"
    assert needle in source
    fixture['sample']['engineSource'] = source.replace(needle, 'len(self.rows) >= self.capacity')
    outcomes = {}
    for key in ('lingxi', 'kuaida', 'mashangfei'):
        value = module.definition('nail-' + key)
        package, _ = fixture_package(value)
        values = {'spec.booking.' + name: data for name, data in fixture['sample_spec'].items()}
        values['spec.booking.level'] = 'reliable' if key == 'mashangfei' else 'basic'
        # Explicit observation fixture: this is not evidence that qwen-plus is available.
        observation = lambda need: {'hasBooking': True, 'hasCancellation': True, 'hasCapacityControl': True}
        result = run(package, target='booking', values=values, period=1,
                     files={'booking.json': json.dumps(fixture['sample'], ensure_ascii=False).encode('utf-8')},
                     resolver=observation if key == 'lingxi' else None)
        outcomes[key] = result.status
        assert [row['check'] for row in result.checks] == value['verifier']['criteria']
        if key == 'mashangfei':
            assert any(row['check'] == '不同时段独立计数' and not row['ok'] for row in result.checks)
    assert outcomes == {'lingxi': 'pass', 'kuaida': 'pass', 'mashangfei': 'fail'}
