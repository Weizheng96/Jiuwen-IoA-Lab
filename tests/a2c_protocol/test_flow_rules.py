"""材料核对、截止时间与环节暂停、新鲜度、解除、槽位顺序。"""
import copy

import pytest

from a2c.card import card_digest
from a2c.errors import A2CError
from a2c.slots import sign_slot, verify_card

from .support import MARKET, PROVIDER, World


def _code(fn):
    with pytest.raises(A2CError) as e:
        fn()
    return e.value.code


# ── materials ───────────────────────────────────────────────────────────

def test_precheck_catches_wrong_declaration():
    """委托方定约前用输入验收器预校验：声明的字数与文件不符即发现。"""
    w = World()
    t = w.publish("countersign")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    draft = w.client.draft_card(t, card_id="C", spec=w.spec(), materials=w.materials(chars=7),
                                signers={"slot1": "client", "slot7": "client"}, delegation_scope_digest="sha256:" + "d" * 64)
    res = w.client.precheck(t, draft.card, {"novel": w.NOVEL.encode()})
    assert res["novel"].status == "fail"


def test_declaration_mismatch_lapses_the_card():
    """文件对、声明错：拒收文书附输入验收器结果，卡失效。"""
    w = World()
    t = w.publish("countersign")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    draft = w.client.draft_card(t, card_id="C", spec=w.spec(), materials=w.materials(chars=7),
                                signers={"slot1": "client", "slot7": "client"}, delegation_scope_digest="sha256:" + "d" * 64)
    card = w.client.countersign(w.client.sign(draft.card))
    out = w.client.start(card)
    rej = out["rejection"]
    assert rej["reason"] == "declaration-mismatch" and rej["inputs"] == ["novel"]
    assert rej["report"]["input"] == "novel" and rej["report"]["results"][0]["status"] == "fail"
    assert _code(lambda: w.client.start(card)) == "out-of-order"


def test_digest_mismatch_can_be_resent():
    """交错了文件：拒收，卡仍有效，startWithin 内重发即开工。"""
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    rec = w.client.store.get("cards", cd)
    good = rec["materials"]["novel"]
    rec["materials"]["novel"] = "d3Jvbmc="                        # "wrong"
    w.client.store.put("cards", cd, rec)
    assert w.client.start(card)["rejection"]["reason"] == "digest-mismatch"
    rec = w.client.store.get("cards", cd)
    rec["materials"]["novel"] = good
    w.client.store.put("cards", cd, rec)
    assert "receipt" in w.client.start(card)


def test_review_material_sent_before_signing_only():
    """签前审阅的材料随复签请求交出、经签2 即接受；开工时不再要。"""
    w = World()
    card = w.formed_card("countersign")
    log = [(op, set(body)) for _to, op, body in w.net.log if op in ("card.countersign", "work.start")]
    assert log[0] == ("card.countersign", {"op", "card", "informationAuthorization"})
    w.client.start(card)
    started = [b for _t, op, b in w.net.log if op == "work.start"][0]
    assert set(started["informationAuthorization"]["inputs"]) == {"novel"}


# ── deadlines, steps, freshness ─────────────────────────────────────────

def test_countersign_after_respond_within_and_stale_slot1():
    """复签请求晚于签1 + respondWithin 即 deadline-passed；签1 不新鲜即 bad-signature。"""
    w = World()
    t = w.publish("countersign")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    draft = w.client.draft_card(t, card_id="C", spec=w.spec(), materials=w.materials(),
                                signers={"slot1": "client", "slot7": "client"}, delegation_scope_digest="sha256:" + "d" * 64)
    card = w.client.sign(draft.card)
    w.clock.advance(minutes=10)
    assert _code(lambda: w.client.countersign(card)) == "bad-signature"
    w.clock.advance(days=2)
    card = w.client.sign(draft.card)
    w.clock.advance(minutes=1)
    assert isinstance(w.client.countersign(card), dict)


def test_start_after_start_within_lapses():
    w = World()
    card = w.formed_card("countersign")
    w.clock.advance(days=4)
    assert _code(lambda: w.client.start(card)) == "deadline-passed"


def test_step_pause_extends_delivery_deadline():
    """环节请求到答复之间不计工期；拒绝也签；未答复超过 authorizeWithin 是服务方的仲裁依据。"""
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    base = w.client.status(cd).deadline
    w.provider.request_step(cd, "publish")
    [ev] = w.client.poll(cd)
    assert ev["type"] == "step-request"
    w.clock.advance(hours=10)
    w.client.answer_step(cd, granted=False)
    st = w.client.status(cd)
    assert (st.deadline - base).total_seconds() == 10 * 3600
    w.provider.request_step(cd, "publish")
    w.client.poll(cd)
    w.clock.advance(days=3)
    st = w.client.status(cd)
    assert any("authorizeWithin" in g for g in st.provider_grounds)
    assert _code(lambda: w.provider.request_step(cd, "not-declared")) == "card-invalid"


def test_accept_within_gives_provider_a_ground():
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.deliver(cd, 1)
    w.client.poll(cd)
    w.clock.advance(days=6)
    w.client.store.put("cards", cd, {**w.client.store.get("cards", cd), "card": w.provider.card(cd)})
    st = w.client.status(cd)
    assert st.phase == "accepting" and st.provider_grounds


# ── slots ───────────────────────────────────────────────────────────────

def test_slot_order_and_signers():
    """槽位前提：没回执不能签4；签7 须有签6；签1 只能由亲签条款点名的人签。"""
    w = World()
    card = w.formed_card("countersign")
    t = w.client.template(card["templateDigest"])
    forged = copy.deepcopy(card)
    forged["fulfillment"]["periods"] = [{"manifest": {"a2cVersion": "0.0.14", "kind": "manifest",
                                                      "cardDigest": card_digest(card), "period": 1,
                                                      "files": [{"name": "x", "digest": "sha256:" + "0" * 64,
                                                                 "mediaType": "text/plain"}]}}]
    forged = sign_slot(forged, 4, w.provider_signer, w.clock(), period=1)
    assert _code(lambda: verify_card(t, forged, w.keys)) == "out-of-order"
    assert _code(lambda: sign_slot(card, 1, w.client.signer, w.clock())) == "out-of-order"   # no re-signing
    wrong = copy.deepcopy(card)
    wrong["signatures"]["1"] = None
    wrong = sign_slot(wrong, 1, w.client.signer, w.clock())          # signers.slot1 is the user
    assert _code(lambda: verify_card(t, wrong, w.keys)) == "bad-signature"


# ── termination ─────────────────────────────────────────────────────────

def test_agreed_termination_pays_balance_and_closes():
    """协商解除：报价不超过本期金额；余额为正委托方补付；市场记「委托方解除」。"""
    w = World()
    card = w.formed_card("countersign", deposit="0")
    cd = card_digest(card)
    w.client.start(card)
    quote = w.client.termination_quote(cd)
    assert quote["period"] == 1 and quote["fee"] == card["derived"]["periods"][0]["amount"]
    w.client.terminate(cd, quote=quote)
    assert [e["purpose"] for e in w.psp.ledger] == ["termination"]
    balance = quote["fee"] - card["derived"]["deposit"]
    assert abs(w.psp.ledger[-1]["amount"] - balance) < 1e-9
    w.provider.close(cd)
    assert w.market.store.get("ledger", cd)["outcome"] == "client-terminated"
    assert _code(lambda: w.deliver(cd, 1)) == "out-of-order"


def test_termination_balance_negative_is_a_refund():
    """定金高于解除价：余额为负，委托方不付款（差额由服务方退）。"""
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.provider.policy.termination_fee = lambda t, c, i: 100
    w.client.terminate(cd, quote=w.client.termination_quote(cd))
    assert [e["purpose"] for e in w.psp.ledger] == ["deposit"]
    assert w.provider.balance(w.provider._record(cd)) < 0


def test_termination_fee_cap_and_unilateral():
    """服务方报价超上限会被 SDK 拒；不引用报价即单方解除，不付余额。"""
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.provider.policy.termination_fee = lambda t, c, i: c["derived"]["periods"][i - 1]["amount"] + 1
    assert _code(lambda: w.client.termination_quote(cd)) == "card-invalid"
    w.client.terminate(cd)
    assert [e["purpose"] for e in w.psp.ledger] == ["deposit"]
    w.provider.close(cd)
    assert w.market.store.get("ledger", cd)["outcome"] == "client-terminated"
    assert MARKET


def test_close_requires_completion():
    w = World()
    card = w.formed_card("countersign")
    w.client.start(card)
    assert _code(lambda: w.provider.close(card_digest(card))) == "out-of-order"


def test_failed_acceptance_signs_report_and_arbitration_recomputes():
    """不通过：委托方签验收报告、不签7、不付款；市场用两个仲裁模型复算，一个不过即不通过。"""
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.deliver(cd, 1)
    w.client.fetch_delivery(cd, 1)
    w.client.accept(cd, 1)
    w.deliver(cd, 2)
    w.client.fetch_delivery(cd, 2)
    w.score = 0.3
    res = w.client.accept(cd, 2)
    assert res.status == "fail" and len(res.card["signatures"]["7"]) == 1
    assert res.card["fulfillment"]["periods"][1]["report"]["signature"]
    assert [e["purpose"] for e in w.psp.ledger] == ["deposit", "installment"]
    files = {"final.mp4": b"FAKEMP4" + b"x" * 64}
    assert w.market.recompute(w.client.card(cd), 2, files).status == "fail"
    w.score = 0.9
    assert w.market.recompute(w.client.card(cd), 2, files).status == "pass"
