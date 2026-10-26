#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""九问IoA仿真平台 · 委托方命令行（只用标准库；输出 JSON）。

账号：环境变量 IOALAB_BASE_URL · IOALAB_USERNAME · IOALAB_PASSWORD，或脚本旁 config.json {"base_url", "username", "password"}。
token 缓存在 ~/.ioalab/<摘要>.token；401 时自动重新登录一次。

    ioalab.py me
    ioalab.py need "<一句话需求>" [--budget 30] [--category 文案] [--sign1 client|user] [--sign7 client|user] [--auto]
    ioalab.py upload <nid> <文件> [--name source] [--declared '{"chars": 407}']
    ioalab.py materials <nid> · search <nid> "<描述>" [--top 8] · reputation <provider> · templates <provider>
    ioalab.py draft <nid> <templateDigest> --spec '{"posts": {...}}' [--materials a,b] [--card-id C-001]
    ioalab.py sign <nid> <cardDigest> · request <nid> <templateDigest> --spec '{...}' [--request-id R-001] · adopt <nid> <cardDigest> · start <nid> <cardDigest>
    ioalab.py status <nid> · events <nid> [--after 0] · wait <nid> [--timeout 600] · abandon <nid>
    ioalab.py confirmations · answer <cid> yes|no
    ioalab.py order <cardDigest> · deliveries <cardDigest> [--period 1] · pay <cardDigest> --period 1 · arbitrate <cardDigest> [--period 1] [--ground …] · terminate <cardDigest> [--unilateral]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMINAL = {"completed", "arbitrated", "failed", "abandoned", "lapsed", "aborted", "terminated"}


def _conf() -> dict:
    env = {k: os.environ.get(v, "").strip() for k, v in (("base_url", "IOALAB_BASE_URL"), ("username", "IOALAB_USERNAME"), ("password", "IOALAB_PASSWORD"))}
    if all(env.values()):
        return env
    p = HERE / "config.json"
    if p.exists():
        c = json.loads(p.read_text(encoding="utf-8"))
        return {**{k: (c.get(k) or "").strip() for k in ("base_url", "username", "password")}, **{k: v for k, v in env.items() if v}}
    _die("未配置账号：设 IOALAB_BASE_URL / IOALAB_USERNAME / IOALAB_PASSWORD，或在脚本旁放 config.json")


def _die(msg: str, code: int = 2) -> None:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(code)


class Api:
    def __init__(self) -> None:
        c = _conf()
        self.base = c["base_url"].rstrip("/")
        self.user, self.pw = c["username"], c["password"]
        self.cache = Path.home() / ".ioalab" / (hashlib.sha1(f"{self.base}|{self.user}".encode()).hexdigest()[:16] + ".token")
        self.token = self.cache.read_text(encoding="utf-8").strip() if self.cache.exists() else ""

    def login(self) -> dict:
        out = self._req("POST", "/api/v1/token", {"username": self.user, "password": self.pw}, auth=False)
        self.token = out["token"]
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(self.token, encoding="utf-8")
        try:
            os.chmod(self.cache, 0o600)
        except OSError:
            pass
        return out

    def call(self, method: str, path: str, body=None, *, files=None, form=None):
        if not self.token:
            self.login()
        try:
            return self._req(method, path, body, files=files, form=form)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.login()
                return self._req(method, path, body, files=files, form=form)
            raise

    def _req(self, method: str, path: str, body=None, *, auth=True, files=None, form=None):
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if files:
            boundary = "----ioalab" + uuid.uuid4().hex
            parts = []
            for k, v in (form or {}).items():
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
            for k, (fname, content) in files.items():
                ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())
            data = b"".join(parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None or method == "POST":
            data = json.dumps(body or {}, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise
            try:
                detail = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                detail = {"error": f"HTTP {e.code}"}
            msg = detail.get("error") or detail.get("detail") or detail
            _die(f"{method} {path} → HTTP {e.code}：{msg}", 1)
        except urllib.error.URLError as e:
            _die(f"连不上平台 {self.base}：{e.reason}", 1)
        return json.loads(raw.decode("utf-8")) if raw else {}


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ioalab.py", description="九问IoA仿真平台 · 委托方命令行")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("me")
    p = sub.add_parser("need"); p.add_argument("text"); p.add_argument("--budget", type=float, default=30); p.add_argument("--category", default="不限")
    p.add_argument("--sign1", default="client", choices=["client", "user"]); p.add_argument("--sign7", default="client", choices=["client", "user"])
    p.add_argument("--auto", action="store_true", help="平台侧确认全自动（默认关：验收亲签等会进待确认，由你答复）")
    p = sub.add_parser("upload"); p.add_argument("nid"); p.add_argument("file"); p.add_argument("--name", default=""); p.add_argument("--declared", default="{}")
    p = sub.add_parser("materials"); p.add_argument("nid")
    p = sub.add_parser("search"); p.add_argument("nid"); p.add_argument("query"); p.add_argument("--top", type=int, default=8)
    p = sub.add_parser("reputation"); p.add_argument("provider")
    p = sub.add_parser("templates"); p.add_argument("provider")
    p = sub.add_parser("draft"); p.add_argument("nid"); p.add_argument("template"); p.add_argument("--spec", required=True); p.add_argument("--materials", default=""); p.add_argument("--card-id", default="")
    p = sub.add_parser("sign"); p.add_argument("nid"); p.add_argument("card")
    p = sub.add_parser("request"); p.add_argument("nid"); p.add_argument("template"); p.add_argument("--spec", required=True); p.add_argument("--materials", default=""); p.add_argument("--request-id", default="")
    p = sub.add_parser("adopt"); p.add_argument("nid"); p.add_argument("card")
    p = sub.add_parser("start"); p.add_argument("nid"); p.add_argument("card")
    p = sub.add_parser("status"); p.add_argument("nid")
    p = sub.add_parser("events"); p.add_argument("nid"); p.add_argument("--after", type=int, default=0)
    p = sub.add_parser("wait"); p.add_argument("nid"); p.add_argument("--timeout", type=float, default=600); p.add_argument("--interval", type=float, default=5)
    p = sub.add_parser("abandon"); p.add_argument("nid")
    sub.add_parser("confirmations")
    p = sub.add_parser("answer"); p.add_argument("cid"); p.add_argument("decision", choices=["yes", "no"])
    p = sub.add_parser("order"); p.add_argument("card")
    p = sub.add_parser("deliveries"); p.add_argument("card"); p.add_argument("--period", type=int, default=1)
    p = sub.add_parser("pay"); p.add_argument("card"); p.add_argument("--period", type=int, required=True)
    p = sub.add_parser("arbitrate"); p.add_argument("card"); p.add_argument("--period", type=int, default=1); p.add_argument("--ground", default="acceptance-failed")
    p = sub.add_parser("terminate"); p.add_argument("card"); p.add_argument("--unilateral", action="store_true")
    a = ap.parse_args(argv)
    api = Api()

    def mats(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    if a.cmd == "me":
        out(api.call("GET", "/api/v1/me"))
    elif a.cmd == "need":
        out(api.call("POST", "/api/v1/delegations", {"text": a.text, "scope": {"budget": a.budget, "category": a.category, "sign1": a.sign1, "sign7": a.sign7},
                                                     "autoSign": a.auto}))
    elif a.cmd == "upload":
        fp = Path(a.file)
        if not fp.exists():
            _die(f"文件不存在：{fp}")
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/materials", files={"file": (fp.name, fp.read_bytes())},
                     form={"name": a.name or fp.stem, "declared": a.declared}))
    elif a.cmd == "materials":
        out(api.call("GET", f"/api/v1/delegations/{a.nid}/materials"))
    elif a.cmd == "search":
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/search", {"query": a.query, "top_k": a.top}))
    elif a.cmd == "reputation":
        out(api.call("GET", "/api/v1/providers/reputation?" + urllib.parse.urlencode({"provider": a.provider})))
    elif a.cmd == "templates":
        out(api.call("GET", "/api/v1/providers/templates?" + urllib.parse.urlencode({"provider": a.provider})))
    elif a.cmd == "draft":
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/cards", {"templateDigest": a.template, "cardId": a.card_id or None, "spec": json.loads(a.spec), "materials": mats(a.materials)}))
    elif a.cmd == "sign":
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/cards/{a.card}/sign"))
    elif a.cmd == "request":
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/requests", {"templateDigest": a.template, "requestId": a.request_id or None, "spec": json.loads(a.spec), "materials": mats(a.materials)}))
    elif a.cmd == "adopt":
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/cards/{a.card}/adopt"))
    elif a.cmd == "start":
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/cards/{a.card}/start"))
    elif a.cmd == "status":
        out(api.call("GET", f"/api/v1/delegations/{a.nid}"))
    elif a.cmd == "events":
        out(api.call("GET", f"/api/v1/delegations/{a.nid}/events?after={a.after}"))
    elif a.cmd == "wait":
        deadline = time.time() + a.timeout
        while True:
            d = api.call("GET", f"/api/v1/delegations/{a.nid}")
            if d.get("pending") or d["state"] in TERMINAL or time.time() > deadline:
                out({"state": d["state"], "card_digest": d.get("card_digest"), "pending": d.get("pending", []),
                     "orders": [{k: o.get(k) for k in ("card_digest", "provider_name", "state", "phase", "total")} for o in d.get("orders", [])],
                     "timeout": time.time() > deadline and d["state"] not in TERMINAL})
                break
            time.sleep(a.interval)
    elif a.cmd == "abandon":
        out(api.call("POST", f"/api/v1/delegations/{a.nid}/abandon"))
    elif a.cmd == "confirmations":
        out(api.call("GET", "/api/v1/confirmations"))
    elif a.cmd == "answer":
        out(api.call("POST", f"/api/v1/confirmations/{a.cid}", {"granted": a.decision == "yes"}))
    elif a.cmd == "order":
        out(api.call("GET", f"/api/v1/orders/{a.card}"))
    elif a.cmd == "deliveries":
        out(api.call("GET", f"/api/v1/orders/{a.card}/deliveries/{a.period}"))
    elif a.cmd == "pay":
        out(api.call("POST", f"/api/v1/orders/{a.card}/pay/{a.period}"))
    elif a.cmd == "arbitrate":
        out(api.call("POST", f"/api/v1/orders/{a.card}/arbitrate", {"period": a.period, "ground": a.ground}))
    elif a.cmd == "terminate":
        out(api.call("POST", f"/api/v1/orders/{a.card}/terminate", {"agree": not a.unilateral}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
