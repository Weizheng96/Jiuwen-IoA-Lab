# -*- coding: utf-8 -*-
"""协议边界：文案无禁用词；A2X 路由未挂；服务方端点只放行本机。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from _support import new_service

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("托管", "代收代付", "账户余额", "清算", "结算通道")
SUFFIXES = {".py", ".md", ".html", ".js", ".css", ".json", ".txt"}


def _sources() -> list[Path]:
    files = [ROOT / "README.md"]
    files += [p for p in (ROOT / "ioalab").rglob("*") if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts]
    return files


def test_no_forbidden_words():
    hits = []
    for p in _sources():
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            for w in FORBIDDEN:
                if w in line:
                    hits.append(f"{p.relative_to(ROOT)}:{i} 含「{w}」")
    assert not hits, "\n".join(hits)


def test_a2x_routes_not_mounted(platform):
    paths = [getattr(r, "path", "") for r in platform.app.routes]
    assert paths
    assert not any(p.startswith("/api/providers") or p.startswith("/api/build") for p in paths), paths
    assert "/a2a" in paths                                  # A2A 端点挂在 /a2a


async def _call(app, scope: dict) -> int:
    msgs: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        msgs.append(m)

    await app(scope, receive, send)
    return next(m["status"] for m in msgs if m["type"] == "http.response.start")


def _scope(path: str, host: str) -> dict:
    return {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/a2a" + path, "root_path": "/a2a",
            "raw_path": ("/a2a" + path).encode(), "query_string": b"", "headers": [(b"host", b"127.0.0.1")],
            "client": (host, 40000), "server": ("127.0.0.1", 80)}


def test_provider_endpoint_local_only(platform):
    pf = platform
    _u, row, provider, _d = new_service(pf)
    path = f"/providers/{row['id']}/.well-known/agent-card.json"
    assert f"providers/{row['id']}" in pf.router.local_only
    assert asyncio.run(_call(pf.router, _scope(path, "10.0.0.5"))) == 403
    assert asyncio.run(_call(pf.router, _scope(path, "192.168.1.9"))) == 403
    assert asyncio.run(_call(pf.router, _scope(path, "127.0.0.1"))) == 200
    # 市场与支付方对外可达；不存在的端点 404
    assert asyncio.run(_call(pf.router, _scope("/market/.well-known/agent-card.json", "10.0.0.5"))) == 200
    assert asyncio.run(_call(pf.router, _scope("/pay/.well-known/agent-card.json", "10.0.0.5"))) == 200
    assert asyncio.run(_call(pf.router, _scope("/nobody/.well-known/agent-card.json", "127.0.0.1"))) == 404
    # 经真实 HTTP（本机）可取到服务方 Agent Card
    import httpx
    r = httpx.get(provider + "/.well-known/agent-card.json", trust_env=False)
    assert r.status_code == 200 and r.json()["name"] == row["name"]
