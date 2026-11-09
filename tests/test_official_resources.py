"""Owned resources install independently; configuration never performs provider work."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
from a2c.template import validate, template_digest
from ioalab.delegation.packages import PackageStore
from ioalab_services import prepare
from ioalab_services.catalog import definitions, materialize, seed_manifest


PAYMENT = {'id': 'https://lab.example/pay', 'jwksUri': 'https://lab.example/pay/.well-known/jwks.json'}


def test_same_type_candidates_share_one_official_account_and_duplicate_owners_fail(tmp_path,monkeypatch):
    from ioalab_services import catalog
    domains=catalog.load_domains();mapping=catalog.account_of()
    assert len(domains)==8 and len(mapping)==52
    programming=next(domain for domain in domains if domain['slug']=='dev')
    assert len(programming['services'])==10 and {mapping[service['id']] for service in programming['services']}=={'ISP_008'}
    raw=json.loads(catalog.domain_files()[0].read_text())
    one=tmp_path/'one.json';two=tmp_path/'two.json'
    one.write_text(json.dumps(raw),encoding='utf-8')
    for duplicated in ('category','account'):
        other=deepcopy(raw);other['slug']='other'
        if duplicated=='category':other['account']='ISP_999'
        else:other['category']['id']='cat_other'
        two.write_text(json.dumps(other),encoding='utf-8')
        monkeypatch.setattr(catalog,'domain_files',lambda:[one,two])
        with pytest.raises(AssertionError,match='账号'):catalog.load_domains()


def test_all_owned_definitions_prepare_unsigned_templates_and_sample_free_execution(tmp_path):
    packages = PackageStore(tmp_path / 'packages')
    before = seed_manifest()
    for definition in definitions():
        verifier = definition['verifier']
        ref = packages.put(verifier_id=verifier['id'], source=verifier['source'], reads=verifier.get('reads', []))
        value = prepare(definition['id'], profile='service', verifier_ref=ref, payment_provider=PAYMENT,
                        now=datetime(2030, 1, 1, tzinfo=timezone.utc))
        template = value['service']['templates'][0]
        candidate = {**deepcopy(template), 'provider': 'https://provider.example/a2a', 'signatures': {'provider': None}}
        candidate['templateDigest'] = template_digest(candidate)
        validate(candidate, endorsed=False)
        assert template['formation'] in ('countersign', 'proposal')
        assert not {'provider', 'templateDigest', 'signatures'} & template.keys()
        assert template['samples'] == []
        assert template['pricing']['paymentProviders'] == [PAYMENT]
        assert value['service']['categoryId'] == definition['category']
        assert not {'sample', 'sample_spec', 'sample_inputs', 'source'} & value['execution']['resources'].keys()
        assert json.dumps(definition['sample'], ensure_ascii=False) not in json.dumps(value['execution'], ensure_ascii=False)
        assert value['execution']['resources']['acceptance'][definition['deliverable']['name']]['verifiers'] == {'script': ref}
        value['execution']['resources']['pricing']['price'] = 'changed'
    assert seed_manifest() == before
    assert next(item for item in definitions() if item['id'] == 'wechat-posts')['pricing']['price'] == '6'


def test_prepare_requires_published_owned_reference_and_does_not_mutate_inputs():
    ref = {'id': 'wechat_posts@1.0', 'uri': 'ioalab://pkg/sha256:' + 'a' * 64, 'digest': 'sha256:' + 'a' * 64}
    saved = deepcopy(ref)
    value = prepare('wechat-posts', profile='service', verifier_ref=ref, payment_provider=PAYMENT)
    value['execution']['resources']['acceptance']['posts']['verifiers']['script']['uri'] = 'changed'
    assert ref == saved
    for bad in ({**ref, 'id': 'foreign@1'}, {**ref, 'digest': 'invented'}, {**ref, 'extra': True}, {'id': ref['id']}):
        with pytest.raises(ValueError, match='reference'):
            prepare('wechat-posts', profile='service', verifier_ref=bad, payment_provider=PAYMENT)
    with pytest.raises(ValueError, match='Unknown'):
        prepare('not-a-service', profile='service', verifier_ref=ref, payment_provider=PAYMENT)


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
