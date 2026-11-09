"""Verifier references in published templates resolve only to this platform's own packages."""
from types import SimpleNamespace
import uuid

import pytest
from a2c.codec import digest, utcnow
from a2c.jws import sign
from a2c.slots import sign_template
from ioalab_sdk.runtime.identities import load_identity
from ioalab_sdk.runtime.session import AccountSession
from ioalab_sdk.templates import template_from_definition
from _support import new_user, PASSWORD

SCHEME = 'ioalab://pkg/'
SOURCE = 'def verify(ctx):\n    return {"pass": True}\n'


@pytest.fixture
def service(platform, tmp_path):
    user = new_user(platform)
    account = AccountSession(platform.s.base_url, user['username'], PASSWORD, tmp_path / 'account')
    identity = load_identity(account, tmp_path / 'keys', {'key': 'service', 'role': 'service', 'name': '验收器引用'})
    try:
        yield account, identity
    finally:
        account.close()


def countersign(platform, identity, reference):
    """A provider-signed countersign template whose script verifier is `reference`."""
    definition = {'formation': 'countersign', 'deliverable': {'name': 'result', 'description': '交付'},
                  'pricing': {'price': 1}, 'verifier': {'id': 'check@1.0', 'source': SOURCE}}
    packages = SimpleNamespace(put=lambda **_: reference)
    template = template_from_definition(definition, packages=packages, payment_provider=platform.payment_provider)
    return sign_template({**template, 'provider': identity.protocol_id}, identity.signer, 'provider', utcnow())


def publish(platform, account, identity, template):
    declaration = {'schema': 'ioalab.service/1', 'agentId': identity.agent_id, 'protocolId': identity.protocol_id,
                   'revision': 1, 'name': '验收器引用服务', 'description': '核对验收器引用只来自本平台包',
                   'tags': ['测试'], 'categoryId': platform.discovery.categories()[0]['id'],
                   'templateDigests': [template['templateDigest']]}
    body = {'operationId': str(uuid.uuid4()), 'declaration': declaration,
            'proof': sign(digest(declaration), identity.signer, 'ioa-service', utcnow()), 'templates': [template]}
    return account.http.post(account.base + '/api/v1/services/' + identity.agent_id + '/publications',
                             headers={'Authorization': 'Bearer ' + account.token}, json=body)


def published(platform, identity):
    return platform.db.one('SELECT COUNT(*) AS n FROM registry_publications WHERE actor=?', identity.agent_id)['n']


def test_platform_package_reference_is_endorsed_and_published(platform, service):
    account, identity = service
    reference = platform.packages.put(verifier_id='check@1.0', source=SOURCE, reads=[])
    assert reference['uri'].startswith(SCHEME)
    response = publish(platform, account, identity, countersign(platform, identity, reference))
    assert response.status_code == 200, response.text
    assert published(platform, identity) == 1


@pytest.mark.parametrize('case', ['external-uri', 'unknown-package'])
def test_foreign_or_missing_verifier_package_is_rejected_before_publication(platform, service, case):
    account, identity = service
    real = platform.packages.put(verifier_id='check@1.0', source=SOURCE, reads=[])
    if case == 'external-uri':
        # Same digest, but the bytes would come from a location the platform never vouched for.
        reference = {**real, 'uri': 'https://verifier.invalid/' + real['digest']}
    else:
        other = real['digest'][:-1] + ('0' if real['digest'][-1] != '0' else '1')
        reference = {**real, 'uri': SCHEME + other, 'digest': other}
    response = publish(platform, account, identity, countersign(platform, identity, reference))
    assert response.status_code == 400, response.text
    error = response.json()['error']
    assert error['code'] == 'invalid_input' and error['recovery'] == 'correct_input'
    assert error['message'].startswith('invalid-object:') and reference['uri'] in error['message']
    assert published(platform, identity) == 0
