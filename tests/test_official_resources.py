"""Owned resources install independently; configuration never performs provider work."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json

import pytest
from a2c.template import validate, template_digest
from ioalab.delegation.packages import PackageStore
from ioalab_services import prepare_service
from ioalab_services.catalog import definitions, materialize, seed_manifest


PAYMENT = {'id': 'https://lab.example/pay', 'jwksUri': 'https://lab.example/pay/.well-known/jwks.json'}


def test_all_domains_share_one_official_account_and_invalid_owners_fail(tmp_path,monkeypatch):
    from ioalab_services import catalog
    domains=catalog.load_domains();mapping=catalog.account_of()
    assert len(domains)==8 and len(mapping)==52
    programming=next(domain for domain in domains if domain['slug']=='dev')
    assert len(programming['services'])==10 and set(mapping.values())=={'ISP_001'}
    raw=json.loads(catalog.domain_files()[0].read_text(encoding='utf-8'))
    one=tmp_path/'one.json';two=tmp_path/'two.json'
    one.write_text(json.dumps(raw),encoding='utf-8')
    for duplicated in ('category','account'):
        other=deepcopy(raw);other['slug']='other'
        if duplicated=='account':
            other['account']='ISP_999';other['category']['id']='cat_other'
        two.write_text(json.dumps(other),encoding='utf-8')
        monkeypatch.setattr(catalog,'domain_files',lambda:[one,two])
        with pytest.raises(AssertionError,match='分类' if duplicated=='category' else '账号'):catalog.load_domains()


def test_all_owned_definitions_prepare_unsigned_templates_and_sample_free_execution(tmp_path):
    packages = PackageStore(tmp_path / 'packages')
    before = seed_manifest()
    for definition in definitions():
        verifier = definition['verifier']
        ref = packages.put(verifier_id=verifier['id'], source=verifier['source'], reads=verifier.get('reads', []))
        value = prepare_service(definition['id'], profile='service', verifier_ref=ref, payment_provider=PAYMENT,
                        now=datetime(2030, 1, 1, tzinfo=timezone.utc))
        template = value['service']['templates'][0]
        candidate = {**deepcopy(template), 'provider': 'https://provider.example/a2a', 'signatures': {'provider': None}}
        candidate['templateDigest'] = template_digest(candidate)
        validate(candidate, endorsed=False)
        assert template['formation'] in ('countersign', 'proposal')
        assert not {'provider', 'templateDigest', 'signatures'} & template.keys()
        assert template['samples'] == []
        assert template['pricing']['paymentProviders'] == [PAYMENT]
        name = definition['deliverable']['name']
        deadlines = template['deadlines']
        resources = value['execution']['resources']
        assert deadlines['respondWithin'] == 'PT24H'
        assert deadlines['startWithin'] == 'PT30M'
        assert deadlines['deliverWithin'] == {name: 'PT30M'}
        assert resources['acceptWithin'] == {name: 'PT24H'}
        if template['formation'] != 'proposal':
            assert deadlines['acceptWithin'] == resources['acceptWithin']
        if definition.get('steps'):
            assert deadlines['authorizeWithin'] == 'PT24H'
            assert resources['requiredAuthorizations'] == definition['steps']
        assert value['service']['categoryId'] == definition['category']
        assert not {'sample', 'sample_spec', 'sample_inputs', 'source'} & value['execution']['resources'].keys()
        assert json.dumps(definition['sample'], ensure_ascii=False) not in json.dumps(value['execution'], ensure_ascii=False)
        assert value['execution']['resources']['acceptance'][definition['deliverable']['name']]['verifiers'] == {'script': ref}
        value['execution']['resources']['pricing']['price'] = 'changed'
    assert seed_manifest() == before
    assert next(item for item in definitions() if item['id'] == 'wechat-posts')['pricing']['price'] == '6'


@pytest.mark.parametrize('configured', [None, 'PT2H'])
def test_official_defaults_and_explicit_overrides_keep_template_resources_consistent(monkeypatch, configured):
    from ioalab_services import profiles
    definition = next(d for d in definitions() if d['id'] == 'ppt-outline')
    for key in ('respondWithin', 'acceptWithin', 'authorizeWithin'):
        if configured is None:
            definition['deadlines'].pop(key, None)
        else:
            definition['deadlines'][key] = configured
    original = deepcopy(definition)
    monkeypatch.setattr(profiles, 'definitions', lambda: [definition])
    ref = {'id': definition['verifier']['id'], 'uri': 'urn:owned', 'digest': 'sha256:' + 'a' * 64}
    value = prepare_service(definition['id'], profile='test', verifier_ref=ref, payment_provider=PAYMENT)
    dl = value['service']['templates'][0]['deadlines']
    expected = configured or 'PT24H'
    assert dl['respondWithin'] == dl['authorizeWithin'] == expected
    assert dl['acceptWithin'] == value['execution']['resources']['acceptWithin'] == {definition['deliverable']['name']: expected}
    assert definition == original


def test_official_windows_use_original_protocol_anchors_and_capped_pause():
    from a2c.slots import Chain
    from a2c.timing import StepWindow, accept_deadline, deliver_deadline, status
    definition = next(d for d in definitions() if d['id'] == 'ppt-outline')
    ref = {'id': definition['verifier']['id'], 'uri': 'urn:owned', 'digest': 'sha256:' + 'a' * 64}
    at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    template = prepare_service(definition['id'], profile='test', verifier_ref=ref,
                               payment_provider=PAYMENT, now=at)['service']['templates'][0]
    card = {'fulfillment': {}}
    step = StepWindow(1, at + timedelta(minutes=1), None)
    chain = Chain(effective=at, receipt=at)
    due = at + timedelta(minutes=30, hours=24)
    assert deliver_deadline(template, chain, 1, [step], at + timedelta(days=2)) == due
    before = status(template, card, chain, step.requested + timedelta(hours=24), [step])
    after = status(template, card, chain, step.requested + timedelta(hours=24, seconds=1), [step])
    assert not before.provider_grounds
    assert any('authorizeWithin' in g for g in after.provider_grounds)
    chain.periods[5] = [at + timedelta(minutes=5)]
    deadline = chain.periods[5][0] + timedelta(hours=24)
    saved = json.loads(json.dumps(template))
    renewed = {**deepcopy(saved), 'validUntil': '2030-04-01T00:00:00Z'}
    for original in (template, saved, renewed):
        assert accept_deadline(original, card, chain, 1) == deadline
        assert not status(original, card, chain, deadline).provider_grounds
        assert any('acceptWithin' in g for g in status(original, card, chain, deadline + timedelta(seconds=1)).provider_grounds)
    legacy = deepcopy(saved)
    legacy['deadlines']['acceptWithin'] = {definition['deliverable']['name']: 'PT15M'}
    assert accept_deadline(legacy, card, chain, 1) == chain.periods[5][0] + timedelta(minutes=15)


def test_prepare_requires_published_owned_reference_and_does_not_mutate_inputs():
    ref = {'id': 'wechat_posts@1.0', 'uri': 'ioalab://pkg/sha256:' + 'a' * 64, 'digest': 'sha256:' + 'a' * 64}
    saved = deepcopy(ref)
    value = prepare_service('wechat-posts', profile='service', verifier_ref=ref, payment_provider=PAYMENT)
    value['execution']['resources']['acceptance']['posts']['verifiers']['script']['uri'] = 'changed'
    assert ref == saved
    for bad in ({**ref, 'id': 'foreign@1'}, {**ref, 'digest': 'invented'}, {**ref, 'extra': True}, {'id': ref['id']}):
        with pytest.raises(ValueError, match='reference'):
            prepare_service('wechat-posts', profile='service', verifier_ref=bad, payment_provider=PAYMENT)
    with pytest.raises(ValueError, match='Unknown'):
        prepare_service('not-a-service', profile='service', verifier_ref=ref, payment_provider=PAYMENT)


@pytest.mark.parametrize('script', ['../calendar.py', 'folder/verifier.py', '/tmp/verifier.py', 'verifier.txt'])
def test_verifier_source_cannot_escape_owned_resources(script):
    with pytest.raises(ValueError, match='owned resource'):
        materialize({'id': 'untrusted', 'verifier': {'id': 'untrusted@1', 'script': script}}, 'test')


def unowned_verifiers(directory):
    from ioalab_services import catalog
    referenced={definition['verifier'].get('script') for definition in catalog.definitions()}
    return {path.name for path in directory.iterdir() if path.is_file() and path.name.endswith('.py')}-referenced-{'common.py'}


def test_every_packaged_verifier_script_is_referenced_by_a_definition(tmp_path):
    from ioalab_services import catalog
    assert unowned_verifiers(catalog.VERIFIERS_DIR)==set()
    (tmp_path/'common.py').write_text('',encoding='utf-8');(tmp_path/'wechat_posts.py').write_text('',encoding='utf-8');(tmp_path/'orphan.py').write_text('',encoding='utf-8')
    assert unowned_verifiers(tmp_path)=={'orphan.py'}
