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
from a2c.slots import verify_card
from a2c.timing import status as timing_status

from ..store import DB, jdump, now
from .discovery import Discovery


class MarketService:
    def __init__(self, signer: Signer, *, keys: Directory, fetch: Callable[[str], bytes], store, db: DB,
                 discovery: Discovery, clock: Clock | None = None, models=None) -> None:
        self.signer = signer
        self.keys = keys
        self.db = db
        self.discovery = discovery
        # Unconfigured dispute models are rejected by the A2C snapshot validator.
        from ..delegation.market import AdmissionMarket
        self.market = AdmissionMarket(signer, keys=keys, fetch=fetch, clock=clock, store=store, recognized_services={}, models=models or {})
        self.now = self.market.now

    @property
    def id(self) -> str:
        return self.signer.id

    def certificates(self):
        from ..store import jload
        return [jload(row['record']) for row in self.db.rows('SELECT record FROM certifications')]

    def endorse_service_templates(self, provider, key, templates, payment_provider):
        """Original A2C endorsement; reuse a completed snapshot after a lost response."""
        from a2c.template import validate
        from a2c.slots import verify_template
        from a2c.common import Directory
        from a2c.codec import parse_time
        from ..errors import LabError
        keys=Directory()
        keys.add(provider,{'keys':[key]})
        checked=[]
        for template in templates:
            validate(template,endorsed=False)
            if template['provider']!=provider or template['formation'] not in ('proposal','countersign'):
                raise LabError('invalid_input','Ordinary services use their bound identity and proposal or countersign templates')
            if set(template.get('signatures',{}))!={'provider'}:
                raise LabError('invalid_input','Upload the original provider-signed template without another endorsement')
            if template['pricing'].get('paymentProviders')!=[payment_provider]:
                raise LabError('invalid_input','Template must select this platform payment provider')
            verify_template(template,keys,endorsed=False)
            if parse_time(template['validUntil'])<=self.now():
                raise LabError('expired','Template is no longer valid for new publication',status=409)
            checked.append(template)
        self.keys.add(provider,{'keys':[key]})
        endorsed=[]
        for template in checked:
            snapshot=self.market.store.get('snapshots',template['templateDigest'])
            original=snapshot.get('template') if snapshot else None
            unsigned_market=lambda t:{**t,'signatures':{k:v for k,v in t['signatures'].items() if k!='market'}}
            if original and original.get('signatures',{}).get('market') and unsigned_market(original)==template:
                verify_template(original,self.keys)
                endorsed.append(original)
            else:
                endorsed.append(self.market.op_template_endorse({'template':template},{},None).body['template'])
        return endorsed

    # ── 服务认证（协议外）────────────────────────────────────────────
    def prepare_proposal(self,template,card,party):
        """Original market validation/signature in an isolated, unpublished Store."""
        from a2c.market import Market
        from a2c.common import Store
        keys=Directory();keys.add(party['id'],{'keys':[party['jwk']]})
        keys.add(self.id,{'keys':[self.market.signer.jwk]})
        staged=Market(self.market.signer,keys=keys,fetch=self.market.fetch,clock=self.market.now,store=Store(),
                      recognized_services=self.market.recognized_services,models=self.market.models)
        staged.store.put('snapshots',template['templateDigest'],{'template':template})
        reply=staged.op_card_endorse({'card':card},{},None)
        return reply.body,staged.store.get('snapshots',card_digest(card))

    def commit_snapshot_in(self,conn,key,snapshot):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Market snapshot requires the owned application transaction')
        self.market.store.put('snapshots',key,snapshot)

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
