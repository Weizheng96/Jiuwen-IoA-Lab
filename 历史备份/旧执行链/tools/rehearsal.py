# -*- coding: utf-8 -*-
"""试用排练：对着一台在跑的平台核对就绪项，可选预热几单、设背景流量。只用标准库。

    python tools/rehearsal.py --base http://10.0.0.2:8750 --password <管理员口令> [--warm 8] [--traffic low]

核对：/api/config（模型已配置）· 48 服务 · 分类树 available · 组件面板 · 需求池；--warm N 从需求池每领域轮流投 N 条并等终态（结清 / 裁定算通过）。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.request

TERMINAL = {"completed", "arbitrated", "failed", "lapsed", "aborted", "abandoned", "terminated"}


class Admin:
    def __init__(self, base: str, password: str) -> None:
        self.base = base.rstrip("/")
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.call("/api/admin/login", {"password": password})

    def call(self, path: str, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET", headers={"Content-Type": "application/json"})
        with self.op.open(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("IOALAB_BASE_URL", "http://127.0.0.1:8750"))
    ap.add_argument("--password", default=os.environ.get("IOALAB_ADMIN_PASSWORD", ""))
    ap.add_argument("--warm", type=int, default=0, help="预热投放几条（每领域轮流）")
    ap.add_argument("--traffic", choices=["off", "low", "normal"], help="排练结束后把背景流量设到这一档")
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args()
    fails = []

    def ok(label, cond, detail=""):
        print(("  ✓ " if cond else "  ✗ ") + label + (f"：{detail}" if detail and not cond else ""))
        if not cond:
            fails.append(label)

    cfg = json.loads(urllib.request.urlopen(a.base.rstrip("/") + "/api/config", timeout=10).read().decode("utf-8"))
    ok("平台可达", True)
    ok("模型已配置（IOALAB_LLM_API_KEY）", bool(cfg.get("llm")))
    ad = Admin(a.base, a.password)
    ok("管理员口令正确", True)
    reg = ad.call("/api/admin/registry")
    ok(f"服务注册表 {len(reg['services'])} 个（期望 ≥ 48）", len(reg["services"]) >= 48)
    ok(f"分类树 {reg['taxonomy']}", reg["taxonomy"] == "available")
    comps = ad.call("/api/admin/components")
    ok(f"内部账号：助手 {comps['assistants']['total']} · 钱包 {comps['payment']['wallets']}", comps["assistants"]["total"] >= 1)
    ok(f"对外地址 {comps['config']['public_url']}（swarm 机器须能访问）", True)
    pool = ad.call("/api/admin/seed-needs")
    ok(f"需求池 {len(pool)} 条", len(pool) >= 8)
    users = ad.call("/api/admin/users")
    ext = [u for u in users if u["kind"] == "external"]
    ok(f"外部账号 {len(ext)} 个（领导试用建议用专用外部账号）", True)

    if a.warm:
        by_dom: dict[str, list] = {}
        for n in pool:
            by_dom.setdefault(n.get("domain", ""), []).append(n)
        picked, i = [], 0
        while len(picked) < a.warm and any(by_dom.values()):
            dom = list(by_dom)[i % len(by_dom)]
            if by_dom[dom]:
                picked.append(by_dom[dom].pop(0))
            i += 1
        used, wave = set(), []
        for n in picked:                                   # 一个助手一次只能一条
            if n["assistant_id"] not in used:
                used.add(n["assistant_id"]); wave.append(n)
        t0 = time.time()
        r = ad.call("/api/admin/dispatch", {"gap": 1.5, "needs": [{"assistant_id": n["assistant_id"], "text": n["text"], "scope": n["scope"], "autoSign": True, "materials": n.get("materials", [])} for n in wave]})
        ids = r["needs"]
        print(f"  … 预热投放 {len(ids)} 条：{[n['service'] for n in wave]}")
        while time.time() - t0 < a.timeout:
            rows = {x["id"]: x for x in ad.call("/api/admin/needs")}
            if all(rows.get(nid, {}).get("state") in TERMINAL for nid in ids):
                break
            time.sleep(10)
        rows = {x["id"]: x for x in ad.call("/api/admin/needs")}
        for nid, n in zip(ids, wave):
            st = rows.get(nid, {}).get("state")
            ok(f"预热 {n['service']:<24} → {st}（{rows.get(nid, {}).get('llm_calls', 0)} 次调用）", st in ("completed", "arbitrated"))
        stats = ad.call("/api/admin/stats")
        print(f"  … 结清 {stats['completed']} · 裁定 {stats['arbitrated']} · 被拒 {stats['declined']} · 模型调用 {stats['llm_calls']}")
    if a.traffic:
        t = ad.call("/api/admin/traffic", {"level": a.traffic})
        ok(f"背景流量 {t['level']}", t["level"] == a.traffic)
    print(("✓ 排练就绪" if not fails else f"✗ {len(fails)} 项未过：" + "；".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
