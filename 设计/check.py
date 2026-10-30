# -*- coding: utf-8 -*-
"""
版本自检（九问IoA实验平台）。

- 设计/VERSION 是本仓版本唯一的一处，格式 vX.Y.Z；变更/README.md 变更表最上一行必须与它一致
- ioalab/ · connector/ · examples/ · deploy/ 各有一个 VERSION，记录它依据的本仓设计版本（存在的目录才查）
- 设计/README.md 记依据的 A2C 设计版本（一行「依据的 A2C 设计版本：vX.Y.Z」），必须与 vendor/wheels/SOURCE.json 一致
- README、docstring、设计文档里不得再散写「设计版本 vN」——版本只在 VERSION 文件里

    python 设计/check.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FMT = re.compile(r"^v\d+\.\d+\.\d+$")
STRAY = re.compile(r"设计版本 v\d|随设计 v\d|当前版本：v\d|平台版本 v\d")
A2C_LINE = re.compile(r"依据的 A2C 设计版本：(v\d+\.\d+\.\d+)")
ARTIFACTS = ("ioalab", "sdk", "official-services", "experiments", "connector", "examples", "deploy", "personal-assistant", "demo")


def read_version(path: Path) -> str | None:
    if not path.exists():
        return None
    v = path.read_text(encoding="utf-8").strip()
    return v if FMT.match(v) else f"格式错误「{v}」"


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v[1:].split("."))


def changelog_top() -> str | None:
    path = ROOT / "设计/变更/README.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if "## 变更表" not in text:
        return None
    sec = text.split("## 变更表", 1)[1]
    for line in sec.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if cells and FMT.match(cells[0]):
            return cells[0]
    return None


def stray_markers() -> list[str]:
    files = [ROOT / "README.md"]
    files += (ROOT / "ioalab").rglob("__init__.py")
    files += sorted(p for p in (ROOT / "设计").rglob("*.md") if "变更" not in p.relative_to(ROOT / "设计").parts)
    files += [p for p in (ROOT / "connector").rglob("*.md")] if (ROOT / "connector").exists() else []
    files += [p for p in (ROOT / "docs").rglob("*.md")] if (ROOT / "docs").exists() else []
    out = []
    for f in files:
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if STRAY.search(line):
                out.append(f"{f.relative_to(ROOT)}:{i} 散落的版本标记「{STRAY.search(line).group(0)}」，版本只写在 VERSION")
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bad: list[str] = []
    design = read_version(ROOT / "设计/VERSION")
    if design is None or not FMT.match(design):
        bad.append(f"设计/VERSION 缺失或{design}")
        design = None
    top = changelog_top()
    if design and top != design:
        bad.append(f"变更/README.md 变更表最上一行是 {top}，与 设计/VERSION 的 {design} 不一致")
    print(f"本仓版本 {design or '—'}　（变更表最上一行 {top or '—'}）")

    # 依据的 A2C 设计版本：设计/README.md ↔ vendor/wheels/SOURCE.json
    readme = (ROOT / "设计/README.md").read_text(encoding="utf-8")
    m = A2C_LINE.search(readme)
    src = json.loads((ROOT / "vendor/wheels/SOURCE.json").read_text(encoding="utf-8"))
    a2c_design = src["source"]["design_version"]
    if not m:
        bad.append("设计/README.md 缺少「依据的 A2C 设计版本：vX.Y.Z」")
    elif m.group(1) != a2c_design:
        bad.append(f"设计/README.md 记的 A2C 设计版本 {m.group(1)} 与 vendor/wheels/SOURCE.json 的 {a2c_design} 不一致")
    print(f"依据的 A2C 设计版本 {a2c_design}（a2c wheel {src['version']}，来源提交 {src['source']['commit'][:10]}）\n")

    print(f"{'产物':<14}{'依据':>8}  状态")
    for name in ARTIFACTS:
        d = ROOT / name
        if not d.exists():
            continue
        v = read_version(d / "VERSION")
        if v is None:
            bad.append(f"{name}/VERSION 缺失")
            status = "缺 VERSION"
        elif not FMT.match(v):
            bad.append(f"{name}/VERSION {v}")
            status = "格式错误"
        elif design and vtuple(v) < vtuple(design):
            status = f"落后（现行 {design}）"
        elif design and vtuple(v) > vtuple(design):
            bad.append(f"{name}/VERSION {v} 高于本仓版本 {design}")
            status = "超前？"
        else:
            status = "现行"
        print(f"{name:<14}{(v if v and FMT.match(v) else '—'):>8}  {status}")

    bad += stray_markers()
    print()
    if bad:
        print("✗ " + "\n✗ ".join(bad))
        return 1
    print("✓ 版本一致：VERSION 唯一、变更表对得上、产物各有依据、A2C 依据与 wheel 一致、无散落标记")
    return 0


if __name__ == "__main__":
    sys.exit(main())
