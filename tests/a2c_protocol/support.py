"""测试共用：可拨的时钟、五方齐全的本地网络、验收器包与模板。"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from a2c.client import Client
from a2c.codec import WIRE_VERSION, digest_bytes
from a2c.common import Directory, LocalNetwork, Store
from a2c.jws import Signer
from a2c.market import Market
from a2c.payment.ap2 import AP2Payment, EcKey
from a2c.payment.psp import PaymentProvider
from a2c.provider import DefaultPolicy, Proposal, Provider
from a2c.service import CertifiedService
from a2c.verifier.package import build

T0 = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)
MARKET = "https://market.example/.well-known/agent-card.json"
PROVIDER = "https://provider.example/.well-known/agent-card.json"
SERVICE = "https://checker.example/.well-known/agent-card.json"
PAY = "https://pay.example"
PAYMENT_PROVIDER = {"id": PAY, "jwksUri": "https://pay.example/.well-known/jwks.json"}


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


# ── verifier packages ───────────────────────────────────────────────────

SOURCES = {
    "outline_check": ("script", ["spec.outline.episodes"], '''
def verify(ctx):
    text = ctx.read("outline.md").decode("utf-8")
    n = sum(1 for line in text.splitlines() if line.startswith("## "))
    want = ctx.values["spec.outline.episodes"]
    return {"status": "pass" if n >= want else "fail", "checks": [{"episodes": n, "want": want}]}
'''),
    "final_check": ("script", ["spec.final.format", "spec.final.quality"], '''
def verify(ctx):
    data = ctx.read("final.mp4")
    ok = data.startswith(b"FAKEMP4") and ctx.values["spec.final.format"] == "mp4"
    return {"status": "pass" if ok else "fail", "checks": [{"size": len(data)}]}
'''),
    "style_check": ("skill", [], '''
from a2c_verifier import observe

def verify(ctx):
    scores = sorted(observe({"ask": "style", "file": "final.mp4"})["score"] for _ in range(3))
    return {"status": "pass" if scores[1] >= 0.6 else "fail", "checks": [{"scores": scores}]}
'''),
    "booking_verify": ("service", [], '''
import json
from datetime import datetime, timedelta
from a2c_verifier import attest

def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def verify(ctx):
    plan = json.loads(ctx.read("plan.json"))
    ready = _t(ctx.anchored_at) + timedelta(hours=1)
    answer = attest({"booking": plan["booking"]})
    if _t(answer["signedAt"]) < ready:
        return {"status": "pending", "notBefore": ready.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return {"status": "pass" if answer["statement"]["confirmed"] else "fail"}
'''),
    "novel_check": ("script", ["inputs.novel.chars"], '''
def verify(ctx):
    text = ctx.read("novel").decode("utf-8")
    return {"status": "pass" if len(text) == ctx.values["inputs.novel.chars"] else "fail",
            "checks": [{"chars": len(text)}]}
'''),
}


def package(name: str, version: str = "1.0.0") -> tuple[dict, bytes]:
    kind, reads, src = SOURCES[name]
    manifest = {"id": f"{name}@{version}", "kind": kind, "runtime": "cpython-3.13-wasi",
                "entry": f"{name}:verify", "reads": reads}
    if kind == "service":
        manifest["service"] = SERVICE
    blob = build({f"{name}.py": src}, manifest)
    ref = {"id": manifest["id"], "uri": f"https://provider.example/verifiers/{name}-{version}.zip",
           "digest": digest_bytes(blob)}
    if kind == "skill":
        ref["disputeModels"] = ["model-a", "model-b"]
    return ref, blob


# ── templates ───────────────────────────────────────────────────────────

def template_draft(formation: str = "countersign", *, deposit: str = "0.1 * total", review_license: bool | None = None,
                   steps: bool = True) -> dict:
    t: dict[str, Any] = {
        "a2cVersion": WIRE_VERSION, "kind": "template", "formation": formation,
        "validUntil": "2026-12-31T23:59:59Z",
        "deliverables": [
            {"name": "outline", "description": "分集大纲", "specSchema": {"episodes": {"enum": [12, 24]}}},
            {"name": "final", "description": "成片", "specSchema": {
                "quality": {"enum": ["720p", "1080p"]}, "format": {"const": "mp4"},
                "notes": {"type": "string", "maxLength": 200}}},
        ],
        "inputs": [
            {"name": "novel", "description": "小说原文", "required": True,
             "specSchema": {"format": {"enum": ["txt", "md"]},
                            "chars": {"type": "integer", "minimum": 1, "maximum": 500000}},
             "verifiers": {"script": package("novel_check")[0]}},
            {"name": "license", "description": "改编授权书", "required": False,
             "specSchema": {"format": {"const": "pdf"}}},
        ],
        "deadlines": {"respondWithin": "P1D", "startWithin": "P3D",
                      "deliverWithin": {"outline": "P5D", "final": "P20D"},
                      "acceptWithin": {"outline": "P5D", "final": "P5D"}},
        "pricing": {"currency": "CNY",
                    "priceFactors": {"outline": {"episodes": {"12": 1.0, "24": 1.8}},
                                     "final": {"quality": {"720p": 0.9, "1080p": 1.0}}},
                    "priceFormula": {"outline": "300 * episodes",
                                     "final": "2000 * quality * episodes + 0.002 * inputs.novel.chars"},
                    "depositFormula": deposit, "paymentWeights": {"outline": 1, "final": 8},
                    "paymentProviders": [PAYMENT_PROVIDER]},
        "acceptance": {
            "outline": {"verifiers": {"script": package("outline_check")[0]}, "notCovered": []},
            "final": {"verifiers": {"script": package("final_check")[0], "skill": package("style_check")[0]},
                      "notCovered": ["节奏"]},
        },
        "samples": [],
    }
    if steps:
        t["authorizableSteps"] = [{"name": "publish", "description": "以用户名义提交平台审核"}]
        t["deadlines"]["authorizeWithin"] = "P2D"
    if review_license if review_license is not None else formation != "preapproval":
        t["inputs"][1]["reviewBeforeSigning"] = True
    if formation == "proposal":
        del t["acceptance"], t["deadlines"]["acceptWithin"]
        t["pricing"] = {"currency": "CNY", "depositFormula": deposit, "paymentWeights": {"outline": 1, "final": 8},
                        "proposalFee": 5, "paymentProviders": [PAYMENT_PROVIDER]}
    return t


class ProposalPolicy(DefaultPolicy):
    """Drafts a proposal with a service verifier on the final period."""

    def __init__(self, decline: str | None = None) -> None:
        self.decline = decline

    def draft(self, template, request, materials):
        if self.decline:
            return self.decline
        return Proposal(
            acceptance={"outline": {"verifiers": {"script": package("outline_check")[0]}, "notCovered": []},
                        "final": {"verifiers": {"script": package("final_check")[0],
                                                "service": package("booking_verify")[0]}, "notCovered": []}},
            accept_within={"outline": "P5D", "final": "P5D"}, line_items={"outline": 300, "final": 2700})


class World:
    """Market, Provider, Certified Service, Payment Provider and Client on one local network."""

    def __init__(self, *, policy=None, provider_model_score: float = 0.8) -> None:
        self.clock = Clock()
        self.net = LocalNetwork()
        self.keys = Directory()
        self.blobs = {ref["uri"]: blob for ref, blob in (package(n) for n in SOURCES)}
        self.score = 0.8
        self.confirmed = True
        self.model_calls = 0
        self.market_signer = Signer.generate(MARKET, kid="m1")
        self.provider_signer = Signer.generate(PROVIDER, kid="p1")
        self.service_signer = Signer.generate(SERVICE, kid="s1")
        self.pay_signer = Signer.generate(PAY, kid="pay1")
        for s in (self.market_signer, self.provider_signer, self.service_signer, self.pay_signer):
            self.keys.add_signer(s)
        self.provider_ap2 = EcKey("p-ap2")
        self.client_ap2 = EcKey("c-ap2")
        self.keys.add(PROVIDER, {'keys':[self.provider_signer.jwk,self.provider_ap2.jwk]})
        self.market = Market(self.market_signer, keys=self.keys, fetch=self.fetch, clock=self.clock,
                             recognized_services={SERVICE: {"keys": [self.service_signer.jwk]}},
                             models={"model-a": self.model, "model-b": self.model})
        self.provider = Provider(self.provider_signer, market=MARKET, keys=self.keys, transport=self.net,
                                 fetch=self.fetch, clock=self.clock, policy=policy or DefaultPolicy(),
                                 model=self.model, ap2_key=self.provider_ap2, name="闪剪工作室")
        self.provider.payment = AP2Payment(self.net,psp=PAY,payer=self.provider_ap2,keys=self.keys,
            instrument={'id':'provider-account','type':'account'},clock=self.clock,store=self.provider.store)
        self.service = CertifiedService(self.service_signer, issue=self.issue, clock=self.clock)
        self.psp = PaymentProvider(self.pay_signer, keys=self.keys, ap2_key=EcKey("psp-ap2"),
                                   payee_keys=lambda pid: {PROVIDER:self.provider_ap2.jwk,
                                       self.client.signer.id:self.client_ap2.jwk}[pid], clock=self.clock)
        for url, ep in ((MARKET, self.market), (PROVIDER, self.provider), (SERVICE, self.service), (PAY, self.psp)):
            self.net.register(url, ep)
        client_store = Store()
        self.client = Client(Signer.generate("https://client.example/a2c/client"), keys=self.keys, transport=self.net,
                             fetch=self.fetch,
                             payment_provider=PAYMENT_PROVIDER,
                             clock=self.clock, model=self.model, user=Signer.generate("https://client.example/a2c/user"),
                             store=client_store,ap2_key=self.client_ap2,
                             markets=(MARKET,),
                             payment=AP2Payment(self.net, psp=PAY, payer=self.client_ap2, keys=self.keys, store=client_store,
                                                instrument={"id": "card-001", "type": "card"}, clock=self.clock))
        self.keys.add(self.client.signer.id,{'keys':[self.client.signer.jwk,self.client_ap2.jwk]})

    def fetch(self, uri: str) -> bytes:
        return self.blobs[uri]

    def model(self, request, files):
        self.model_calls += 1
        return {"score": self.score}

    def issue(self, verifier, values, files):
        return {"booking": values["booking"], "confirmed": self.confirmed}

    # ── conveniences ────────────────────────────────────────────────────

    NOVEL = "从前有座山" * 20

    def materials(self, *, chars: int | None = None, license_: bool = True) -> dict:
        m = {"novel": (self.NOVEL.encode(), {"format": "md", "chars": chars if chars is not None else len(self.NOVEL)})}
        if license_:
            m["license"] = (b"%PDF-1.7 license", {})
        return m

    def spec(self) -> dict:
        return {"outline": {"episodes": 12}, "final": {"quality": "1080p", "notes": "保留雨夜对峙"}}

    def publish(self, formation: str = "countersign", **kw) -> dict:
        return self.provider.publish(template_draft(formation, **kw))

    def formed_card(self, formation: str = "countersign", signers=None, **kw) -> dict:
        """Publish, fetch, draft, sign (and countersign): an effective card on the client side."""
        t = self.publish(formation, **kw)
        [t] = self.client.get_templates(PROVIDER, t["templateDigest"])
        draft = self.client.draft_card(t, card_id="C-1", spec=self.spec(), materials=self.materials(),
                                       signers=signers or {"slot1": "user", "slot7": "client"},
                                       delegation_scope_digest="sha256:" + "d" * 64)
        card = self.client.sign(draft.card)
        if formation == "countersign":
            card = self.client.countersign(card)
        return card

    def deliver(self, cd: str, period: int, *, good: bool = True) -> dict:
        files = ({"outline.md": ("\n".join(f"## 第{i}集" for i in range(1, 13 if good else 3)).encode(), "text/markdown")}
                 if period == 1 else {"final.mp4": ((b"FAKEMP4" if good else b"BROKEN") + b"x" * 64, "video/mp4")})
        return self.provider.deliver(cd, files)


def card_files(n: int) -> dict:
    return {f"f{i}.txt": (f"file {i}".encode(), "text/plain") for i in range(n)}


__all__ = ["T0", "MARKET", "PROVIDER", "SERVICE", "PAY", "Clock", "World", "package", "template_draft",
           "ProposalPolicy", "SOURCES", "copy", "json"]
