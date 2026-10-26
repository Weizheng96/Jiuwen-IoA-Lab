"""委托方操作（一条需求上的发现 → 比较 → 拟卡 → 签字 → 开工）：ReActAgent 的 @tool 与 /api/v1 的共同底座。

全部同步阻塞（在工作线程里调用）。业务上的「不成」用返回值表达（如 {"countersigned": False, "reason": …}），
程序错误抛异常（A2CError 等），由 @tool 包装成 {"error": …}、由 REST 转成 400。
确认：平台驾驭且关了仿真代签 → 阻塞等用户在页面答复；九问 swarm 驾驭 → 不再上升确认（技能包要求 swarm 调接口前先问用户）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from a2c.card import card_digest as digest_of
from a2c.codec import digest_bytes
from a2c.errors import CARD_INVALID, fail

MAX_SWITCHES = 3


def summary_of(template: Mapping[str, Any]) -> dict:
    return {k: v for k, v in template.items() if k not in ("signatures", "a2cVersion", "kind", "samples")}


class DelegationOps:
    def __init__(self, rt, need: Mapping[str, Any]) -> None:
        self.rt, self.need = rt, dict(need)
        self.client, self.hooks = rt.client, rt.hooks
        scope = self.need.get("scope") or {}
        self.scope = scope
        self.sign1_user = scope.get("sign1") == "user"
        self.sign7_user = scope.get("sign7") == "user"
        self.auto = bool(self.need.get("auto_sign", 1))
        self.external = self.need.get("driver") == "external"
        self.scope_digest = digest_bytes(json.dumps(scope, ensure_ascii=False, sort_keys=True).encode())
        self.materials_by_name = {m["name"]: m for m in self.need.get("materials", [])}
        self.budget = float(scope.get("budget", 0) or 0)

    # ── 确认 ─────────────────────────────────────────────────────────
    def _confirm_or_reason(self, kind: str, text: str) -> str | None:
        """返回拒绝理由；None 表示可以继续。"""
        if self.auto or self.external:
            return None
        return None if self.hooks.confirm(self.need["id"], kind, text) else "用户拒绝了这一步"

    def _signers(self) -> dict:
        return {"slot1": "user" if self.sign1_user else "client", "slot7": "user" if self.sign7_user else "client"}

    def _mats(self, names: list[str] | None) -> dict:
        out = {}
        for n in names or []:
            m = self.materials_by_name.get(n)
            if m is None:
                fail(CARD_INVALID, f"没有这份材料：{n}")
            out[n] = (Path(m["path"]).read_bytes(), m.get("declared", {}))
        return out

    def tried(self) -> list[str]:
        return list(self.hooks.tried(self.need["id"])) if getattr(self.hooks, "tried", None) else []

    # ── 模板解析与规格清洗（模型会抄错摘要、填 const 键）────────────────
    def template(self, ref: str) -> dict:
        """ref 可以是模板摘要（允许前缀 ≥ 16 位十六进制）或服务方 URL（取其公示的模板；多份时取第一份）。"""
        ref = (ref or "").strip()
        if ref.startswith("http"):
            ts = self.client.get_templates(ref)
            if not ts:
                fail(CARD_INVALID, f"服务方 {ref} 没有公示的模板")
            return ts[0]
        hexpart = ref.split(":", 1)[-1].lower()
        try:
            return self.client.template(ref if ref.startswith("sha256:") else "sha256:" + hexpart)
        except Exception as e:  # noqa: BLE001
            if len(hexpart) < 16:
                raise
            cands = [(k, v) for k, v in self.client.store.all("templates").items()
                     if k.split(":", 1)[-1].startswith(hexpart[:16]) or hexpart.startswith(k.split(":", 1)[-1][:16])]
            if len(cands) != 1:
                fail(CARD_INVALID, f"模板摘要不存在或抄错（{ref[:30]}…）：请用 list_templates 返回的 templateDigest 原样复制，或直接传服务方 URL")
            t = cands[0][1]
            return t.get("template", t) if isinstance(t, dict) and "template" in t else t

    @staticmethod
    def clean_spec(template: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict, list[str]]:
        """按模板 specSchema 清洗规格：去掉 const 键与模板没有的键（委托方不填），数值型字符串转数；返回 (spec, 提示)。"""
        notes: list[str] = []
        schemas = {d["name"]: d.get("specSchema") or {} for d in template.get("deliverables", [])}
        out: dict = {}
        for dname, vals in (spec or {}).items():
            if dname not in schemas:
                if len(schemas) == 1 and isinstance(vals, dict):          # 交付件名写错：只有一个交付件时直接归到它
                    notes.append(f"规格里的「{dname}」按唯一交付件「{next(iter(schemas))}」处理")
                    dname = next(iter(schemas))
                else:
                    notes.append(f"模板没有交付件「{dname}」，已忽略")
                    continue
            sch = schemas[dname]
            cleaned = {}
            for k, v in (vals or {}).items():
                if k not in sch:
                    notes.append(f"{dname}.{k} 不在模板 specSchema 里，已忽略")
                    continue
                if "const" in sch[k]:
                    notes.append(f"{dname}.{k} 是模板固定值（const），委托方不填，已去掉")
                    continue
                if sch[k].get("type") == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
                    v = int(v)
                elif sch[k].get("type") == "number" and isinstance(v, str):
                    try:
                        v = float(v)
                    except ValueError:
                        pass
                cleaned[k] = v
            out[dname] = cleaned
        for dname in schemas:
            out.setdefault(dname, {})
        return out, notes

    # ── 发现与比较 ───────────────────────────────────────────────────
    def search(self, query: str, top_k: int = 8) -> list[dict]:
        hits = self.hooks.search(query, int(top_k))
        return [{"provider": h["card"].get("url"), "id": h["id"], "name": h["name"], "description": h["description"],
                 "status": h["card"].get("status"), "a2c": h["card"].get("a2c")} for h in hits]

    def reputation(self, provider: str) -> dict:
        return self.hooks.reputation(provider)

    def templates(self, provider: str) -> list[dict]:
        return [summary_of(t) for t in self.client.get_templates(provider)]

    def materials(self) -> list[dict]:
        out = []
        for m in self.materials_by_name.values():
            try:
                preview = Path(m["path"]).read_text(encoding="utf-8")[:300]
            except Exception:  # noqa: BLE001
                preview = ""
            out.append({"name": m["name"], "declared": m.get("declared", {}), "preview": preview})
        return out

    def wallet(self) -> str:
        return self.hooks.wallet()

    # ── 拟卡 · 签字 ──────────────────────────────────────────────────
    def draft(self, template_digest: str, card_id: str, spec: Mapping[str, Any], material_names: list[str] | None = None) -> dict:
        t = self.template(template_digest)
        spec, notes = self.clean_spec(t, spec)
        mats = self._mats(material_names)
        d = self.client.draft_card(t, card_id=card_id, spec=spec, materials=mats, signers=self._signers(),
                                   delegation_scope_digest=self.scope_digest)
        cd = digest_of(d.card)
        total = float(d.card["derived"]["total"])
        if self.budget and total > self.budget:
            return {"cardDigest": cd, "error": f"合计 {total} 超过预算上限 {self.budget}，不能签"}
        pre = {n: r.status for n, r in self.client.precheck(t, d.card, {n: b for n, (b, _x) in mats.items()}).items()}
        return {"cardDigest": cd, "cardId": card_id, "provider": t["provider"], "formation": t["formation"], "derived": d.card["derived"],
                "notices": list(d.notices) + notes, "inputPrecheck": pre}

    def sign(self, card_digest: str) -> dict:
        card = self.client.card(card_digest)
        t = self.client.template(card["templateDigest"])
        why = self._confirm_or_reason("sign", f"签署合同卡 {card.get('cardId')}：服务方 {t['provider']}，合计 {card['derived']['total']} 虚拟币")
        if why:
            return {"signed": False, "error": why}
        card = self.client.sign(card)
        self.hooks.on_phase(self.need["id"], "signed", {"cardDigest": card_digest, "provider": t["provider"]})
        if t["formation"] == "countersign":
            out = self.client.countersign(card)
            if isinstance(out, str):
                self.hooks.on_phase(self.need["id"], "declined", {"cardDigest": card_digest, "provider": t["provider"], "reason": out})
                n = len(self.tried())
                return {"signed": True, "countersigned": False, "reason": out, "switches": n,
                        "hint": "换下一家" if n < MAX_SWITCHES else f"已换过 {MAX_SWITCHES} 家，可以放弃"}
            return {"signed": True, "countersigned": True, "effective": True}
        return {"signed": True, "effective": True}

    def submit_request(self, template_digest: str, request_id: str, spec: Mapping[str, Any], material_names: list[str] | None = None) -> dict:
        t = self.template(template_digest)
        if t["formation"] != "proposal":
            fail(CARD_INVALID, "只有提案型模板才发提案卡；预批准 / 待批准型用 draft_card")
        spec, _notes = self.clean_spec(t, spec)
        why = self._confirm_or_reason("sign", f"向 {t['provider']} 发提案卡（拟卡费 {t['pricing'].get('proposalFee', 0)} 虚拟币）")
        if why:
            return {"submitted": False, "error": why}
        req = self.client.draft_request(t, request_id=request_id, version=1, spec=spec, materials=self._mats(material_names),
                                        signers=self._signers(), delegation_scope_digest=self.scope_digest)
        out = self.client.submit_request(req)
        if isinstance(out, str):
            self.hooks.on_phase(self.need["id"], "returned", {"provider": t["provider"], "reason": out})
            n = len(self.tried())
            return {"submitted": True, "returned": True, "reason": out, "switches": n,
                    "hint": "换下一家" if n < MAX_SWITCHES else f"已换过 {MAX_SWITCHES} 家，可以放弃"}
        cd = digest_of(out)
        total = float(out["derived"]["total"])
        res = {"submitted": True, "returned": False, "cardDigest": cd, "provider": t["provider"], "derived": out["derived"],
               "acceptance": list(out.get("acceptance", {}).keys())}
        if self.budget and total > self.budget:
            res["error"] = f"报价 {total} 超过预算 {self.budget}，不要采纳"
        return res

    def adopt(self, card_digest: str) -> dict:
        card = self.client.card(card_digest)
        why = self._confirm_or_reason("sign", f"采纳提案型合同卡 {card.get('cardId')}，合计 {card['derived']['total']} 虚拟币")
        if why:
            return {"adopted": False, "error": why}
        self.client.adopt(card)
        self.hooks.on_phase(self.need["id"], "signed", {"cardDigest": card_digest, "provider": card["provider"]})
        return {"adopted": True, "effective": True}

    def start(self, card_digest: str) -> dict:
        card = self.client.card(card_digest)
        out = self.client.start(card)                  # 定金在签卡时已确认过（亲签 / 预算），开工不再上升
        if "rejection" in out:
            return {"started": False, "rejected": out["rejection"].get("reason")}
        self.client.watch(card_digest, self.hooks.market)
        self.hooks.on_phase(self.need["id"], "started", {"cardDigest": card_digest})
        return {"started": True}


__all__ = ["DelegationOps", "summary_of", "MAX_SWITCHES"]
