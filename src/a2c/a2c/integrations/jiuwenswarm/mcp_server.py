"""The A2C Client SDK as an MCP server (stdio), for jiuwenswarm or any MCP host.

jiuwenswarm needs no code change: install the connector package in ``connector/`` (or add one
``mcp.servers`` entry) and gate the signing tools with ``permissions.tools.<tool>: ask`` so the
user confirms every signature and payment. Contracts persist in ``$A2C_HOME``; long waits are
polled with ``poll`` (e.g. from a heartbeat job).

Environment: ``A2C_HOME`` · ``A2C_MARKETS`` (comma-separated Market Agent Card URLs) ·
``A2C_PAYMENT_PROVIDER`` and ``A2C_PAYMENT_JWKS`` (the card's ``paymentProvider``) · ``A2C_PSP``
(its AP2 endpoint) · ``A2C_INSTRUMENT`` · optional ``A2C_MODEL_URL`` / ``A2C_MODEL_KEY`` /
``A2C_MODEL`` (an OpenAI-compatible model for skill verifiers).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from ...card import card_digest as digest_of
from ...client import Client
from ...codec import utcnow
from ...common import Directory, Store
from ...jws import Signer

INSTRUCTIONS = ("A2C 合同工具：取合同模板、拟合同卡、签字、交材料、取件、验收、付款与解除。"
                "签字、付款、环节授权、解除前须经用户确认。详细流程见 a2c-client 技能。")


# ── identity and wiring ─────────────────────────────────────────────────

def load_identity(home: Path) -> tuple[Signer, Signer, Any]:
    """The client and user Ed25519 keys and the AP2 payer key, created on first use (kept in ``home``)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, load_pem_private_key

    from ...payment.ap2 import EcKey

    path = home / "keys.json"
    if not path.exists():
        def raw(k):
            return base64.b64encode(k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
        ec_key = ec.generate_private_key(ec.SECP256R1())
        data = {"clientId": os.environ.get("A2C_CLIENT_ID", "urn:a2c:client:" + os.urandom(8).hex()),
                "client": raw(Ed25519PrivateKey.generate()), "user": raw(Ed25519PrivateKey.generate()),
                "ap2": ec_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()}
        home.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    data = json.loads(path.read_text(encoding="utf-8"))

    def key(b64):
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(b64))
    client = Signer(id=data["clientId"], key=key(data["client"]))
    user = Signer(id=data["clientId"] + ":user", key=key(data["user"]))
    return client, user, EcKey("payer", load_pem_private_key(data["ap2"].encode(), None))


def openai_model(base_url: str, api_key: str, model: str):
    """A ModelPort over an OpenAI-compatible chat endpoint: the observe request in, one JSON object out."""
    import httpx

    def ask(request, files):
        listing = {n: len(b) for n, b in files.items()}
        r = httpx.post(base_url.rstrip("/") + "/chat/completions", timeout=120,
                       headers={"Authorization": f"Bearer {api_key}"},
                       json={"model": model, "response_format": {"type": "json_object"},
                             "messages": [{"role": "system", "content": "You observe a deliverable for an acceptance "
                                           "verifier. Answer with exactly one JSON object."},
                                          {"role": "user", "content": json.dumps({"request": request, "files": listing},
                                                                                  ensure_ascii=False)}]})
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])
    return ask


def build_client(home: Path) -> Client:
    import httpx

    from ...binding import A2ATransport
    from ...payment.ap2 import AP2Payment

    client_signer, user_signer, payer = load_identity(home)
    transport = A2ATransport()
    keys = Directory(fetch=transport.jwks)
    store = Store(home / 'store.json')
    model = None
    if os.environ.get("A2C_MODEL_URL"):
        model = openai_model(os.environ["A2C_MODEL_URL"], os.environ.get("A2C_MODEL_KEY", ""),
                             os.environ.get("A2C_MODEL", ""))
    payment = None
    if os.environ.get("A2C_PSP"):
        payment = AP2Payment(transport, psp=os.environ["A2C_PSP"], payer=payer, clock=utcnow, keys=keys, store=store,
                             instrument={"id": os.environ.get("A2C_INSTRUMENT", "default"), "type": "card"})
    return Client(client_signer, keys=keys, transport=transport,
                  fetch=lambda uri: httpx.get(uri, timeout=60).raise_for_status().content,
                  payment_provider={"id": os.environ.get("A2C_PAYMENT_PROVIDER", ""),
                                    "jwksUri": os.environ.get("A2C_PAYMENT_JWKS", "")},
                  store=store, payment=payment, model=model, user=user_signer, ap2_key=payer,
                  markets=tuple(m for m in os.environ.get("A2C_MARKETS", "").split(",") if m))


# ── tools ───────────────────────────────────────────────────────────────

def _summary(template: dict) -> dict:
    """What an agent reads to compare offers; signatures and package digests left out."""
    return {k: v for k, v in template.items() if k not in ("signatures", "a2cVersion", "kind")}


def _materials(materials: dict[str, dict]) -> dict:
    return {name: (Path(m["path"]).read_bytes(), m.get("declared", {})) for name, m in materials.items()}


def _out(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def build_server(client: Client, home: Path):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("a2c", instructions=INSTRUCTIONS)

    @mcp.tool()
    def list_templates(provider: str) -> str:
        """取服务方的合同模板（已验签、未过期）。provider 为服务方 Agent Card URL。"""
        return _out([_summary(t) for t in client.get_templates(provider)])

    @mcp.tool()
    def draft_card(template_digest: str, card_id: str, spec: dict, materials: dict, user_signs_slot1: bool = False,
                   user_signs_slot7: bool = False, delegation_scope_digest: str = "sha256:" + "0" * 64) -> str:
        """按模板拟合同卡（预批准型、待批准型）。spec：{交付件: {键: 值}}；materials：{材料名: {path, declared}}。"""
        t = client.template(template_digest)
        signers = {"slot1": "user" if user_signs_slot1 else "client", "slot7": "user" if user_signs_slot7 else "client"}
        d = client.draft_card(t, card_id=card_id, spec=spec, materials=_materials(materials), signers=signers,
                              delegation_scope_digest=delegation_scope_digest)
        pre = {n: r.status for n, r in client.precheck(t, d.card, {n: b for n, (b, _x) in _materials(materials).items()}).items()}
        return _out({"cardDigest": digest_of(d.card), "derived": d.card["derived"], "notices": d.notices,
                     "inputPrecheck": pre})

    @mcp.tool()
    def sign_card(card_digest: str) -> str:
        """签1（须经用户确认）；待批准型随即请服务方复签。"""
        card = client.sign(client.card(card_digest))
        t = client.template(card["templateDigest"])
        if t["formation"] == "countersign":
            out = client.countersign(card)
            return _out({"countersigned": isinstance(out, dict), "reason": out if isinstance(out, str) else None})
        return _out({"signed": True})

    @mcp.tool()
    def submit_request(template_digest: str, request_id: str, spec: dict, materials: dict, version: int = 1,
                       user_signs_slot1: bool = False, user_signs_slot7: bool = False,
                       delegation_scope_digest: str = "sha256:" + "0" * 64) -> str:
        """提案型：付拟卡费（如收）、签提案卡并发出；返回服务方拟的合同卡或退回理由（须经用户确认）。"""
        t = client.template(template_digest)
        signers = {"slot1": "user" if user_signs_slot1 else "client", "slot7": "user" if user_signs_slot7 else "client"}
        req = client.draft_request(t, request_id=request_id, version=version, spec=spec, materials=_materials(materials),
                                   signers=signers, delegation_scope_digest=delegation_scope_digest)
        out = client.submit_request(req)
        if isinstance(out, str):
            return _out({"reason": out})
        return _out({"cardDigest": digest_of(out), "acceptance": out["acceptance"], "derived": out["derived"]})

    @mcp.tool()
    def start(card_digest: str, market: str = "") -> str:
        """付定金（如有）、交材料、开工；可同时订阅市场的存证通知（须经用户确认）。"""
        out = client.start(client.card(card_digest))
        if "receipt" in out and market:
            client.watch(card_digest, market)
        return _out({k: (v["reason"] if k == "rejection" else "ok") for k, v in out.items()})

    @mcp.tool()
    def poll(card_digest: str) -> str:
        """查新事件：anchored（某期已存证，可取件）、step-request（服务方请求以用户名义办事）；附当前状态与期限。"""
        events = client.poll(card_digest)
        st = client.status(card_digest)
        return _out({"events": events, "phase": st.phase, "period": st.period,
                     "deadline": st.deadline.isoformat() if st.deadline else None,
                     "clientGrounds": st.client_grounds, "providerGrounds": st.provider_grounds})

    @mcp.tool()
    def answer_step(card_digest: str, granted: bool) -> str:
        """答复环节请求：同意或拒绝都签名（须经用户确认）。"""
        client.answer_step(card_digest, granted)
        return _out({"answered": granted})

    @mcp.tool()
    def fetch(card_digest: str, period: int) -> str:
        """取第 period 期交付件、核对清单、签收（签6），文件存到本地并返回路径。"""
        files = client.fetch_delivery(card_digest, period)
        target = home / "deliveries" / card_digest.split(":")[1][:16] / str(period)
        target.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (target / Path(name).name).write_bytes(data)
        return _out({"files": [str(target / Path(n).name) for n in files]})

    def _results(result) -> dict:
        return {"status": result.status, "notBefore": result.not_before, "errors": result.errors,
                "results": [{"verifier": r.ref["id"], "status": r.outcome.status, "checks": r.outcome.checks}
                            for r in result.runs]}

    @mcp.tool()
    def verify(card_digest: str, period: int) -> str:
        """运行本期验收器，不签名：先把结果给用户看，再由用户确认 accept。"""
        return _out(_results(client.verify(card_digest, period)))

    @mcp.tool()
    def accept(card_digest: str, period: int) -> str:
        """运行本期验收器：通过即签7、付本期款（须经用户确认）；不通过签验收报告；还不能判时返回最早可判时间。"""
        out = client.accept(card_digest, period)
        return _out({**_results(out.result), "paid": out.voucher["amount"] if out.voucher else None})

    @mcp.tool()
    def terminate(card_digest: str, agree_quote: bool = True) -> str:
        """解除：先取服务方报价；agree_quote 为真则协商解除并补付余额，否则单方解除（须经用户确认）。"""
        quote = client.termination_quote(card_digest) if agree_quote else None
        client.terminate(card_digest, quote=quote)
        return _out({"terminated": True, "fee": quote["fee"] if quote else None})

    return mcp


def main() -> None:
    home = Path(os.environ.get("A2C_HOME", Path.home() / ".a2c")).expanduser()
    build_server(build_client(home), home).run()


if __name__ == "__main__":
    main()


__all__ = ["build_server", "build_client", "load_identity", "openai_model", "main"]
