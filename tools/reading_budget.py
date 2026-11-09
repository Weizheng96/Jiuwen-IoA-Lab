"""Measure the reading budget of "change one module": characters an agent must read before editing.

The reading set follows the module-folder format's budget table: the fixed entry chain, the architecture
module table, the module's own pages and the interface pages of the modules it declares as dependencies.
``required`` counts parent pages only; ``ceiling`` adds every interface sub-page. Modules not yet migrated
into a folder are measured through the pages the architecture table links, without dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import check_modules as modules

ENTRY = ("AGENTS.md", ".agents/skills/develop/SKILL.md", ".agents/skills/develop/references/routing.md")
CODE_SUFFIXES = {".py", ".js", ".html", ".css", ".sh"}


def size(path: Path) -> int:
    return len(path.read_text(encoding="utf-8")) if path.is_file() else 0


def architecture_section() -> int:
    """Module table and dependency graph only: the part every module task reads."""
    path = modules.ROOT / modules.DESIGN / "架构.md"
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8")
    if "## 模块表" not in text:
        return len(text)
    return len("## 模块表" + text.split("## 模块表", 1)[1].split("\n## ", 1)[0])


def architecture_links() -> dict[str, list[Path]]:
    """Module name -> pages the architecture table links in its 文件夹 column."""
    path = modules.ROOT / modules.DESIGN / "架构.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    section = text.split("## 模块表", 1)[-1].split("\n## ", 1)[0]
    found = {}
    for cells in modules.table_rows(section):
        if len(cells) < 5:
            continue
        name = modules.plain(cells[1]) if modules.plain(cells[1]) not in ("—", "-", "") else modules.plain(cells[0])
        targets = [target.split("#")[0] for _, target in modules.LINK.findall(cells[4])]
        found[name] = [(path.parent / target).resolve() for target in targets if target]
    return found


def own_pages(folder: Path) -> tuple[list[Path], list[Path]]:
    """(parent pages, interface sub-pages) of a module folder; terms and decisions may sit at group level."""
    group = folder.parent if folder.parent.name != "组件" else folder
    pages = [folder / name for name in ("接口.md", "依赖.md", "实现.md")]
    pages += [p for p in (folder / "术语.md", group / "术语.md") if p.is_file()][:1]
    for decisions in {folder / "决策", group / "决策"}:
        pages += sorted(decisions.glob("*.md")) if decisions.is_dir() else []
    return pages, sorted((folder / "接口").glob("*.md"))


def code_size(name: str, table) -> int:
    """Characters of code files the module owns; a nested module's files count only for the nested one."""
    total = 0
    for item in modules.MODULES.get(name, ()):
        path = modules.ROOT / item
        files = [path] if path.is_file() else path.rglob("*") if path.is_dir() else []
        total += sum(size(p) for p in files if p.suffix in CODE_SUFFIXES and "__pycache__" not in p.parts
                     and modules.module_of(p, table) == name)
    return total


def measure() -> dict:
    folders, links, table = modules.module_folders(), architecture_links(), modules.owners()
    fixed = sum(size(modules.ROOT / path) for path in ENTRY) + architecture_section()
    rows = []
    for name in modules.MODULES:
        folder = folders.get(name)
        if folder is None:
            pages = list(dict.fromkeys(links.get(name, [])))
            required = ceiling = sum(size(p) for p in pages)
            rows.append({"module": name, "migrated": False, "required": fixed + required,
                         "ceiling": fixed + ceiling, "pages": len(pages), "code": code_size(name, table)})
            continue
        parents, subpages = own_pages(folder)
        dependencies = sorted(modules.declared_dependencies(folder) or ())
        for other in dependencies:
            if other in folders:
                parents.append(folders[other] / "接口.md")
                subpages += sorted((folders[other] / "接口").glob("*.md"))
            else:
                parents += links.get(other, [])
        parents = list(dict.fromkeys(parents))
        required = sum(size(p) for p in parents)
        rows.append({"module": name, "migrated": True, "required": fixed + required,
                     "ceiling": fixed + required + sum(size(p) for p in subpages),
                     "pages": len(parents) + len(subpages), "dependencies": dependencies, "code": code_size(name, table)})
    return {"fixed": fixed, "modules": rows}


def render(result: dict) -> str:
    lines = [f"固定入口（AGENTS、develop、路由表、架构模块表）：{result['fixed']} 字", "",
             "| 模块 | 已迁入 | 必读字数 | 上限字数 | 页数 | 代码字数 |", "|---|---|---|---|---|---|"]
    for row in result["modules"]:
        lines.append(f"| {row['module']} | {'是' if row['migrated'] else '否'} | {row['required']} | "
                     f"{row['ceiling']} | {row['pages']} | {row['code']} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="also write the measurement to this new JSON file")
    parser.add_argument("--max-required", type=int, help="fail when a migrated module's required reading exceeds this")
    args = parser.parse_args(argv)
    result = measure()
    print(render(result))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with args.json.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    if args.max_required is not None:
        over = [row for row in result["modules"] if row["migrated"] and row["required"] > args.max_required]
        for row in over:
            print(f"Reading budget exceeded: {row['module']} requires {row['required']} > {args.max_required}")
        return 1 if over else 0
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
