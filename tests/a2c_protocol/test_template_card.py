"""模板规则与合同卡不变量（1、2、3、6）。"""
import copy

import pytest

from a2c.card import build_card, card_digest, validate_card
from a2c.errors import A2CError
from a2c.template import template_digest, validate

from .support import PAYMENT_PROVIDER, template_draft


def _stamp(t):
    t = copy.deepcopy(t)
    t["provider"] = "https://provider.example/.well-known/agent-card.json"
    t["templateDigest"] = template_digest(t)
    t["signatures"] = {"provider": {"protected": "x", "signature": "y"}}
    return t


def _code(fn):
    with pytest.raises(A2CError) as e:
        fn()
    return e.value.code


@pytest.mark.parametrize("formation", ["preapproval", "countersign", "proposal"])
def test_valid_templates(formation):
    validate(_stamp(template_draft(formation)), endorsed=False)


def test_template_rules():
    """各条模板规则：违者 template-invalid / invalid-object / digest-mismatch。"""
    t = template_draft("preapproval", review_license=True)
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "template-invalid"      # 预批准型不得签前审阅

    t = template_draft("proposal")
    t["acceptance"] = template_draft()["acceptance"]
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "invalid-object"        # 提案型模板无验收标准

    t = template_draft()
    del t["acceptance"]["final"]["verifiers"]["skill"]["disputeModels"]
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "template-invalid"      # skill 须有仲裁模型

    t = template_draft()
    t["inputs"][0]["verifiers"] = {"service": template_draft()["acceptance"]["outline"]["verifiers"]["script"]}
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "template-invalid"      # 输入验收器只许 script · skill

    t = template_draft()
    t["concurrency"] = 3
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "invalid-object"        # 已删字段

    t = template_draft()
    t["inputs"][0]["specSchema"]["digest"] = {"type": "string", "maxLength": 3}
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "template-invalid"      # 材料不得声明 digest

    t = template_draft()
    t["pricing"]["priceFormula"]["final"] = "2000 * quality * inputs.license.size"
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "template-invalid"      # 公式只引用必需材料的数值

    t = template_draft()
    del t["pricing"]["priceFactors"]["final"]["quality"]["720p"]
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "template-invalid"      # 系数须覆盖每个取值

    t = template_draft()
    del t["deadlines"]["authorizeWithin"]
    assert _code(lambda: validate(_stamp(t), endorsed=False)) == "template-invalid"      # 有可授权环节就要 authorizeWithin

    t = _stamp(template_draft())
    t["validUntil"] = "2027-01-01T00:00:00Z"
    assert _code(lambda: validate(t, endorsed=False)) == "digest-mismatch"


def _card(t, **kw):
    args = dict(card_id="C", client={"id": "c", "jwk": {"kty": "OKP", "crv": "Ed25519", "x": "AA"}},
                signers={"slot1": "client", "slot7": "client"},
                spec={"outline": {"episodes": 12}, "final": {"quality": "720p", "notes": ""}},
                inputs={"novel": {"format": "md", "chars": 100, "digest": "sha256:" + "1" * 64}},
                delegation_scope_digest="sha256:" + "2" * 64, payment_provider=PAYMENT_PROVIDER)
    args.update(kw)
    return build_card(t, **args)


def test_card_invariants():
    """不变量 1–3：const 不填、非 const 必填、取值在域内、派生值可复算、必需材料齐、亲签点名用户须带公钥。"""
    t = _stamp(template_draft())
    card = _card(t)
    validate_card(t, card)
    bad = [
        {"spec": {"outline": {"episodes": 12}, "final": {"quality": "720p", "notes": "", "format": "mp4"}}},
        {"spec": {"outline": {"episodes": 12}, "final": {"quality": "720p"}}},
        {"spec": {"outline": {"episodes": 13}, "final": {"quality": "720p", "notes": ""}}},
        {"inputs": {"license": {"digest": "sha256:" + "1" * 64}}},
    ]
    for kw in bad:
        assert _code(lambda kw=kw: _card(t, **kw)) == "card-invalid"
        forged = copy.deepcopy(card)
        forged.update(copy.deepcopy(kw))
        assert _code(lambda f=forged: validate_card(t, f)) == "card-invalid"
    tampered = copy.deepcopy(card)
    tampered["derived"]["periods"][0]["amount"] += 1
    assert _code(lambda: validate_card(t, tampered)) == "card-invalid"
    assert _code(lambda: validate_card(t, _card(t, signers={"slot1": "user", "slot7": "client"}))) == "card-invalid"
    assert card_digest(card) == card_digest({**card, "signatures": {}, "fulfillment": {"receipt": 1}})


def test_proposal_card_keeps_client_terms(world):
    """不变量 6：服务方拟的卡改动委托方填的值即 card-invalid。"""
    from a2c.card import build_proposal_card, request_digest
    from .support import ProposalPolicy
    t = _stamp(template_draft("proposal"))
    req = world.client.draft_request.__self__   # noqa: B018 - only to show the client builds requests
    from a2c.card import build_request
    request = build_request(t, request_id="R", version=1, client={"id": "c", "jwk": {"kty": "OKP", "crv": "Ed25519", "x": "AA"}},
                            signers={"slot1": "client", "slot7": "client"},
                            spec={"outline": {"episodes": 12}, "final": {"quality": "720p", "notes": "x"}},
                            inputs={"novel": {"format": "md", "chars": 100, "digest": "sha256:" + "1" * 64}},
                            delegation_scope_digest="sha256:" + "2" * 64, payment_provider=PAYMENT_PROVIDER)
    request["signature"] = {"protected": "x", "signature": "y"}
    p = ProposalPolicy().draft(t, request, {})
    card = build_proposal_card(t, request, card_id="C", acceptance=p.acceptance, accept_within=p.accept_within,
                               line_items=p.line_items)
    validate_card(t, card, request=request)
    assert card["requestDigest"] == request_digest(request)
    changed = copy.deepcopy(card)
    changed["spec"]["final"]["notes"] = "改了"
    assert _code(lambda: validate_card(t, changed, request=request)) == "card-invalid"
    del req
