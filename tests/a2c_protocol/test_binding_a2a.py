"""同一套流程跑在真实的 A2A（a2a-sdk 1.0、JSON-RPC）上：五方各是一个 A2A 智能体。"""
import json

import time

import httpx
import pytest

from a2c.binding import A2C_EXTENSION, AP2_EXTENSION, A2ATransport, HostRouter, build_app, from_parts, to_parts
from a2c.card import card_digest
from a2c.common import Directory

from .support import MARKET, PROVIDER, ProposalPolicy, World


def a2a_world(**kw) -> World:
    w = World(**kw)
    apps = {
        "market.example": build_app(w.market, name="Agent 市场", base_url="https://market.example", role="market",
                                    jwks={"keys": [w.market_signer.jwk]}),
        "provider.example": build_app(w.provider, name="闪剪工作室", base_url="https://provider.example",
                                      role="provider", jwks={"keys": [w.provider_signer.jwk]},
                                      extensions=(A2C_EXTENSION, AP2_EXTENSION)),
        "checker.example": build_app(w.service, name="预订核验", base_url="https://checker.example", role="service",
                                     jwks={"keys": [w.service_signer.jwk]}),
        "pay.example": build_app(w.psp, name="支付方", base_url="https://pay.example", role="payment",
                                 jwks={"keys": [w.pay_signer.jwk]}, extensions=(AP2_EXTENSION,)),
    }
    router = HostRouter(apps)

    def transport():
        return A2ATransport(http=lambda: httpx.AsyncClient(transport=router, base_url="https://x", timeout=60))

    w.provider.transport = transport()
    t = transport()
    w.client.transport = t
    w.client.keys = Directory(fetch=t.jwks)            # the Client learns every key over A2A
    w.client.payment.transport = t
    w.client.payment.psp = "https://pay.example"
    return w


def test_parts_roundtrip_keeps_integers():
    body = {"op": "x", "n": 3, "f": 1.5, "nested": [{"period": 2}]}
    out, files = from_parts(to_parts(body, {"a.txt": b"hi"}))
    assert out == body and type(out["n"]) is int and files == {"a.txt": b"hi"}


def test_countersign_over_a2a():
    """待批准型全流程走 A2A：复签、开工（交付任务）、环节请求经 GetTask 呈现、存证通知、取件、验收、付款、结清。"""
    w = a2a_world()
    card = w.formed_card("countersign")
    cd = card_digest(card)
    assert "receipt" in w.client.start(card)
    w.client.watch(cd, MARKET)
    w.provider.request_step(cd, "publish")
    [ev] = w.client.poll(cd)
    assert ev["type"] == "step-request"
    w.client.answer_step(cd, granted=True)
    for period in (1, 2):
        w.deliver(cd, period)
        assert {"type": "anchored", "period": period} in w.client.poll(cd)
        w.client.fetch_delivery(cd, period)
        assert w.client.accept(cd, period).status == "pass"
    w.provider.close(cd)
    assert w.market.store.get("ledger", cd)["outcome"] == "completed"
    assert [e["purpose"] for e in w.psp.ledger] == ["deposit", "installment", "installment"]


def test_proposal_over_a2a():
    """提案型走 A2A：拟卡费、提案卡、签3、第三方认证结论。"""
    w = a2a_world(policy=ProposalPolicy())
    t = w.publish("proposal")
    [t] = w.client.get_templates(PROVIDER, t["templateDigest"])
    request = w.client.draft_request(t, request_id="R-9", spec=w.spec(), materials=w.materials(),
                                     signers={"slot1": "client", "slot7": "client"},
                                     delegation_scope_digest="sha256:" + "d" * 64)
    card = w.client.adopt(w.client.submit_request(request))
    w.client.start(card)
    cd = card_digest(card)
    w.deliver(cd, 1)
    w.client.fetch_delivery(cd, 1)
    w.client.accept(cd, 1)
    w.provider.deliver(cd, {"plan.json": (json.dumps({"booking": "B-1"}).encode(), "application/json"),
                            "final.mp4": (b"FAKEMP4 x", "video/mp4")})
    w.client.fetch_delivery(cd, 2)
    w.clock.advance(hours=2)
    assert w.client.accept(cd, 2).status == "pass"


def test_extension_is_required():
    """不声明 A2C 扩展的请求被拒（ExtensionSupportRequiredError）。"""
    from a2a.types import Message, Role, SendMessageRequest
    w = a2a_world()
    t = w.client.transport

    async def bare():
        client = await t._client(PROVIDER)
        msg = Message(role=Role.ROLE_USER, message_id="m-1", parts=to_parts({"op": "template.get"}))
        async for _r in client.send_message(SendMessageRequest(message=msg)):
            pass

    with pytest.raises(Exception) as e:
        t._run(bare())
    assert type(e.value).__name__ == "ExtensionSupportRequiredError"
    assert "templates" in t.send(PROVIDER, "template.get", {}).body


def test_a_slow_review_does_not_hold_the_request():
    """复签要等服务方看过：SendMessage 立即返回任务，委托方轮询 GetTask 取结果。"""
    from a2c.provider import DefaultPolicy

    class Slow(DefaultPolicy):
        def review(self, template, card, materials):
            time.sleep(0.6)
            return None
    w = a2a_world(policy=Slow())
    polls = []
    original = w.client.transport.get_task

    def counted(to, task_id):
        polls.append(task_id)
        return original(to, task_id)
    w.client.transport.get_task = counted
    card = w.formed_card("countersign")
    assert card["signatures"]["2"] and polls
