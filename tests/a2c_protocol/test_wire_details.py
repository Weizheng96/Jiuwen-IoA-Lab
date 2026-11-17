"""草案审校补的线上细节：表达式细则、环节请求只能一个、存证订阅随结清结束。"""
import pytest

from a2c import objects
from a2c.card import card_digest
from a2c.errors import A2CError
from a2c.expr import ExprError, evaluate, variables
from a2c.jws import Signer

from .support import MARKET, PROVIDER, World, template_draft


def _code(fn):
    with pytest.raises(A2CError) as e:
        fn()
    return e.value.code


@pytest.mark.parametrize("expr", ["-(-x)", "-(x + 1)", "+(x)", "--x", "- -x", "007", "00", "1.", "\t1", "1\n+ 2",
                                  "2 * - (3)", "x -"])
def test_rejected_expressions(expr):
    with pytest.raises(ExprError):
        variables(expr)


@pytest.mark.parametrize("expr,value", [("2 * -3", -6.0), ("x - -3", 5.0), (" (-x) ", -2.0), ("0.5 + 10", 10.5),
                                        ("-x * 2", -4.0)])
def test_accepted_expressions(expr, value):
    assert evaluate(expr, lambda path: 2.0) == value


def _balanced(n: int, first: str = "1") -> str:
    """A sum of n leaves as a balanced tree (depth stays small; parentheses are not nodes)."""
    if n == 1:
        return first
    return f"({_balanced(n // 2, first)} + {_balanced(n - n // 2)})"


def test_node_limit_counts_numbers_names_unary_signs_and_operators():
    assert evaluate(_balanced(128), lambda p: 0) == 128.0            # 128 numbers + 127 operators = 255
    assert evaluate(_balanced(128, "-1"), lambda p: 0) == 126.0      # + one unary sign = 256
    with pytest.raises(ExprError):
        variables(_balanced(129))                                     # 257
    with pytest.raises(ExprError):
        variables(" + ".join(["1"] * 40))                             # a left-nested chain 40 levels deep


def test_one_step_request_at_a_time():
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.provider.request_step(cd, "publish")
    assert _code(lambda: w.provider.request_step(cd, "publish")) == "out-of-order"
    w.client.poll(cd)
    w.client.answer_step(cd, granted=True)
    w.provider.request_step(cd, "publish")


def test_anchor_watch_completes_when_the_card_closes():
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    task = w.client.watch(cd, MARKET)
    for i in (1, 2):
        w.deliver(cd, i)
        w.client.fetch_delivery(cd, i)
        assert w.client.accept(cd, i).status == "pass"
    assert w.net.get_task(MARKET, task).state == "working"
    w.provider.close(cd)
    assert w.net.get_task(MARKET, task).state == "completed"


def test_sample_provenance_is_verified_against_the_template_market():
    """带出处的样件：委托方验签模板的那家市场的加签；验不过即按 bad-signature 拒收模板。"""
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.deliver(cd, 1)
    w.client.fetch_delivery(cd, 1)
    w.client.accept(cd, 1)
    w.deliver(cd, 2)
    w.client.fetch_delivery(cd, 2)
    sample = w.client.card(cd)["fulfillment"]["periods"][1]["manifest"]["files"][0]["digest"]
    prov = w.client.sign_provenance(cd, sample)
    w.client.accept(cd, 2, provenance=prov)
    w.provider.close(cd)
    [endorsed] = w.provider.endorse_provenance(cd)

    def published(provenance):
        t = template_draft("countersign")
        t["samples"] = [{"name": "s1", "deliverable": "final", "uri": "https://sp.example/samples/s1.mp4",
                         "digest": sample, "spec": {"quality": "1080p"}, "provenance": provenance}]
        return w.provider.publish(t)

    good = published(endorsed)
    assert w.client.get_templates(PROVIDER, good["templateDigest"])
    forged = objects.sign_role(prov, "market", Signer.generate(MARKET, kid="m1"), w.clock())
    bad = published(forged)
    assert _code(lambda: w.client.get_templates(PROVIDER, bad["templateDigest"])) == "bad-signature"


def _request(w):
    t = w.publish("proposal")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    return w.client.draft_request(t, request_id="R", spec=w.spec(), materials=w.materials(),
                                  signers={"slot1": "client", "slot7": "client"},
                                  delegation_scope_digest="sha256:" + "d" * 64)


def test_slot_3_is_signed_without_the_request_card():
    """签3 时市场只收卡；提案卡是否被改、拟卡是否超时由委托方采纳前核。"""
    from .support import ProposalPolicy
    w = World(policy=ProposalPolicy())
    card = w.client.submit_request(_request(w))
    assert isinstance(card, dict) and card["signatures"]["3"]
    assert all("request" not in body for _to, op, body in w.net.log if op == "card.endorse")


def test_drafting_is_judged_by_slot_3():
    """带签2、签3 的卡须在提案卡时刻后 respondWithin 内备好；晚了即 deadline-passed。"""
    from .support import ProposalPolicy

    class Slow(ProposalPolicy):
        def draft(self, template, request, materials):
            w.clock.advance(days=2)                   # respondWithin is P1D
            return super().draft(template, request, materials)
    w = World(policy=Slow())
    assert _code(lambda: w.client.submit_request(_request(w))) == "deadline-passed"


def test_retrieved_signatures_are_checked_against_a_window():
    """委托方取回的回执、环节请求不核新鲜度，核不晚于本机时间、不早于上一枚已知签名。"""
    from datetime import timedelta
    w = World()
    card = w.formed_card("countersign")
    w.provider.now = lambda: w.clock() + timedelta(hours=1)          # a receipt dated in the future
    assert _code(lambda: w.client.start(card)) == "bad-signature"
    w = World()                                                      # the Provider's start stands; use a fresh world
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    w.provider.now = lambda: w.clock() - timedelta(hours=1)          # a step request dated before the receipt
    w.provider.request_step(cd, "publish")
    assert _code(lambda: w.client.poll(cd)) == "bad-signature"


def test_provenance_endorsement_takes_the_closed_card():
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    w.client.start(card)
    for i in (1, 2):
        w.deliver(cd, i)
        w.client.fetch_delivery(cd, i)
        if i == 1:
            w.client.accept(cd, 1)
    sample = w.client.card(cd)["fulfillment"]["periods"][1]["manifest"]["files"][0]["digest"]
    prov = w.client.sign_provenance(cd, sample)
    w.client.accept(cd, 2, provenance=prov)
    w.provider.close(cd)
    closed = w.provider.card(cd)
    assert _code(lambda: w.net.send(MARKET, "provenance.endorse", {"provenance": prov})) == "invalid-object"
    tampered = {**closed, "cardId": "another"}
    assert _code(lambda: w.net.send(MARKET, "provenance.endorse", {"provenance": prov, "card": tampered})) == "invalid-object"
    assert w.net.send(MARKET, "provenance.endorse", {"provenance": prov, "card": closed}).body["provenance"]["signatures"]["market"]


def test_provider_purges_offers_that_lapsed_before_work_started():
    w = World()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    assert w.provider.purge_lapsed() == []
    w.clock.advance(days=4)                                          # startWithin is P3D
    assert w.provider.purge_lapsed() == [cd]
    assert _code(lambda: w.provider.card(cd)) == "unknown-card"
