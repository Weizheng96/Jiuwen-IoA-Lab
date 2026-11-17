"""待批准型：签1 → 复签签2 → 付定金 → 开工 → 两期交付验收付款 → 结清 → 出处加签。"""
from a2c.card import card_digest
from a2c.objects import object_digest

from .support import MARKET, PROVIDER


def test_countersign_full_flow(world):
    """完整走一遍待批准型，各签名、凭证、结局都对。"""
    card = world.formed_card("countersign")
    cd = card_digest(card)
    assert card["signatures"]["1"] and card["signatures"]["2"]
    assert card["derived"]["deposit"] == round(card["derived"]["total"] * 0.1, 2)

    started = world.client.start(card)
    assert "receipt" in started
    world.client.watch(cd, MARKET)

    for period in (1, 2):
        world.clock.advance(hours=2)
        world.deliver(cd, period)
        events = world.client.poll(cd)
        assert {"type": "anchored", "period": period} in events
        world.client.fetch_delivery(cd, period)
        result = world.client.accept(cd, period)
        assert result.status == "pass", result.result.errors
        assert result.voucher["purpose"] == "installment"

    world.provider.close(cd)
    ledger = world.market.store.get("ledger", cd)
    assert ledger["outcome"] == "completed"
    paid = [e["purpose"] for e in world.psp.ledger]
    assert paid == ["deposit", "installment", "installment"]
    total = sum(e["amount"] for e in world.psp.ledger)
    assert abs(total - card["derived"]["total"]) < 1e-9


def test_skill_runs_three_observations(world):
    """skill 里同一请求调用三次，各占一条记录、各问一次模型（重放按调用序号）。"""
    card = world.formed_card("countersign")
    cd = card_digest(card)
    world.client.start(card)
    for period in (1, 2):
        world.deliver(cd, period)
        world.client.fetch_delivery(cd, period)
        before = world.model_calls
        res = world.client.accept(cd, period)
        assert res.status == "pass"
        if period == 2:
            assert world.model_calls - before == 3
            skill = [r for r in res.result.runs if r.kind == "skill"][0]
            assert len(skill.outcome.responses) == 3


def test_provenance_double_signed(world):
    """委托方随验收通知签出处声明，结清后市场加签；样件须出自该卡清单。"""
    card = world.formed_card("countersign")
    cd = card_digest(card)
    world.client.start(card)
    world.deliver(cd, 1)
    world.client.fetch_delivery(cd, 1)
    world.client.accept(cd, 1)
    world.deliver(cd, 2)
    world.client.fetch_delivery(cd, 2)
    sample = world.client.card(cd)["fulfillment"]["periods"][1]["manifest"]["files"][0]["digest"]
    prov = world.client.sign_provenance(cd, sample)
    world.client.accept(cd, 2, provenance=prov)
    world.provider.close(cd)
    [endorsed] = world.provider.endorse_provenance(cd)
    assert set(endorsed["signatures"]) == {"client", "market"}
    assert endorsed["sampleDigest"] == sample and object_digest(endorsed) == object_digest(prov)
    assert world.provider.id == PROVIDER
