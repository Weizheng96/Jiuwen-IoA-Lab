# -*- coding: utf-8 -*-
"""种子：领域文件与声明式验收引擎、确定性分类树、seed_all 幂等（内部账号 · 48 服务归 ISP · 分类树 available · 需求池 · 向量退路能检索到）。"""
from __future__ import annotations

import json

from ioalab.seed import load_definitions, load_domains, load_needs
from ioalab.seed.domains import common_source


class _Ctx:
    def __init__(self, files: dict[str, bytes], values: dict):
        self.files, self.values = files, values

    def read(self, name: str) -> bytes:
        return self.files[name]


def _engine(checks: list[dict], file: str = "d.json"):
    src = common_source().replace("__RULES__", json.dumps({"file": file, "checks": checks}, ensure_ascii=False))
    ns: dict = {}
    exec(compile(src, "common.py", "exec"), ns)        # 宿主里只为测引擎本身；平台运行时只在 wasm 沙箱里跑它
    return ns["verify"]


def _run(checks, data, values=None):
    return _engine(checks)(_Ctx({"d.json": json.dumps(data, ensure_ascii=False).encode()}, values or {}))


def test_rules_engine_core_checks():
    data = {"posts": ["茶小满今天上新", "来茶小满喝一杯", "茶小满周末八折"], "days": [{"day": 1, "cost": 300}, {"day": 2, "cost": 500}], "title": "x" * 10,
            "colors": ["#1F3A5F", "#FFFFFF"], "level": "中"}
    vals = {"spec.d.count": 3, "spec.d.shop": "茶小满", "spec.d.budget": 1000, "spec.d.avoid": "花生, 虾"}
    r = _run([{"kind": "list_len", "path": "posts", "eq": "spec.d.count"}, {"kind": "each_contains", "path": "posts[]", "value": "spec.d.shop"},
              {"kind": "each_str_len", "path": "posts[]", "max": 60}, {"kind": "keys", "path": "days[]", "required": ["day", "cost"]},
              {"kind": "sum_le", "path": "days[].cost", "max": "spec.d.budget"}, {"kind": "each_num", "path": "days[].cost", "min": 0},
              {"kind": "each_regex", "path": "colors[]", "pattern": "#[0-9A-Fa-f]{6}"}, {"kind": "unique", "path": "colors[]"},
              {"kind": "str_len", "path": "title", "min": 5, "max": 20}, {"kind": "each_in", "path": "level", "in": ["低", "中", "高"]},
              {"kind": "forbid", "path": "", "words": "spec.d.avoid"}, {"kind": "contains_all", "path": "posts", "words": ["上新", "八折"]},
              {"kind": "cjk_ratio", "path": "posts[]", "min": 0.5}], data, vals)
    assert r["status"] == "pass", r
    bad = _run([{"kind": "list_len", "path": "posts", "eq": 5}, {"kind": "forbid", "path": "", "words": ["八折"]}, {"kind": "keys", "path": "nope", "required": ["x"]}], data, vals)
    assert bad["status"] == "fail" and [c["ok"] for c in bad["checks"]] == [False, False, False]
    assert _engine([])(_Ctx({"d.json": b"not json"}, {}))["status"] == "fail"


def test_domain_files_shape():
    doms = load_domains()
    assert len(doms) == 8 and all(len(d["services"]) == 6 for d in doms)
    assert [d["account"] for d in doms] == [f"ISP_{i:03d}" for i in range(1, 9)]
    for d in doms:
        forms = [s["formation"] for s in d["services"]]
        assert forms.count("countersign") >= 1 and forms.count("proposal") >= 1, d["slug"]
        for s in d["services"]:
            assert s["category"] == d["category"]["id"] and "source" in s["verifier"] and s["verifier"]["reads"] is not None
            assert "sample" in s and s.get("runnable") is True
            if s["formation"] in ("countersign", "proposal"):
                assert s["capacity"] == 1
    defs = load_definitions()
    assert len(defs) == 48 and len({s["id"] for s in defs}) == 48
    needs = load_needs()
    assert len(needs) == 24 and {n["service"] for n in needs} <= {s["id"] for s in defs}


def test_seed_all_idempotent_and_taxonomy(platform):
    from ioalab.market import taxonomy
    pf = platform
    out = pf.ensure_seeded(force=True)
    assert out["isp"] == 8 and out["isc"] == pf.s.isc_count and out["services"] == 48 and out["needs"] == 24
    assert out["taxonomy"] == "available", out
    assert pf.ensure_seeded() is None, "已注入则跳过"
    assert pf.db.one("SELECT COUNT(*) AS n FROM agents WHERE role='service' AND owner_id IN (SELECT id FROM users WHERE kind='isp')")["n"] == 48
    # 服务归各领域的 ISP 账号
    owner = pf.db.one("SELECT u.username FROM agents a JOIN users u ON u.id=a.owner_id WHERE a.id='svc-wechat-posts'")["username"]
    assert owner == "ISP_004"
    # 树：8 个领域叶类 + 其他，每服务恰在一类
    cats = pf.discovery.categories()
    by = {c["id"]: c for c in cats}
    assert len([c for c in cats if c["services"]]) == 8 and "svc-wechat-posts" in by["cat_writing"]["services"]
    from ioalab.market.discovery import DATASET
    cls = json.loads((pf.discovery.svc.taxonomy_dir(DATASET) / "class.json").read_text(encoding="utf-8"))["categories"]
    assert set(cls) >= set(by) | {"root"} and all(cls[c]["decision_rule"] for c in by if c != taxonomy.MISC["id"])
    # 每个 ISC 一个助手 · 需求池就位
    assert pf.db.one("SELECT COUNT(*) AS n FROM agents WHERE role='assistant' AND owner_id IN (SELECT id FROM users WHERE kind='isc')")["n"] == pf.s.isc_count
    pool = json.loads((pf.s.data / "dispatch.json").read_text(encoding="utf-8"))
    assert len(pool) == 24 and all(p["assistant_id"].startswith("asst-") for p in pool)
    # 向量退路（桩模式字符二元组）能把对的服务排进前 8
    hits = pf._search("帮我的奶茶店写三条朋友圈文案", 8)
    assert "svc-wechat-posts" in [h["id"] for h in hits]
    # 运行期注册带领域：确定性归入叶类，树仍 available
    d = dict(next(s for s in load_definitions() if s["id"] == "headlines"))
    d["id"], d["name"] = "headlines-ext", d["name"] + "·外"
    row = pf.register_service(pf.db.one("SELECT id FROM users WHERE username='ISP_004'")["id"], d)
    assert row["id"] in {c["id"]: c for c in pf.discovery.categories()}["cat_writing"]["services"]
    assert pf.discovery.taxonomy_state() == "available"
