"""委托方开放接口 /api/v1：九问 swarm（技能包 scripts/ioalab.py）用它当个人助手。

平台替账号保管密钥与 a2c Client；swarm 做发现 → 比较 → 拟卡 → 签字 → 开工，并答复确认；签约后的固定流程由平台跑。
全部需要 Authorization: Bearer <token>（POST /api/v1/token 取）。需求的驾驭方式记 external。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from a2c.errors import CARD_INVALID, fail

from ..store import jdump, jload, now

MAX_MATERIAL = 2 * 1024 * 1024
MAX_MATERIALS = 10
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_FILE = re.compile(r"[^A-Za-z0-9_.\-一-鿿]")


def build_router(platform, current_user: Callable[..., str]) -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    db, s = platform.db, platform.s

    async def body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:  # noqa: BLE001
            return {}

    def need_of(uid: str, nid: str) -> dict:
        n = platform.need(nid)
        if n["owner_id"] != uid:
            raise HTTPException(403, "不是你的需求")
        return n

    def order_of(uid: str, cd: str) -> dict:
        o = db.one("SELECT * FROM orders WHERE card_digest=?", cd)
        if not o:
            raise HTTPException(404, "没有这张合同卡的订单")
        if o["owner_id"] != uid:
            raise HTTPException(403, "不是你的订单")
        return o

    def assistant_of(uid: str) -> dict:
        a = db.one("SELECT * FROM agents WHERE owner_id=? AND role='assistant'", uid)
        if not a:
            a = platform.register_assistant(uid, "九问 Swarm")
        platform.db.run("UPDATE agents SET meta=? WHERE id=?", jdump({**jload(a.get("meta") or "{}"), "last_driver": "external"}), a["id"])
        return a

    def ops_for(uid: str, nid: str):
        return platform.ops_for(need_of(uid, nid))

    async def run(fn, *a):
        return await asyncio.to_thread(fn, *a)

    # ── 账号 ─────────────────────────────────────────────────────────
    @r.post("/token")
    async def token(request: Request):
        b = await body(request)
        u = await run(lambda: platform.login(b.get("username", ""), b.get("password", ""), ip=request.client.host if request.client else ""))
        a = await run(assistant_of, u["id"])
        return {"token": platform.auth.bearer(u["id"]), "id": u["id"], "username": u["username"], "kind": u["kind"],
                "display_name": u["display_name"], "assistant": a["id"], "credited": u.get("credited", False)}

    @r.get("/me")
    async def me(uid: str = Depends(current_user)):
        u = db.one("SELECT id, username, kind, display_name FROM users WHERE id=?", uid)
        a = db.one("SELECT id, name FROM agents WHERE owner_id=? AND role='assistant'", uid)
        return {**u, "assistant": a["id"] if a else None, "wallet": str(platform.wallets.balance(uid)),
                "pending": db.rows("SELECT id, need_id, kind, text, created FROM confirmations WHERE owner_id=? AND state='pending' ORDER BY created", uid),
                "active": db.rows("SELECT id, text, state, card_digest FROM needs WHERE owner_id=? AND state IN ('open','running','started') ORDER BY created DESC", uid)}

    # ── 需求 ─────────────────────────────────────────────────────────
    @r.post("/delegations")
    async def create(request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        if b.get("materials"):
            raise HTTPException(400, "材料请用 POST /delegations/{id}/materials 上传，不接受路径")
        a = await run(assistant_of, uid)
        scope = dict(b.get("scope") or {})
        scope.setdefault("sign1", "client")
        scope.setdefault("sign7", "client")
        n = await run(lambda: platform.create_need(uid, a["id"], (b.get("text") or "").strip(), scope, auto_sign=bool(b.get("autoSign", False)),
                                                    materials=[], driver="external"))
        return n

    @r.post("/delegations/{nid}/materials")
    async def upload(nid: str, file: UploadFile = File(...), name: str = Form(""), declared: str = Form("{}"), uid: str = Depends(current_user)):
        n = need_of(uid, nid)
        if n["state"] not in ("open",):
            raise HTTPException(400, "需求已在进行中，不能再加材料")
        mats = list(n.get("materials") or [])
        if len(mats) >= MAX_MATERIALS:
            raise HTTPException(400, f"最多 {MAX_MATERIALS} 件材料")
        data = await file.read()
        if len(data) > MAX_MATERIAL:
            raise HTTPException(400, f"单件材料不超过 {MAX_MATERIAL // 1024 // 1024} MB")
        mname = (name or Path(file.filename or "material").stem)
        if not _NAME.match(mname):
            raise HTTPException(400, "材料名须是字母数字下划线（对应模板 inputs 的 name）")
        try:
            decl = json.loads(declared or "{}")
            assert isinstance(decl, dict)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "declared 须是 JSON 对象")
        fname = _FILE.sub("_", Path(file.filename or mname).name) or mname
        dst = s.data / "needs" / nid / fname
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        mats = [m for m in mats if m["name"] != mname] + [{"name": mname, "path": str(dst), "declared": decl, "size": len(data), "file": fname}]
        sc = jload(db.one("SELECT scope FROM needs WHERE id=?", nid)["scope"])
        sc["materials"] = mats
        db.run("UPDATE needs SET scope=?, updated=? WHERE id=?", jdump(sc), now(), nid)
        return {"materials": [{k: v for k, v in m.items() if k != "path"} for m in mats]}

    @r.get("/delegations/{nid}")
    async def detail(nid: str, uid: str = Depends(current_user)):
        n = need_of(uid, nid)
        n["materials"] = [{k: v for k, v in m.items() if k != "path"} for m in n.get("materials", [])]
        orders = db.rows("SELECT card_digest, provider_id, formation, total, state, phase, created, updated FROM orders WHERE need_id=? ORDER BY created", nid)
        for o in orders:
            o["provider_name"] = (db.one("SELECT name FROM agents WHERE id=?", o["provider_id"].rsplit("/", 1)[-1]) or {}).get("name")
        return {**n, "orders": orders,
                "pending": db.rows("SELECT id, kind, text, created FROM confirmations WHERE need_id=? AND state='pending' ORDER BY created", nid),
                "tried": platform.tried(nid)}

    @r.get("/delegations/{nid}/events")
    async def events(nid: str, after: int = 0, limit: int = 200, uid: str = Depends(current_user)):
        n = need_of(uid, nid)
        cds = [o["card_digest"] for o in db.rows("SELECT card_digest FROM orders WHERE need_id=?", nid)]
        rows = db.rows("SELECT seq, at, kind, op, sender, receiver, card_digest, period, amount, outcome, detail FROM events "
                       "WHERE seq>? AND (card_digest IN (%s) OR (op LIKE 'need.%%' AND outcome=?) OR (sender=? AND op='discovery.search')) ORDER BY seq LIMIT ?"
                       % (",".join("?" * len(cds)) if cds else "''"), after, *cds, nid, n["assistant_id"], limit)
        for e in rows:
            e["detail"] = jload(e["detail"])
        return rows

    @r.post("/delegations/{nid}/abandon")
    async def abandon(nid: str, uid: str = Depends(current_user)):
        need_of(uid, nid)
        await run(platform.abandon_need, nid)
        return {"ok": True}

    # ── 发现 · 比较 ──────────────────────────────────────────────────
    @r.post("/delegations/{nid}/search")
    async def search(nid: str, request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        ops = ops_for(uid, nid)
        return await run(ops.search, b.get("query", ""), int(b.get("top_k", 8)))

    @r.get("/providers/reputation")
    async def reputation(provider: str, uid: str = Depends(current_user)):
        return await run(platform.market.reputation, provider)

    @r.get("/providers/templates")
    async def templates(provider: str, uid: str = Depends(current_user)):
        a = assistant_of(uid)
        rt = platform.assistants[a["id"]]
        from ..runtime.ops import summary_of
        ts = await run(rt.client.get_templates, provider)
        return [summary_of(t) for t in ts]

    @r.get("/delegations/{nid}/materials")
    async def materials(nid: str, uid: str = Depends(current_user)):
        return await run(ops_for(uid, nid).materials)

    # ── 拟卡 · 签字 · 开工 ───────────────────────────────────────────
    @r.post("/delegations/{nid}/cards")
    async def draft(nid: str, request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        ops = ops_for(uid, nid)
        return await run(ops.draft, b.get("templateDigest", ""), b.get("cardId") or f"C-{nid[-6:]}", b.get("spec") or {}, b.get("materials") or [])

    @r.post("/delegations/{nid}/cards/{cd}/sign")
    async def sign(nid: str, cd: str, uid: str = Depends(current_user)):
        return await run(ops_for(uid, nid).sign, cd)

    @r.post("/delegations/{nid}/requests")
    async def submit_request(nid: str, request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        ops = ops_for(uid, nid)
        return await run(ops.submit_request, b.get("templateDigest", ""), b.get("requestId") or f"R-{nid[-6:]}", b.get("spec") or {}, b.get("materials") or [])

    @r.post("/delegations/{nid}/cards/{cd}/adopt")
    async def adopt(nid: str, cd: str, uid: str = Depends(current_user)):
        return await run(ops_for(uid, nid).adopt, cd)

    @r.post("/delegations/{nid}/cards/{cd}/start")
    async def start(nid: str, cd: str, uid: str = Depends(current_user)):
        return await run(ops_for(uid, nid).start, cd)

    # ── 确认 ─────────────────────────────────────────────────────────
    @r.get("/confirmations")
    async def confirmations(uid: str = Depends(current_user)):
        return db.rows("SELECT id, need_id, card_digest, kind, text, state, created FROM confirmations WHERE owner_id=? AND state='pending' ORDER BY created", uid)

    @r.post("/confirmations/{cid}")
    async def answer(cid: str, request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        platform.answer_confirmation(uid, cid, bool(b.get("granted")))
        return {"ok": True}

    # ── 签约后：订单 · 交付件 · 补付 · 仲裁 · 解除 ─────────────────────
    @r.get("/orders/{cd}")
    async def order(cd: str, uid: str = Depends(current_user)):
        order_of(uid, cd)
        d = platform.order_detail(cd)
        d["provider_name"] = (db.one("SELECT name FROM agents WHERE id=?", (d.get("provider_id") or "").rsplit("/", 1)[-1]) or {}).get("name")
        return d

    @r.get("/orders/{cd}/deliveries/{period}")
    async def deliveries(cd: str, period: int, uid: str = Depends(current_user)):
        o = order_of(uid, cd)
        rt = platform.assistants[o["assistant_id"]]
        home = rt.home / "deliveries" / cd.split(":")[1][:16] / str(period)
        if not home.exists():
            raise HTTPException(404, "本期尚未取件")
        files = {}
        for p in sorted(home.iterdir()):
            if p.is_file():
                try:
                    files[p.name] = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    files[p.name] = {"bytes": p.stat().st_size}
        return {"cardDigest": cd, "period": period, "files": files}

    @r.post("/orders/{cd}/pay/{period}")
    async def pay(cd: str, period: int, uid: str = Depends(current_user)):
        o = order_of(uid, cd)
        return await run(platform.pay_installment, o["assistant_id"], cd, period)

    @r.post("/orders/{cd}/arbitrate")
    async def arbitrate(cd: str, request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        o = order_of(uid, cd)
        period = int(b.get("period", 1))
        ruling = await run(platform._arbitrate_for, o["assistant_id"], cd, period, b.get("ground") or "acceptance-failed")
        platform._on_phase(o["need_id"], "arbitrated", {"cardDigest": cd, "period": period, "liable": ruling.get("liable")})
        return ruling

    @r.post("/orders/{cd}/terminate")
    async def terminate(cd: str, request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        o = order_of(uid, cd)
        return await run(platform.terminate_order, o, bool(b.get("agree", True)))

    # ── 外部服务方接入 ───────────────────────────────────────────────
    @r.post("/provider/packages")
    async def provider_package(request: Request, uid: str = Depends(current_user)):
        """上传 L1 脚本验收器源码 → ioalab://pkg/<digest>（写进模板 acceptance）。body {verifierId, source, reads[]}"""
        b = await body(request)
        src = str(b.get("source") or "")
        if not src or len(src.encode()) > 200_000:
            raise HTTPException(400, "source 须是 ≤ 200 KB 的 Python 源码（def verify(ctx)）")
        return await run(lambda: platform.packages.put(verifier_id=str(b.get("verifierId") or ""), source=src, reads=list(b.get("reads") or [])))

    @r.get("/packages/{digest}")
    async def package_blob(digest: str):
        """按摘要取验收器包（内容寻址，外部服务方自测与三方验收都从这里取）。"""
        from fastapi.responses import Response
        try:
            data = platform.packages.fetch("ioalab://pkg/" + digest)
        except Exception:  # noqa: BLE001
            raise HTTPException(404, "没有这个验收器包")
        return Response(content=data, media_type="application/zip")

    @r.post("/provider/register")
    async def provider_register(request: Request, uid: str = Depends(current_user)):
        """登记外部服务方：{url, name, description, tags[], category, ap2Jwk}；须已向本平台市场 template.endorse。"""
        b = await body(request)
        row = await run(lambda: platform.register_external_provider(uid, url=b.get("url", ""), name=b.get("name", ""), description=b.get("description", ""),
                                                                    tags=b.get("tags") or [], category=b.get("category"), ap2_jwk=b.get("ap2Jwk") or b.get("ap2_jwk")))
        return {"id": row["id"], "name": row["name"], "status": row["status"], "url": jload(row["meta"])["url"]}

    @r.post("/provider/refresh")
    async def provider_refresh(request: Request, uid: str = Depends(current_user)):
        """公示模板之后调一次：平台拉模板进索引并把服务方置为在线（平台也会每 30 s 自动拉一次）。body {id}"""
        b = await body(request)
        row = db.one("SELECT owner_id FROM agents WHERE id=?", str(b.get("id") or ""))
        if not row or row["owner_id"] != uid:
            raise HTTPException(403, "不是你的服务方")
        return {"id": b["id"], "status": await run(lambda: platform.refresh_external_provider(str(b["id"])))}

    @r.get("/provider/mine")
    async def provider_mine(uid: str = Depends(current_user)):
        rows = db.rows("SELECT id, name, status, created, meta, definition FROM agents WHERE owner_id=? AND role='service' ORDER BY created", uid)
        out = []
        for x in rows:
            m, d = jload(x["meta"]), jload(x["definition"])
            out.append({"id": x["id"], "name": x["name"], "status": x["status"], "external": bool(m.get("external")), "url": m.get("url") or platform.provider_url(x["id"]),
                        "category": d.get("category"), "reputation": platform.market.reputation(m.get("url") or platform.provider_url(x["id"]))})
            out[-1]["reputation"].pop("signature", None)
        return out

    return r


__all__ = ["build_router"]
