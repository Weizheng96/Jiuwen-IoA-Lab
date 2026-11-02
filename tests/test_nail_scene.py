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
    assert terms['samples'] == [] and not {'a2cVersion', 'kind', 'validUntil', 'provider', 'signatures'} & terms.keys()
    assert 'paymentProviders' not in terms['pricing']
    assert [item['name'] for item in terms['inputs']] == ['requirement_text', 'function_spec']
    assert all(item['required'] for item in terms['inputs'])
    execution = json.loads((tmp_path / 'first/execution-fields.json').read_text(encoding='utf-8'))
    assert execution['skills'] == ['nail-miniapp'] and execution['native_tools'] == {'write_file': 'allow'}
    assert not {'sample', 'sample_spec', 'engineSource', 'resources'} & execution.keys()


@pytest.mark.parametrize('fault', ['profile', 'criteria', 'criterion_names', 'source', 'syntax', 'deliverable', 'schema', 'quote'])
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
