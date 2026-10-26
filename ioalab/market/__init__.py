"""Agent 交易市场：平台方 SDK（a2c.market.Market）+ 市场自有服务（认证记录 · 发现 · 信誉条目 · 仲裁文书）。

市场不看钱款收付，只验签名链。仲裁到裁定文书为止，不划转。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from a2c import objects
from a2c.card import card_digest
from a2c.codec import digest_bytes, fmt_time
from a2c.common import Clock, Directory
from a2c.errors import CARD_INVALID, fail
from a2c.jws import Signer, header_of
from a2c.market import Market
from a2c.slots import verify_card
from a2c.timing import status as timing_status

from ..store import DB, jdump, now
from .discovery import Discovery


class MarketService:
    def __init__(self, signer: Signer, *, keys: Directory, fetch: Callable[[str], bytes], store, db: DB,
                 discovery: Discovery, clock: Clock | None = None) -> None:
        self.signer = signer
        self.keys = keys
        self.db = db
        self.discovery = discovery
        # Market(models={})：skill 验收器的模板自然拒签（首版只认 script）
        self.market = Market(signer, keys=keys, fetch=fetch, clock=clock, store=store, recognized_services={}, models={})
        self.now = self.market.now

    @property
    def id(self) -> str:
        return self.signer.id

    # ── 服务认证（协议外）────────────────────────────────────────────
    def certify(self, agent_id: str, jwk: Mapping[str, Any], name: str) -> dict:
        """认主体身份，发一条认证记录（市场签名的独立文书，A2C 不消费）；公钥入 Directory。"""
        self.keys.add(agent_id, {"keys": [dict(jwk)]})
        rec = {"kind": "certification", "subject": agent_id, "name": name, "issuedAt": fmt_time(self.now()),
               "jwkThumbprint": digest_bytes(jdump({k: jwk[k] for k in sorted(jwk) if k != "kid"}).encode())}
        rec["signature"] = _sign_free(self.signer, rec, self.now())
        self.db.run("INSERT OR REPLACE INTO certifications (agent_id, at, record) VALUES (?,?,?)", agent_id, now(), jdump(rec))
        return rec

    # ── 信誉条目（协议外；依据是结清的卡与裁定）───────────────────────
    def reputation(self, provider_id: str) -> dict:
        ledger = self.market.store.all("ledger")
        rulings = self.market.store.all("rulings")
        total = success = 0
        for cd, e in ledger.items():
            pid = e.get("provider") or (e.get("card") or {}).get("provider")
            if pid != provider_id:
                continue
            outcome = e.get("outcome")
            if outcome == "completed":
                total += 1
                success += 1
            elif outcome == "arbitrated":
                r = rulings.get(cd, {})
                total += 1
                if r.get("liable") == "client":
                    success += 1
            # client-terminated：不计
        entry = {"kind": "reputation", "provider": provider_id, "completed": total,
                 "successRate": round(success / total, 3) if total else None, "issuedAt": fmt_time(self.now())}
        entry["signature"] = _sign_free(self.signer, entry, self.now())
        return entry

    # ── 仲裁（协议外接口）────────────────────────────────────────────
    def arbitrate(self, *, card: Mapping[str, Any], period: int, files: Mapping[str, bytes] | None,
                  report: Mapping[str, Any] | None, applicant: str, ground: str) -> dict:
        """两类裁定同一格式：复算类（recompute）与时限类（deadline）。裁定文书引用卡摘要，不上卡。"""
        cd = card_digest(card)
        template = self.market._template(card["templateDigest"])
        chain = verify_card(template, card, self.keys)
        kind, liable, evidence = "deadline", "provider", {}
        if ground in ("acceptance-failed", "fetch-failed") and files is not None:
            manifest = (card["fulfillment"].get("periods") or [{}] * period)[period - 1].get("manifest")
            if not manifest:
                fail(CARD_INVALID, "本期没有交付件清单")
            want = {f["name"]: f["digest"] for f in manifest["files"]}
            got = {n: digest_bytes(b) for n, b in files.items()}
            if want != got:
                fail(CARD_INVALID, "提交的交付件与签4 清单的摘要不符，驳回")
            result = self.market.recompute(card, period, files)
            kind = "recompute"
            liable = "provider" if result.status != "pass" else "client"
            evidence = {"recompute": result.status, "runs": [{"verifier": r.ref["id"], "status": r.outcome.status}
                                                             for r in result.runs], "errors": result.errors}
        else:
            st = timing_status(template, card, chain, self.now(), [], [])
            evidence = {"phase": st.phase, "clientGrounds": st.client_grounds, "providerGrounds": st.provider_grounds,
                        "ground": ground}
            liable = {"accept-overdue": "client", "payment-overdue": "client", "deliver-overdue": "provider",
                      "start-overdue": "provider", "authorize-overdue": "client"}.get(ground, "provider")
        ruling = {"kind": "ruling", "cardDigest": cd, "period": period, "rulingKind": kind, "liable": liable,
                  "applicant": applicant, "ground": ground, "evidence": evidence, "issuedAt": fmt_time(self.now())}
        ruling["signature"] = _sign_free(self.signer, ruling, self.now())
        self.market.record_ruling(card, ruling)
        self.db.run("INSERT OR REPLACE INTO rulings (card_digest, period, at, ruling) VALUES (?,?,?,?)", cd, period, now(), jdump(ruling))
        return ruling

    def ledger(self) -> dict:
        return self.market.store.all("ledger")


def _sign_free(signer: Signer, obj: Mapping[str, Any], ts) -> dict:
    """市场自有文书的签名：对对象（去签名）摘要签 JWS（a2c-slot=market）。"""
    from a2c.jws import sign
    body = {k: v for k, v in obj.items() if k != "signature"}
    return sign(objects.object_digest(body), signer, "market", ts)


__all__ = ["MarketService", "Discovery"]
