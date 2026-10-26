"""种子：8 个领域 × 6 个可接单的服务、内部账号、需求池、确定性分类树。部署时随平台自带（首次启动自动注入）。

  domains/<nn>-<slug>.json   领域文件：ISP 账号 · 分类树叶类 · 6 份服务定义（4 预批准 + 1 待批准 + 1 提案型）
  verifiers/common.py        声明式验收规则引擎；verifiers/<name>.py 脚本验收器
  needs/<slug>.json          需求池：每领域 3 条，材料在 materials/
  accounts.py                ISP_/ISC_ 内部账号
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import domains as _domains

HERE = Path(__file__).parent


def load_definitions() -> list[dict]:
    """全部服务定义（含 verifier.source · category · origin）。"""
    return _domains.definitions()


def load_domains() -> list[dict]:
    return _domains.load_domains()


def load_needs() -> list[dict]:
    """需求池（每条带 domain 与 service）。"""
    out = []
    for p in sorted((HERE / "needs").glob("*.json")):
        for n in json.loads(p.read_text(encoding="utf-8")):
            out.append({**n, "domain": p.stem})
    return out


def seed_all(platform, only: str | None = None) -> dict:
    """建齐内部账号（ISP_/ISC_）→ 服务归各领域的 ISP 账号（签模板 + A2X）→ 按领域建分类树 → 每个 ISC 账号一个平台驾驭的助手
    → 需求池材料就位、写 data/dispatch.json。幂等。"""
    from .accounts import ensure_accounts
    acc = ensure_accounts(platform)
    uid_of = {platform.db.one("SELECT username FROM users WHERE id=?", uid)["username"]: uid for uid in acc["isp"]}
    doms = load_domains()
    items = []
    for d in doms:
        owner = uid_of.get(d["account"]) or acc["isp"][0]
        items += [(owner, s) for s in d["services"]]
    platform.seed_services(items)
    members = {d["category"]["id"]: ["svc-" + s["id"] for s in d["services"]] for d in doms}
    platform.discovery.build_domains([d["category"] for d in doms], members)
    for uid in acc["isc"]:
        if not platform.db.one("SELECT 1 FROM agents WHERE owner_id=? AND role='assistant'", uid):
            platform.register_assistant(uid, "九问 Swarm")
    needs = load_needs()
    mat_dir = platform.s.data / "needs" / "pool"
    pool = []
    for i, n in enumerate(needs):
        uid = acc["isc"][i % len(acc["isc"])]
        a = platform.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'", uid)
        mats = []
        for m in n.get("materials", []):
            src = HERE / "materials" / m["file"]
            dst = mat_dir / n["domain"] / m["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copyfile(src, dst)
            mats.append({"name": m["name"], "path": str(dst), "declared": m.get("declared", {})})
        pool.append({"assistant_id": a["id"], "owner_id": uid, "domain": n["domain"], "service": n["service"], "text": n["text"],
                     "scope": n.get("scope", {}), "autoSign": n.get("autoSign", True), "materials": mats})
    (platform.s.data / "dispatch.json").write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"isp": len(acc["isp"]), "isc": len(acc["isc"]), "domains": len(doms), "services": len(items), "needs": len(pool),
            "taxonomy": platform.discovery.taxonomy_state()}


__all__ = ["load_definitions", "load_domains", "load_needs", "seed_all"]
