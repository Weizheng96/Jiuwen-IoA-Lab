"""Rule units reached directly, without the platform or resident runtime: payment scopes,
admission identifiers, provider quote schema, arbitration evidence and candidate checks."""
from types import SimpleNamespace
import uuid

from jsonschema import Draft202012Validator
import pytest

from ioalab.errors import LabError
from ioalab.payments import scopes
from ioalab.delegation import admission
from ioalab_sdk.adapters import arbitration
from ioalab_sdk.assistant import candidates
from ioalab_sdk.provider.policy import definition_digest, quote_schema
from ioalab_sdk.runtime.host import AgentDefinition, ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault


def rejected(call, *args):
    with pytest.raises(LabError) as caught:
        call(*args)
    assert caught.value.code == 'invalid_input' and caught.value.status == 400
    return str(caught.value)


@pytest.mark.parametrize('value', ['0.00', '1.50', '10.05', '9999999999999.99'])
def test_amount_accepts_two_decimal_strings_up_to_thirteen_integer_digits(value):
    assert str(scopes.amount(value)) == value


@pytest.mark.parametrize('value', ['1.5', '01.00', '-1.00', '1.000', '10000000000000.00', '1,00', '', 1.0, 100, None])
def test_amount_rejects_non_canonical_or_non_string_values(value):
    assert 'two decimal places' in rejected(scopes.amount, value)


def test_encode_is_canonical_and_rejects_values_json_cannot_carry():
    assert scopes.encode({'b': 1, 'a': [1, {'d': 2, 'c': '中'}]}) == '{"a":[1,{"c":"中","d":2}],"b":1}'
    assert scopes.encode({'x': 1}) == admission.encode({'x': 1})
    for bad in ({'x': float('nan')}, {'x': object()}, {'x': b'bytes'}):
        assert 'JSON values' in rejected(scopes.encode, bad)


@pytest.mark.parametrize('value,ok', [
    ('sha256:' + 'a' * 64, True), ('sha256:' + '0' * 64, True), ('sha256:' + 'A' * 64, False),
    ('sha256:' + 'a' * 63, False), ('md5:' + 'a' * 64, False), ('a' * 64, False), (None, False)])
def test_scope_digest_requires_lowercase_sha256_reference(value, ok):
    if ok:
        assert scopes.ScopeLedger._scope_digest(value) == value
    else:
        rejected(scopes.ScopeLedger._scope_digest, value)


def context(**changes):
    value = {'schema': 'ioalab.scope/1', 'scopeId': str(uuid.uuid4()), 'platform': 'http://127.0.0.1:1',
             'actor': 'did:ioalab:assistant', 'user': {'id': 'user-1', 'jwk': {'kty': 'OKP', 'crv': 'Ed25519', 'x': 'AA'}},
             'purpose': '隔离测试委托', 'categoryIds': None, 'currency': 'CNY', 'budget': '100.00', 'proposalFeeLimit': '3.00'}
    value.update(changes)
    return value


def test_delegation_context_accepts_the_complete_bound_user_scope():
    assert scopes.ScopeLedger._context(context()) is None
    assert scopes.ScopeLedger._context(context(categoryIds=[], proposalFeeLimit='100.00')) is None
    assert scopes.ScopeLedger._context(context(categoryIds=['cat_dev', 'cat_nail'], user={'id': 'u', 'jwk': {'kid': 'k', 'kty': 'OKP', 'crv': 'Ed25519', 'x': 'AA'}})) is None


@pytest.mark.parametrize('changes,message', [
    ({'schema': 'ioalab.scope/2'}, 'complete delegation context'),
    ({'extra': 1}, 'complete delegation context'),
    ({'scopeId': 'not-a-uuid'}, 'canonical UUID'),
    ({'scopeId': str(uuid.uuid4()).upper()}, 'canonical UUID'),
    ({'purpose': ''}, 'bounded context fields'),
    ({'platform': 'x' * 2049}, 'bounded context fields'),
    ({'actor': 7}, 'bounded context fields'),
    ({'user': {'id': 'u'}}, 'original user signing identity'),
    ({'user': {'id': '', 'jwk': {'kty': 'OKP', 'crv': 'Ed25519', 'x': 'AA'}}}, 'original user signing identity'),
    ({'user': {'id': 'u', 'jwk': {'kty': 'OKP', 'crv': 'Ed25519', 'x': 'AA', 'd': 'private'}}}, 'public Ed25519 user key'),
    ({'user': {'id': 'u', 'jwk': {'kty': 'EC', 'crv': 'P-256', 'x': 'AA'}}}, 'public Ed25519 user key'),
    ({'categoryIds': ['a', 'a']}, 'distinct exact category IDs'),
    ({'categoryIds': ['c%d' % i for i in range(33)]}, 'distinct exact category IDs'),
    ({'categoryIds': 'cat_dev'}, 'distinct exact category IDs'),
    ({'categoryIds': ['']}, 'distinct exact category IDs'),
    ({'currency': 'USD'}, 'CNY'),
    ({'proposalFeeLimit': '100.01'}, 'part of the total budget'),
    ({'budget': '1e2'}, 'two decimal places'),
])
def test_delegation_context_rejects_each_violation_with_its_own_reason(changes, message):
    assert message in rejected(scopes.ScopeLedger._context, context(**changes))


def test_identifier_normalizes_canonical_uuid_and_rejects_anything_else():
    value = str(uuid.uuid4())
    assert admission.identifier(value) == value
    assert admission.identifier(value.upper()) == value
    assert admission.identifier('{' + value + '}') == value
    for bad in ('abc', '', None, 12, value[:-1]):
        with pytest.raises(LabError) as caught:
            admission.identifier(bad)
        assert caught.value.code == 'invalid_input' and 'UUID' in str(caught.value)


def test_stamp_is_utc_rfc3339_with_z_suffix():
    assert admission.stamp(0) == '1970-01-01T00:00:00Z'
    assert admission.stamp(1.5) == '1970-01-01T00:00:01.500000Z'
    assert admission.stamp(1_700_000_000) == '2023-11-14T22:13:20Z'


def quote(names, **changes):
    value = {'acceptance': {name: {'verifiers': {'script': {'id': 'v', 'uri': 'ioalab://pkg/sha256:00', 'digest': 'sha256:00'}},
                                   'notCovered': []} for name in names},
             'acceptWithin': {name: 'P1D' for name in names},
             'lineItems': {name: 10 for name in names}}
    value.update(changes)
    return value


def test_quote_schema_requires_every_deliverable_and_separates_refusal_from_a_quote():
    names = ['源码', '部署说明']
    schema = quote_schema({'deliverables': [{'name': name} for name in names]})
    Draft202012Validator.check_schema(schema)
    valid = Draft202012Validator(schema).is_valid
    assert valid(quote(names))
    assert valid({'reason': '当前排期已满'})
    assert not valid({**quote(names), 'reason': '不能同时成交和拒绝'})
    assert not valid(quote(names, lineItems={'源码': 10}))
    assert not valid(quote(names, acceptWithin={**quote(names)['acceptWithin'], '额外': 'P1D'}))
    assert not valid({'acceptance': quote(names)['acceptance'], 'acceptWithin': quote(names)['acceptWithin']})
    broken = quote(names)
    broken['acceptance']['源码']['verifiers'] = {}
    assert not valid(broken)
    broken['acceptance']['源码']['verifiers'] = {'binary': {'id': 'v', 'uri': 'u', 'digest': 'd'}}
    assert not valid(broken)
    broken['acceptance']['源码']['verifiers'] = {'skill': {'id': 'v', 'uri': 'u', 'digest': 'd'}}
    assert not valid(broken), 'skill verifiers must name disputeModels'
    broken['acceptance']['源码']['verifiers'] = {'skill': {'id': 'v', 'uri': 'u', 'digest': 'd', 'disputeModels': ['m']}}
    assert valid(broken)


def tool(name, **changes):
    fields = {'name': name, 'description': '测试工具', 'input_schema': {'type': 'object'}, 'invoke': lambda **_: None}
    fields.update(changes)
    return ToolBinding(**fields)


def test_definition_digest_binds_instructions_tools_skills_and_native_tools_but_not_the_display_name():
    base = AgentDefinition(name='甲', instructions='按合同生产', tools=(tool('a'), tool('b')), skills=('nail',))
    same = AgentDefinition(name='乙', instructions='按合同生产', tools=(tool('a'), tool('b')), skills=('nail',))
    assert definition_digest(base) == definition_digest(same)
    assert definition_digest(base) != definition_digest(AgentDefinition(name='甲', instructions='改了', tools=base.tools, skills=base.skills))
    assert definition_digest(base) != definition_digest(AgentDefinition(name='甲', instructions=base.instructions, tools=(tool('b'), tool('a')), skills=base.skills))
    assert definition_digest(base) != definition_digest(AgentDefinition(name='甲', instructions=base.instructions, tools=(tool('a'), tool('b', effect='external')), skills=base.skills))
    assert definition_digest(base) != definition_digest(AgentDefinition(name='甲', instructions=base.instructions, tools=base.tools, skills=()))
    assert definition_digest(base) != definition_digest(AgentDefinition(name='甲', instructions=base.instructions, tools=base.tools, skills=base.skills, native_tools=(('web', 'search'),)))


def case(**changes):
    value = {'admissionId': 'adm-1', 'subject': {'kind': 'contract', 'digest': 'sha256:' + '1' * 64},
             'caseRef': str(uuid.uuid4()), 'state': 'queued', 'ruling': None, 'decisionEvidence': None}
    value.update(changes)
    return value


def test_unruled_case_is_returned_only_when_it_binds_the_same_admission_and_subject():
    subject = case()['subject']
    for state in ('queued', 'evaluating', 'unknown', 'needs_review'):
        value = case(state=state)
        assert arbitration.verify_case(value, None, None, 'adm-1', subject) is value


@pytest.mark.parametrize('changes', [
    {'admissionId': 'adm-2'}, {'subject': {'kind': 'contract', 'digest': 'sha256:' + '2' * 64}},
    {'caseRef': str(uuid.uuid4()).upper()}, {'caseRef': 'case-1'}, {'state': 'done'},
    {'ruling': {}}, {'decisionEvidence': {}}, {'state': 'ruled'}, {'state': 'ruled', 'ruling': {}, 'decisionEvidence': {}}])
def test_inconsistent_case_evidence_requires_restoring_the_original(changes):
    subject = case()['subject']
    with pytest.raises(RuntimeFault) as caught:
        arbitration.verify_case(case(**changes), None, None, 'adm-1', subject)
    assert caught.value.code == 'invalid_evidence' and caught.value.recovery == 'restore_original'
    with pytest.raises(RuntimeFault):
        arbitration.verify_case('not a case', None, None, 'adm-1', subject)


def test_read_case_passes_the_actor_and_admission_and_require_open_stops_on_a_ruling(monkeypatch):
    calls = []
    value = case()
    account = SimpleNamespace(api=lambda method, path: calls.append((method, path)) or value)
    assert arbitration.read_case(account, None, 'did:actor', 'adm-1', value['subject']) is value
    assert calls == [('GET', '/arbitrations?agentId=did%3Aactor&admissionId=adm-1')]
    assert arbitration.read_case(SimpleNamespace(api=lambda *_: None), None, 'did:actor', 'adm-1', value['subject']) is None
    assert arbitration.require_open(account, None, 'did:actor', 'adm-1', value['subject']) is value
    monkeypatch.setattr(arbitration, 'read_case', lambda *args, **kwargs: {'state': 'ruled'})
    with pytest.raises(RuntimeFault) as caught:
        arbitration.require_open(account, None, 'did:actor', 'adm-1', value['subject'])
    assert caught.value.code == 'closed' and caught.value.recovery == 'read_case'


def trading(expected='did:provider'):
    identity = SimpleNamespace(signer=SimpleNamespace(id='did:client', inline_jwk={'kty': 'OKP'}),
                               user=SimpleNamespace(id='user', inline_jwk={'kty': 'OKP'}))
    return SimpleNamespace(identity=identity, protocol_id=lambda service: expected,
                           role=SimpleNamespace(delegation=SimpleNamespace(task=lambda ref: {'context': {'scopeId': ref}})))


def test_candidate_without_a_completed_quote_is_not_ready():
    for record in ({'status': 'pending', 'result': {'card': {}}}, {'status': 'completed', 'result': {}}, {'status': 'completed'}):
        with pytest.raises(RuntimeFault) as caught:
            candidates.checked_candidate(trading(), record)
        assert caught.value.code == 'not_ready'


def test_candidate_bound_to_another_task_service_or_provider_is_rejected_before_any_signature_check():
    record = {'status': 'completed', 'taskRef': 't1', 'service': 's1',
              'template': {'provider': 'did:provider', 'formation': 'countersign'},
              'result': {'taskRef': 't1', 'service': 's1', 'formation': 'countersign', 'card': {'provider': 'did:provider'}}}
    for broken in ({'result': {**record['result'], 'taskRef': 't2'}}, {'result': {**record['result'], 'service': 's2'}},
                   {'template': {**record['template'], 'provider': 'did:other'}},
                   {'result': {**record['result'], 'formation': 'proposal'}}):
        with pytest.raises(RuntimeFault) as caught:
            candidates.checked_candidate(trading(), {**record, **broken})
        assert caught.value.code == 'invalid_input' and 'original task' in str(caught.value)
    with pytest.raises(RuntimeFault) as caught:
        candidates.checked_candidate(trading(), {'status': 'completed', 'result': {'card': {}}})
    assert caught.value.code == 'invalid_input' and 'incomplete' in str(caught.value)


def test_source_record_only_restores_original_requests_of_proposal_candidates():
    assert candidates.source_record(trading(), {'template': {'formation': 'countersign'}}) == {}
    assert candidates.source_record(trading(), {'template': {'formation': 'proposal'}, 'result': {}}) == {}
    assert candidates.source_record(trading(), {'template': {'formation': 'proposal'}, 'result': {'card': {'x': 1}}}) == {}
