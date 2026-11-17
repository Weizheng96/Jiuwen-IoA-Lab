"""守卫：SDK 不 eval、不起子进程；运行时归档与清单一致；服务方回调适配。"""
import ast
import hashlib
import json
from pathlib import Path

from a2c.card import card_digest
from a2c.integrations.openjiuwen import CallbackPolicy

from .support import World

PKG = Path(__import__("a2c").__file__).resolve().parent


def test_no_eval_exec_or_subprocess():
    """服务方给的公式与验收器绝不 eval，也不在宿主进程里跑。"""
    banned_calls = {"eval", "exec", "compile", "__import__"}
    for py in PKG.rglob("*.py"):
        if "guest" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned_calls, f"{py}: {node.func.id}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(n.split(".")[0] in ("subprocess", "pickle") for n in names), f"{py}: {names}"


def test_runtime_archive_is_pinned():
    spec = json.loads((PKG / "verifier" / "runtime" / "runtimes.json").read_text())["cpython-3.13-wasi"]
    data = (PKG / "verifier" / "runtime" / spec["archive"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == spec["archiveSha256"]


def test_callback_policy_hands_work_to_the_agent():
    """openJiuwen 智能体经回调接活：开工时拿到卡与材料。"""
    started = []
    w = World(policy=CallbackPolicy(on_start=lambda t, c, m: started.append((card_digest(c), sorted(m)))))
    card = w.formed_card("countersign")
    w.client.start(card)
    assert started == [(card_digest(card), ["license", "novel"])]
