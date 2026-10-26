# -*- coding: utf-8 -*-
"""技能包脚本 connector/ioalab-client/skills/ioalab-delegate/scripts/ioalab.py 作 swarm 替身：只用标准库，经环境变量取账号，
走一单 need → search → templates → draft → sign → start → wait → deliveries；token 缓存与 401 重登；manifest 合法。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _support import PASSWORD, new_service, username

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "connector" / "ioalab-client" / "skills" / "ioalab-delegate" / "scripts" / "ioalab.py"


def _cli(env: dict, *args: str) -> dict | list:
    p = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert p.returncode == 0, (args, p.stdout, p.stderr)
    return json.loads(p.stdout)


def test_skill_script_round_trip(platform, client):
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf)
    name = username()
    assert client.post("/api/auth/register", json={"username": name, "password": PASSWORD}).status_code == 200
    home = tempfile.mkdtemp(prefix="ioalab-home-")
    env = {**os.environ, "IOALAB_BASE_URL": pf.s.base_url, "IOALAB_USERNAME": name, "IOALAB_PASSWORD": PASSWORD,
           "HOME": home, "USERPROFILE": home, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    me = _cli(env, "me")
    assert me["username"] == name and me["assistant"].startswith("asst-")
    assert list((Path(home) / ".ioalab").glob("*.token")), "token 已缓存"
    need = _cli(env, "need", "给小满美甲写三条朋友圈文案，主题夏日清凉", "--budget", "30", "--category", "文案")
    nid = need["id"]
    assert need["state"] == "open" and need["driver"] == "external"
    hits = _cli(env, "search", nid, "给美甲店写朋友圈文案", "--top", "50")
    assert any(h["id"] == svc_row["id"] for h in hits)
    [t] = _cli(env, "templates", provider)
    draft = _cli(env, "draft", nid, t["templateDigest"], "--spec", json.dumps({"posts": {"shop": "小满美甲", "theme": "夏日清凉"}}, ensure_ascii=False), "--card-id", "C-CLI")
    cd = draft["cardDigest"]
    assert _cli(env, "sign", nid, cd) == {"signed": True, "effective": True}
    assert _cli(env, "start", nid, cd) == {"started": True}
    w = _cli(env, "wait", nid, "--timeout", "120", "--interval", "1")
    assert w["state"] == "completed" and w["orders"][0]["state"] == "completed", w
    files = _cli(env, "deliveries", cd)["files"]
    assert "posts.json" in files
    assert _cli(env, "order", cd)["signatures"]["7"] == [True]
    # 坏 token → 401 → 自动重登
    tok = next((Path(home) / ".ioalab").glob("*.token"))
    tok.write_text("bad", encoding="utf-8")
    assert _cli(env, "me")["username"] == name
    # 无账号时报错而不是栈
    p = subprocess.run([sys.executable, str(SCRIPT), "me"], env={k: v for k, v in env.items() if not k.startswith("IOALAB_")},
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 2 and "未配置账号" in p.stdout


def test_connector_manifest_shape():
    base = ROOT / "connector" / "ioalab-client"
    m = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert m["package_type"] == "mcp" and m["id"] == base.name and m["integration"] == {"type": "skill-only"}
    assert m["credentials"] == {"type": "token", "file": "token-schema.json"} and (base / m["credentials"]["file"]).exists()
    assert (base / m["icon"]).exists() and m["icon"].endswith(".png")
    for sk in m["skills"]:
        assert (base / sk["dir"] / "SKILL.md").exists()
    schema = json.loads((base / "token-schema.json").read_text(encoding="utf-8"))
    assert [f["key"] for f in schema["fields"]] == ["IOALAB_BASE_URL", "IOALAB_USERNAME", "IOALAB_PASSWORD"]
    assert next(f for f in schema["fields"] if f["key"] == "IOALAB_PASSWORD")["type"] == "password"
    from test_boundary import FORBIDDEN
    text = (base / "skills" / "ioalab-delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: ioalab-delegate") and "@skills/connector-ioalab-client/scripts/ioalab.py" in text
    for word in FORBIDDEN:
        assert word not in text
