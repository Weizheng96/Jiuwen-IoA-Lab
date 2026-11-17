"""审校补漏：信任、解除、仲裁复算的绑定、分期取整、包与沙箱的上限、各处状态与新鲜度。"""
import copy
import io
import json
import zipfile
from datetime import timedelta

import pytest

from a2c.card import card_digest
from a2c.codec import digest_bytes
from a2c.errors import A2CError
from a2c.pricing import derive
from a2c.slots import sign_slot
from a2c.verifier.package import load

from .support import MARKET, PROVIDER, ProposalPolicy, World, template_draft


def _code(fn):
    with pytest.raises(A2CError) as e:
        fn()
    return e.value.code


def _done(w, card):
    cd = card_digest(card)
    w.client.start(card)
    for i in (1, 2):
        w.deliver(cd, i)
        w.client.fetch_delivery(cd, i)
        assert w.client.accept(cd, i).status == "pass"
    return cd


# ── trust ───────────────────────────────────────────────────────────────

def test_untrusted_market_and_payment_provider():
    """委托方只认信任的市场；卡上的支付方须是模板列出的。"""
    w = World()
    t = w.publish("countersign")
    w.client.markets = set()
    assert w.client.get_templates(PROVIDER) == []
    w.client.markets = {MARKET}
    [t] = w.client.get_templates(PROVIDER)
    w.client.payment_provider = {"id": "https://evil.example", "jwksUri": "https://evil.example/jwks"}
    assert _code(lambda: w.client.draft_card(t, card_id="C", spec=w.spec(), materials=w.materials(),
                                             signers={"slot1": "client", "slot7": "client"},
                                             delegation_scope_digest="sha256:" + "d" * 64)) == "card-invalid"


# ── termination ─────────────────────────────────────────────────────────

def test_no_termination_after_completion_and_quote_period_checked():
    w = World()
    card = w.formed_card("countersign")
    cd = _done(w, card)
    assert _code(lambda: w.client.termination_quote(cd)) == "out-of-order"
    w2 = World()
    card2 = w2.formed_card("countersign")
    cd2 = card_digest(card2)
    w2.client.start(card2)
    original = w2.provider.op_termination_quote

    def wrong_period(body, files, task_id):          # a Provider quoting a later, dearer period
        rec = w2.provider._record(body["cardDigest"])
        q = w2.provider._sign("termination-quote", cardDigest=body["cardDigest"], period=2,
                              fee=rec["card"]["derived"]["periods"][1]["amount"])
        return type(original(body, files, task_id))({"op": "termination.quote", "terminationQuote": q})
    w2.provider.op_termination_quote = wrong_period
    assert _code(lambda: w2.client.termination_quote(cd2)) == "card-invalid"


def test_nothing_after_termination():
    """解除生效后不得再追加签4–7：服务方不能交付，委托方不能取件验收，卡上补签也验不过。"""
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.deliver(cd, 1)
    w.client.terminate(cd)
    assert _code(lambda: w.deliver(cd, 2)) == "out-of-order"
    assert _code(lambda: w.client.fetch_delivery(cd, 1)) == "out-of-order"
    terminated = w.client.card(cd)
    assert _code(lambda: sign_slot(terminated, 6, w.client.signer, w.clock(), period=1)) == "out-of-order"


def test_deposit_not_paid_counts_as_zero_in_the_balance():
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    quote = w.client.termination_quote(cd)              # before work.start: no deposit paid
    w.client.terminate(cd, quote=quote)
    assert [e["purpose"] for e in w.psp.ledger] == ["termination"]
    assert abs(w.psp.ledger[0]["amount"] - quote["fee"]) < 1e-9


# ── arbitration ─────────────────────────────────────────────────────────

def _proposal_done(w):
    t = w.publish("proposal")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    req = w.client.draft_request(t, request_id="R", spec=w.spec(), materials=w.materials(),
                                 signers={"slot1": "client", "slot7": "client"},
                                 delegation_scope_digest="sha256:" + "d" * 64)
    card = w.client.adopt(w.client.submit_request(req))
    w.client.start(card)
    cd = card_digest(card)
    w.deliver(cd, 1)
    w.client.fetch_delivery(cd, 1)
    w.client.accept(cd, 1)
    w.provider.deliver(cd, {"plan.json": (json.dumps({"booking": "B"}).encode(), "application/json"),
                            "final.mp4": (b"FAKEMP4", "video/mp4")})
    w.client.fetch_delivery(cd, 2)
    return cd


def test_recompute_binds_attestations_and_closes_pending():
    """复算：认证结论须绑定本卡本期本清单；仍「还不能判」即不通过。"""
    w = World(policy=ProposalPolicy())
    cd = _proposal_done(w)
    files = {"plan.json": json.dumps({"booking": "B"}).encode(), "final.mp4": b"FAKEMP4"}
    assert w.client.accept(cd, 2).status == "pending"
    card = w.client.card(cd)
    assert w.market.recompute(card, 2, files).status == "fail"           # nothing stored yet, pending → fail
    w.clock.advance(hours=2)
    assert w.client.accept(cd, 2).status == "pass"
    card = w.client.card(cd)
    assert w.market.recompute(card, 2, files).status == "pass"
    forged = copy.deepcopy(card)
    a = forged["fulfillment"]["periods"][1]["attestations"][0]
    other = w.service.op_attestation_request({**{k: a[k] for k in ("verifier", "cardDigest", "manifestDigest")},
                                              "period": 1, "values": {"booking": "B"}}, {}, None).body["attestation"]
    forged["fulfillment"]["periods"][1]["attestations"] = [other]
    assert w.market.recompute(forged, 2, files).status == "fail"


# ── pricing ─────────────────────────────────────────────────────────────

def test_fractional_line_items_and_exact_split():
    t = template_draft("proposal", deposit="0")
    d = derive(t, {}, {}, line_items={"outline": 12.34, "final": 99.9})
    assert d["total"] == 112.24
    assert _code(lambda: derive(t, {}, {}, line_items={"outline": 12.345, "final": 1})) == "card-invalid"
    t["pricing"]["paymentWeights"] = {"outline": 1, "final": 1}
    assert [p["amount"] for p in derive(t, {}, {}, line_items={"outline": 0.05, "final": 0})["periods"]] == [0.02, 0.03]
    t2 = template_draft()
    t2["pricing"]["paymentWeights"]["final"] = 1.5
    from a2c.template import validate
    from .test_template_card import _stamp
    assert _code(lambda: validate(_stamp(t2), endorsed=False)) == "template-invalid"


# ── state, access, freshness ────────────────────────────────────────────

def test_request_version_checked_by_the_provider():
    """同一版本重发：拟卡费凭证复用，服务方以版本未递增拒收。"""
    w = World(policy=ProposalPolicy())
    t = w.publish("proposal")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    req = w.client.draft_request(t, request_id="R", spec=w.spec(), materials=w.materials(),
                                 signers={"slot1": "client", "slot7": "client"},
                                 delegation_scope_digest="sha256:" + "d" * 64)
    assert isinstance(w.client.submit_request(req), dict)
    assert _code(lambda: w.client.submit_request(req)) == "out-of-order"
    assert [e["purpose"] for e in w.psp.ledger] == ["proposal-fee"]


def test_closed_card_cannot_restart_and_fetch_needs_the_task():
    w = World()
    card = w.formed_card("countersign")
    cd = _done(w, card)
    w.provider.close(cd)
    assert _code(lambda: w.provider.handle("work.start", {"card": w.provider.card(cd)}).body
                 and w.net.send(PROVIDER, "work.start", {"card": w.provider.card(cd)})) == "out-of-order"
    assert _code(lambda: w.net.send(PROVIDER, "delivery.fetch", {"cardDigest": cd, "period": 1})) == "invalid-object"


def test_market_rejects_a_stale_slot_4():
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    rec = w.provider._record(cd)
    c = copy.deepcopy(rec["card"])
    from a2c import objects
    c["fulfillment"]["periods"] = [{"manifest": objects.new("manifest", cardDigest=cd, period=1, files=[
        {"name": "outline.md", "digest": digest_bytes(b"x"), "mediaType": "text/markdown"}])}]
    w.clock.advance(minutes=1)
    stale = sign_slot(c, 4, w.provider_signer, w.clock() - timedelta(minutes=30), period=1)
    assert _code(lambda: w.net.send(MARKET, "delivery.anchor", {"card": stale})) in ("bad-signature", "out-of-order")


# ── packages and sandbox ────────────────────────────────────────────────

def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_package_traversal_and_limits():
    m = json.dumps({"id": "t@1", "kind": "script", "runtime": "cpython-3.13-wasi", "entry": "m:verify", "reads": []})
    for blob in (_zip({"a2c-verifier.json": m, "../evil.py": "x"}),
                 _zip({"a2c-verifier.json": m, **{f"f{i}.py": "" for i in range(300)}})):
        ref = {"id": "t@1", "uri": "u", "digest": digest_bytes(blob)}
        assert _code(lambda b=blob, r=ref: load(b, r, "script")) == "template-invalid"


def test_input_reads_are_limited():
    """输入验收器只读本材料的键；清单读的键须在模板里存在。"""
    from a2c.verifier.runner import values_for
    t = template_draft()
    card = {"spec": {"final": {"quality": "720p", "notes": ""}}, "inputs": {"novel": {"chars": 3}}}
    assert values_for(["inputs.novel.chars"], t, card, material="novel") == {"inputs.novel.chars": 3}
    assert _code(lambda: values_for(["spec.final.quality"], t, card, material="novel")) == "template-invalid"
    assert _code(lambda: values_for(["spec.final.nothing"], t, card)) == "template-invalid"
