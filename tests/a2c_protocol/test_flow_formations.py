"""预批准型与提案型的端到端流程。"""
import json

import pytest

from a2c.card import card_digest
from a2c.errors import A2CError

from .support import MARKET, PROVIDER, ProposalPolicy, World


def test_preapproval_without_deposit():
    """预批准型：委托方签1 即生效；定金为 0 则不付款、不带凭证；签1 由委托方自己签。"""
    w = World()
    card = w.formed_card("preapproval", signers={"slot1": "client", "slot7": "client"}, deposit="0", steps=False)
    assert card["signatures"]["2"] is None and card["derived"]["deposit"] == 0
    assert "receipt" in w.client.start(card)
    assert w.psp.ledger == []
    cd = card_digest(card)
    w.deliver(cd, 1)
    w.client.fetch_delivery(cd, 1)
    assert w.client.accept(cd, 1).status == "pass"
    assert [e["purpose"] for e in w.psp.ledger] == ["installment"]


def _proposal_world(**kw):
    w = World(policy=ProposalPolicy(**kw))
    t = w.publish("proposal")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    request = w.client.draft_request(t, request_id="R-1", spec=w.spec(), materials=w.materials(),
                                     signers={"slot1": "user", "slot7": "user"},
                                     delegation_scope_digest="sha256:" + "d" * 64)
    return w, t, request


def _deliver_plan(w, cd):
    w.deliver(cd, 1)
    w.client.fetch_delivery(cd, 1)
    assert w.client.accept(cd, 1).status == "pass"
    plan = json.dumps({"booking": "B-42"}).encode()
    w.provider.deliver(cd, {"plan.json": (plan, "application/json"), "final.mp4": (b"FAKEMP4" + b"x" * 8, "video/mp4")})
    w.client.fetch_delivery(cd, 2)


def test_proposal_flow_with_fee_and_service_verifier():
    """提案型：付拟卡费 → 提案卡 → 服务方拟卡签2 → 市场签3 → 采纳签1 → 开工；
    L3 认证结论先「还不能判」，过了验收器自定的时间再取一次即通过；认证结论原件入履约记录。"""
    w, t, request = _proposal_world()
    card = w.client.submit_request(request)
    assert isinstance(card, dict) and card["signatures"]["3"] is not None
    assert w.psp.ledger[0]["purpose"] == "proposal-fee"
    card = w.client.adopt(card)
    assert "receipt" in w.client.start(card)
    cd = card_digest(card)
    _deliver_plan(w, cd)
    first = w.client.accept(cd, 2)
    assert first.status == "pending" and first.result.not_before
    w.clock.advance(hours=2)
    done = w.client.accept(cd, 2)
    assert done.status == "pass"
    stored = w.client.card(cd)["fulfillment"]["periods"][1]
    assert len(stored["attestations"]) == 1 and stored["report"]["results"][1]["attestationDigests"]
    w.provider.close(cd)
    assert w.market.store.get("ledger", cd)["outcome"] == "completed"


def test_arbitration_replays_attestation_without_calling_service():
    """仲裁复算：用存下的认证结论原件重放，不再调服务；skill 用每个仲裁模型复算。"""
    w, t, request = _proposal_world()
    card = w.client.adopt(w.client.submit_request(request))
    w.client.start(card)
    cd = card_digest(card)
    _deliver_plan(w, cd)
    w.clock.advance(hours=2)
    assert w.client.accept(cd, 2).status == "pass"
    calls = len(w.service.tasks)
    files = {"plan.json": json.dumps({"booking": "B-42"}).encode(), "final.mp4": b"FAKEMP4" + b"x" * 8}
    result = w.market.recompute(w.client.card(cd), 2, files)
    assert result.status == "pass" and len(w.service.tasks) == calls
    w.confirmed = False                      # 服务方此后的说法变了也不影响复算
    assert w.market.recompute(w.client.card(cd), 2, files).status == "pass"


def test_proposal_decline_and_revision():
    """退回附理由；改版重发版本须递增，否则 out-of-order。"""
    w, t, request = _proposal_world(decline="预算不足以覆盖京都五日")
    assert w.client.submit_request(request) == "预算不足以覆盖京都五日"
    with pytest.raises(A2CError) as e:
        w.client.submit_request(request)
    assert e.value.code == "out-of-order"
    w.provider.policy.decline = None
    v2 = w.client.draft_request(t, request_id="R-1", version=2, spec=w.spec(), materials=w.materials(),
                                signers={"slot1": "user", "slot7": "user"}, delegation_scope_digest="sha256:" + "d" * 64)
    assert isinstance(w.client.submit_request(v2), dict)
    assert [e["purpose"] for e in w.psp.ledger] == ["proposal-fee", "proposal-fee"]


def test_adoption_after_respond_within_is_late():
    """签3 后 respondWithin 内未采纳：报价失效，开工时 deadline-passed。"""
    w, t, request = _proposal_world()
    card = w.client.submit_request(request)
    w.clock.advance(days=2)
    card = w.client.adopt(card)
    with pytest.raises(A2CError) as e:
        w.client.start(card)
    assert e.value.code == "deadline-passed"


def test_market_refuses_unrecognized_service_and_unrunnable_model():
    """市场签模板 / 签3 时：服务不在认可名单、仲裁模型跑不了，即 not-arbitrable。"""
    from .support import template_draft
    w = World()
    t = template_draft("countersign")
    t["acceptance"]["final"]["verifiers"]["skill"]["disputeModels"] = ["model-a", "model-x"]
    with pytest.raises(A2CError) as e:
        w.provider.publish(t)
    assert e.value.code == "not-arbitrable"
    w2, t2, request = _proposal_world()
    del w2.market.recognized_services["https://checker.example/.well-known/agent-card.json"]
    with pytest.raises(A2CError) as e:
        w2.client.submit_request(request)
    assert e.value.code == "not-arbitrable"
    assert MARKET
