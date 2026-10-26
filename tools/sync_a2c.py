# -*- coding: utf-8 -*-
"""从 A2C 主仓的 src/ 构建 a2c wheel 入库到 vendor/wheels/，写 SOURCE.json，并同步 requirements.txt 里的文件名。

    python tools/sync_a2c.py --src D:/project/AgentContractProtocol/src

主仓 src/ 用 hatchling 构建（需 `pip install build`）；wheel 里带验收器沙箱的 wasm 运行时归档（约 14 MB）。
SOURCE.json 记来源提交、依据的 A2C 设计版本（主仓 设计/VERSION）与 sha256；tools/selfcheck.py 核对装上的 a2c 版本与之一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEELS = ROOT / "vendor" / "wheels"


def sh(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="A2C 主仓的 src/ 目录（含 pyproject.toml 与 VERSION）")
    args = ap.parse_args()
    src = Path(args.src).resolve()
    assert (src / "pyproject.toml").exists() and (src / "VERSION").exists(), f"不是 a2c 的 src/：{src}"
    design_version = (src.parent / "设计" / "VERSION").read_text(encoding="utf-8").strip()
    commit = sh(["git", "rev-parse", "HEAD"], src)
    dirty = sh(["git", "status", "--porcelain", "--", "."], src)
    if dirty:
        print("⚠ src/ 有未提交改动，SOURCE.json 记的提交不含它们：\n" + dirty)
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", td], cwd=str(src), check=True)
        [built] = list(Path(td).glob("a2c-*.whl"))
        WHEELS.mkdir(parents=True, exist_ok=True)
        for old in WHEELS.glob("a2c-*.whl"):
            old.unlink()
        dst = WHEELS / built.name
        shutil.copyfile(built, dst)
    version = re.match(r"a2c-([^-]+)-", dst.name).group(1)
    info = {"package": "a2c", "version": version, "wheel": dst.name, "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
            "source": {"repo": "https://github.com/Weizheng96/AgentContractProtocol", "commit": commit, "subdirectory": "src",
                       "design_version": design_version},
            "built": time.strftime("%Y-%m-%d"), "tool": "tools/sync_a2c.py"}
    (WHEELS / "SOURCE.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    req = ROOT / "requirements.txt"
    s = req.read_text(encoding="utf-8")
    s2, n = re.subn(r"^\./vendor/wheels/a2c-[^\s]+\.whl$", f"./vendor/wheels/{dst.name}", s, flags=re.M)
    assert n == 1, "requirements.txt 里应恰有一行 ./vendor/wheels/a2c-*.whl"
    req.write_text(s2, encoding="utf-8")
    print(f"✓ {dst.relative_to(ROOT)} · a2c {version} · 来源 {commit[:10]} · 设计 {design_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
