"""种子：100 份服务定义（12 可运行 + 88 牌子）、10 条需求、A2X 分类树。

  services/*.json    可运行的服务定义（验收器源码在 verifier.source）
  placeholders.py    90 块牌子的生成规则（15 个领域 × 6 家）；其中 ALTERNATES 两块改成可运行（撞单被拒后的换家对象）
  needs.json         10 条需求（两对撞同一待批准型服务）；材料在 materials/
  taxonomy/          离线建好的 A2X 分类树（taxonomy.json · class.json · build_config.json）
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


def load_definitions() -> list[dict]:
    out = []
    for p in sorted((HERE / "services").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        src = HERE / "services" / (p.stem + ".py")
        if "verifier" in d and "source" not in d["verifier"] and src.exists():
            d["verifier"]["source"] = src.read_text(encoding="utf-8")
        out.append(d)
    try:
        from .placeholders import placeholders
        out.extend(placeholders())
    except ImportError:
        pass
    base = {d["id"]: d for d in out}
    for pid, bid in ALTERNATES.items():           # 两块牌子改成可运行的替代服务：名字、描述、标签不变（分类树不用重建），其余随底本
        p, b = base[pid], base[bid]
        keep = {k: p[k] for k in ("id", "name", "description", "tags") + (("pricing",) if b.get("formation") != "proposal" else ()) if k in p}
        p.clear()
        p.update({k: v for k, v in b.items() if k not in keep})
        p.update(keep)
        p["runnable"] = True
        p.pop("timeout_reject", None)
    return out


# 牌子 → 以哪个可运行服务为底（同一分类树叶类、检索能命中的替代者，供撞单被拒后换家）：
# 简历一页精炼室 ← 简历润色工作室（待批准型）· 周末城市漫游手册社 ← 两日行程定制所（提案型，拟卡时退回）
ALTERNATES = {"resume-01": "resume-polish", "trip-plan-01": "trip-plan"}


def load_needs() -> list[dict]:
    p = HERE / "needs.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def seed_all(platform, only: str | None = None) -> dict:
    """创建虚拟账号、注册 100 个服务（签模板 + A2X）、10 个助手（各带需求的材料）。幂等。"""
    defs = load_definitions()
    items = []
    for d in defs:
        phone = f"1380000{abs(hash(d['id'])) % 10000:04d}"
        u = platform.db.one("SELECT id FROM users WHERE phone=?", phone) or platform.register_user(phone, is_virtual=True, note=f"服务方 · {d['name']}")
        items.append((u["id"], d))
    platform.seed_services(items)
    needs = load_needs()
    assistants = []
    mat_dir = platform.s.data / "needs"
    for i, n in enumerate(needs):
        phone = f"1370000{i + 1:04d}"
        u = platform.db.one("SELECT id FROM users WHERE phone=?", phone) or platform.register_user(phone, is_virtual=True, note=f"虚拟用户 · {n['owner']}")
        a = platform.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'", u["id"]) or platform.register_assistant(u["id"], n.get("assistant", "小艺"))
        mats = []
        for m in n.get("materials", []):
            src = HERE / "materials" / m["file"]
            dst = mat_dir / f"{i + 1:02d}" / m["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copyfile(src, dst)
            mats.append({"name": m["name"], "path": str(dst), "declared": m.get("declared", {})})
        assistants.append({"assistant_id": a["id"], "owner_id": u["id"], "text": n["text"], "scope": n.get("scope", {}),
                           "autoSign": n.get("autoSign", True), "materials": mats, "service": n.get("service")})
    (platform.s.data / "dispatch.json").write_text(json.dumps(assistants, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"services": len(items), "assistants": len(assistants), "taxonomy": platform.discovery.taxonomy_state()}
