# -*- coding: utf-8 -*-
"""
九问IoA仿真平台自检（不调模型，几十秒内跑完）：

  ① 依赖：openjiuwen == 0.1.19.post2 · a2c 版本 = vendor/wheels/SOURCE.json · a2x_registry 来自 vendor/A2X-registry
  ② 版本：设计/VERSION 形如 vX.Y.Z；设计/check.py 通过
  ③ 不入库：git ls-files data logs 为空，.gitignore 含 data/ 与 logs/
  ④ 禁用词：ioalab/ · README.md · 设计/ · connector/ · docs/ · deploy/ 零命中
  ⑤ 旧名零命中：源码与文档里不再出现 agentnet
  ⑥ tools/check_services.py：每份种子服务定义能生成合法模板、sample 过验收器
  ⑦ python -m ioalab smoke：固定交付件桩跑通一单（exit 0）
  ⑧ 汇总；任一项未过 exit 1

    python tools/selfcheck.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # 仓库根
FORBIDDEN = ("托管", "代收代付", "账户余额", "清算", "结算通道", "按裁定强制划转", "资金沉淀")
SCAN_SUFFIXES = {".py", ".md", ".html", ".js", ".css", ".json", ".txt", ".sh", ".service", ".conf", ".example", ".toml"}
SCAN_DIRS = ("ioalab", "设计", "connector", "docs", "deploy", "examples", "tools", "tests")
SKIP_PARTS = {"__pycache__", ".git", ".venv", "data", "logs", "vendor", "node_modules"}
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "IOALAB_STUB": "1"}


class Checker:
    def __init__(self) -> None:
        self.n = 0
        self.fails: list[str] = []

    def ok(self, label: str, cond: bool, detail: str = "") -> None:
        self.n += 1
        print(("  ✓ " if cond else "  ✗ ") + label + (("：" + detail) if detail and not cond else ""))
        if not cond:
            self.fails.append(label)

    def eq(self, label: str, got, want) -> None:
        self.ok(label, got == want, f"实际 {got!r} ≠ 期望 {want!r}")

    def report(self) -> int:
        print(f"{'✓ 全部' if not self.fails else '✗'} {self.n} 项" + (f"，{len(self.fails)} 项未过：" if self.fails else ""))
        for f in self.fails:
            print("    " + f)
        return 1 if self.fails else 0


def run(args: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run(args, cwd=str(cwd), env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def scan_files() -> list[Path]:
    out = [ROOT / "README.md"]
    for d in SCAN_DIRS:
        base = ROOT / d
        if base.exists():
            out += [p for p in base.rglob("*") if p.is_file() and p.suffix in SCAN_SUFFIXES and not (set(p.parts) & SKIP_PARTS)]
    # 扫描器自己的词表与历史记录（设计/变更/）不算命中
    return [p for p in out if p.exists() and p.name not in ("selfcheck.py", "test_boundary.py") and "变更" not in p.parts]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = Checker()
    t0 = time.time()

    print("① 依赖")
    try:
        import importlib.metadata as md
        c.eq("openjiuwen == 0.1.19.post2", md.version("openjiuwen"), "0.1.19.post2")
    except Exception as e:  # noqa: BLE001
        c.ok("openjiuwen 已安装", False, str(e))
    src = json.loads((ROOT / "vendor" / "wheels" / "SOURCE.json").read_text(encoding="utf-8"))
    try:
        import importlib.metadata as md
        c.eq(f"a2c 版本 = SOURCE.json（{src['version']}）", md.version("a2c"), src["version"])
    except Exception as e:  # noqa: BLE001
        c.ok("a2c 可导入", False, str(e))
    c.ok("vendor/wheels 里有 SOURCE.json 指向的 wheel", (ROOT / "vendor" / "wheels" / src["wheel"]).exists(), src["wheel"])
    try:
        import a2x_registry
        c.ok("a2x_registry 来自 vendor/A2X-registry", Path(a2x_registry.__file__).resolve().is_relative_to(ROOT / "vendor" / "A2X-registry"),
             a2x_registry.__file__)
    except Exception as e:  # noqa: BLE001
        c.ok("a2x_registry 可导入", False, str(e))

    print("② 版本")
    vp = ROOT / "设计" / "VERSION"
    v = vp.read_text(encoding="utf-8").strip() if vp.exists() else ""
    c.ok("设计/VERSION 形如 vX.Y.Z", bool(re.fullmatch(r"v\d+\.\d+\.\d+", v)), repr(v))
    if (ROOT / "设计" / "check.py").exists():
        code, out = run([sys.executable, str(ROOT / "设计" / "check.py")], ROOT, timeout=60)
        c.ok("设计/check.py 通过", code == 0, out.strip()[-400:])
    else:
        c.ok("设计/check.py 存在", False)

    print("③ 运行期数据不入库")
    code, out = run(["git", "ls-files", "data", "logs"], ROOT, timeout=60)
    c.ok("git ls-files data logs 为空", code == 0 and not out.strip(), out.strip()[:200])
    gi = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    c.ok(".gitignore 含 data/ 与 logs/", "data/" in gi and "logs/" in gi)

    print("④ 禁用词")
    files = scan_files()
    hits = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            hits += [f"{p.relative_to(ROOT)}:{i}「{w}」" for w in FORBIDDEN if w in line]
    c.ok(f"源码与文档零命中（扫描 {len(files)} 个文件）", not hits, "；".join(hits[:8]))

    print("⑤ 旧名")
    old = [str(p.relative_to(ROOT)) for p in files if "agentnet" in p.read_text(encoding="utf-8", errors="replace")]
    c.ok("不再出现 agentnet", not old, "；".join(old[:8]))

    print("⑥ 种子服务定义")
    code, out = run([sys.executable, str(ROOT / "tools" / "check_services.py")], ROOT)
    tail = [l for l in out.strip().splitlines() if l.strip()]
    summary = next((l for l in reversed(tail) if "通过" in l), "")
    c.ok(f"check_services.py 全过（{summary}）", code == 0 and "✗" not in out, "\n".join(tail[-12:]))

    print("⑦ 冒烟（固定交付件桩跑通一单）")
    code, out = run([sys.executable, "-m", "ioalab", "smoke"], ROOT, timeout=600)
    lines = [l for l in out.splitlines() if l.startswith("  ✓") or l.startswith("  ✗") or l.startswith("✓") or l.startswith("✗")]
    bad = [l for l in lines if l.startswith("  ✗")]
    c.ok(f"python -m ioalab smoke exit 0（{sum(1 for l in lines if l.startswith('  ✓'))} 项）", code == 0 and not bad,
         "\n".join(bad or out.splitlines()[-12:]))

    print(f"⑧ 汇总（{time.time() - t0:.1f} s）")
    return c.report()


if __name__ == "__main__":
    sys.exit(main())
