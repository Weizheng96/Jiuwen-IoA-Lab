"""Check module folders against the code: declared dependencies, entry files and the entry rule, test columns and the progress index."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = "设计"
FENCE = re.compile(r"```[\s\S]*?```")
LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
TICKET = re.compile(r"^\d\d-.*\.md$")

# Module name -> code paths (files or directories). The longest matching prefix owns a file.
MODULES = {
    "身份中心": ["src/platform/ioalab/identity"],
    "注册中心": ["src/platform/ioalab/registry"],
    "委托中心": ["src/platform/ioalab/delegation"],
    "支付中心": ["src/platform/ioalab/payments"],
    "消息中心": ["src/platform/ioalab/messaging"],
    "观测": ["src/platform/ioalab/observation"],
    "web服务": ["src/platform/ioalab/web", "src/platform/ioalab/app.py", "src/platform/ioalab/operations.py",
              "src/platform/ioalab/cli.py", "src/platform/ioalab/bootstrap.py", "src/platform/ioalab/readiness.py",
              "src/platform/ioalab/seed", "src/platform/ioalab/__init__.py", "src/platform/ioalab/__main__.py"],
    "网页UI": ["src/platform/ioalab/web/static"],
    "平台基础": ["src/platform/ioalab/storage.py", "src/platform/ioalab/migrations", "src/platform/ioalab/errors.py",
             "src/platform/ioalab/files.py", "src/platform/ioalab/keys.py", "src/platform/ioalab/events.py",
             "src/platform/ioalab/config.py", "src/platform/ioalab/configuration.py",
             "src/platform/ioalab/configuration_permissions.py"],
    "共用运行时": ["src/access/sdk/ioalab_sdk/runtime", "src/access/sdk/ioalab_sdk/adapters",
              "src/access/sdk/ioalab_sdk/templates.py", "src/access/sdk/ioalab_sdk/__init__.py", "src/access/skills/_shared"],
    "个人助手端": ["src/access/sdk/ioalab_sdk/assistant", "src/access/skills/assistant"],
    "服务方端": ["src/access/sdk/ioalab_sdk/provider", "src/access/skills/provider"],
    "宿主适配": ["src/hosts/jiuwen/ioalab_jiuwen"],
    "SDK装配": ["src/access/sdk/ioalab_sdk/access.py"],
    "官方服务": ["src/official/services/ioalab_services"],
    "管理员助手": ["src/official/admin-assistant/ioalab_admin_assistant", "src/official/tools/ioalab_tools"],
    "在线试用": ["src/trial/ioalab_trial", "deploy/trial"],
    "部署": ["deploy"],
}
# Code that calls modules but is not a module folder itself.
OUTSIDE = {"示例": ["src/access/examples"], "开发工具": ["tools"], "测试": ["tests"]}
# Import roots for absolute imports.
PACKAGES = {
    "ioalab": "src/platform/ioalab", "ioalab_sdk": "src/access/sdk/ioalab_sdk",
    "ioalab_jiuwen": "src/hosts/jiuwen/ioalab_jiuwen", "ioalab_services": "src/official/services/ioalab_services",
    "ioalab_admin_assistant": "src/official/admin-assistant/ioalab_admin_assistant",
    "ioalab_tools": "src/official/tools/ioalab_tools", "ioalab_trial": "src/trial/ioalab_trial",
    "deploy": "deploy", "tools": "tools",
}
# Entry rule (module-format.md · 入口). Only modules named in CONVERGED are held to it; tickets 03b—03d add one line
# per module once its imports go through the entry. PACKAGE: every entry file is an __init__.py of the module's
# packages and holds only imports and dunder names. FILES: at most MAX_FILE_ENTRIES entry files.
PACKAGE, FILES = "package", "files"
MAX_FILE_ENTRIES = 8
CONVERGED = {
    "平台基础": FILES,
    "web服务": FILES,
    "身份中心": PACKAGE,
    "注册中心": PACKAGE,
    "委托中心": PACKAGE,
    "支付中心": PACKAGE,
    "消息中心": PACKAGE,
    "观测": PACKAGE,
}


def owners():
    """(code path, module name) sorted so the longest prefix is tried first."""
    pairs = [(ROOT / path, name) for name, paths in {**MODULES, **OUTSIDE}.items() for path in paths]
    return sorted(pairs, key=lambda pair: -len(pair[0].as_posix()))


def module_of(path, table):
    path = path.resolve()
    for code, name in table:
        if path == code or path.is_relative_to(code):
            return name
    return None


def module_folders():
    """module name -> folder under 设计/组件 holding 接口.md."""
    found = {}
    for page in sorted((ROOT / DESIGN / "组件").rglob("接口.md")):
        found[page.parent.name] = page.parent
    return found


def table_rows(text, column=None):
    """Rows of every Markdown table as lists of cells; with `column`, only that column's cells."""
    rows, header = [], None
    for line in FENCE.sub("", text).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if column is None:
            rows.append(cells)
        elif column in header and len(cells) > header.index(column):
            rows.append(cells[header.index(column)])
    return rows


def plain(cell):
    return LINK.sub(r"\1", cell).replace("`", "").strip()


def python_files(name):
    for path in MODULES[name]:
        full = ROOT / path
        if full.is_file() and full.suffix == ".py":
            yield full
        elif full.is_dir():
            yield from (p for p in full.rglob("*.py") if "__pycache__" not in p.parts)


def resolve_import(source, module, level):
    """Path (file or package directory) an import statement points at, or None for third-party imports."""
    if level:
        base = source.parent
        for _ in range(level - 1):
            base = base.parent
        target = base.joinpath(*module.split(".")) if module else base
    else:
        top, _, rest = module.partition(".")
        if top not in PACKAGES:
            return None
        target = ROOT / PACKAGES[top]
        if rest:
            target = target.joinpath(*rest.split("."))
    if target.with_suffix(".py").is_file():
        return target.with_suffix(".py")
    return target


def imports(source):
    """(target path, statement text) for every import whose target lives in this repository."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = resolve_import(source, alias.name, 0)
                if target is not None:
                    yield target, f"import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            target = resolve_import(source, node.module or "", node.level)
            if target is not None:
                yield target, f"from {'.' * node.level}{node.module or ''} import {', '.join(a.name for a in node.names)}"


def edges():
    """(from module, to module, source file, target path, statement) for every cross-module import."""
    table = owners()
    for name in MODULES:
        for source in python_files(name):
            for target, statement in imports(source):
                owner = module_of(target, table)
                if owner is not None and owner != name:
                    yield name, owner, source, target, statement


def declared_dependencies(folder):
    page = folder / "依赖.md"
    if not page.is_file():
        return None
    return {plain(cell) for cell in table_rows(page.read_text(encoding="utf-8"), "依赖模块")}


def entry_files(name, folder):
    page = folder / "实现.md"
    if not page.is_file():
        return None
    primary = ROOT / MODULES[name][0]
    primary = primary if primary.is_dir() else primary.parent
    entries = set()
    for cell in table_rows(page.read_text(encoding="utf-8"), "入口文件"):
        for item in re.split(r"[、,，]", plain(cell)):
            item = item.strip()
            if item:
                entries.add((ROOT / item if item.startswith(("src/", "deploy/", "tools/")) else primary / item).resolve())
    return entries


def check_architecture_table(folders):
    architecture = ROOT / DESIGN / "架构.md"
    if not architecture.is_file():
        return ["Missing architecture page: 设计/架构.md"]
    text = architecture.read_text(encoding="utf-8")
    if "## 模块表" not in text:
        return ["Architecture page has no 模块表 section"]
    section = text.split("## 模块表", 1)[1].split("\n## ", 1)[0]
    names = set()
    for cells in table_rows(section):
        if len(cells) >= 2:
            names.add(plain(cells[1]) if plain(cells[1]) not in ("—", "-", "") else plain(cells[0]))
    failures = [f"Module missing from architecture table: {name}" for name in MODULES if name not in names]
    failures += [f"Architecture table row without a code mapping in tools/check_modules.py: {name}"
                 for name in sorted(names) if name not in MODULES and name not in ("模块", "子模块")]
    failures += [f"Module folder without a code mapping: {folder.relative_to(ROOT)}"
                 for name, folder in folders.items() if name not in MODULES]
    return failures


def check_dependencies(folders):
    failures, seen = [], set()
    declared = {name: declared_dependencies(folder) for name, folder in folders.items()}
    entries = {name: entry_files(name, folder) for name, folder in folders.items()}
    for name, folder in folders.items():
        if declared[name] is None:
            failures.append(f"Missing dependency page: {(folder / '依赖.md').relative_to(ROOT)}")
        if entries[name] is None:
            failures.append(f"Missing implementation page: {(folder / '实现.md').relative_to(ROOT)}")
    for source_module, target_module, source, target, statement in edges():
        key = (source_module, target_module, source, statement)
        if key in seen:
            continue
        seen.add(key)
        if source_module in folders and declared.get(source_module) is not None and target_module not in declared[source_module]:
            failures.append(f"Undeclared dependency: {source_module} -> {target_module} in {source.relative_to(ROOT)}: {statement}")
        if target_module in folders and entries.get(target_module):
            actual = (target / "__init__.py" if target.is_dir() else target).resolve()
            if actual not in entries[target_module]:
                failures.append(f"Import bypasses entry files of {target_module}: {source.relative_to(ROOT)}: {statement}")
    return failures


def package_entries(name):
    """__init__.py files of the packages a module owns."""
    found = set()
    for path in MODULES[name]:
        full = ROOT / path
        if full.is_dir() and (full / "__init__.py").is_file():
            found.add((full / "__init__.py").resolve())
        elif full.name == "__init__.py" and full.is_file():
            found.add(full.resolve())
    return found


def logic_in_entry(entry):
    """Line numbers of statements other than a docstring, imports and dunder assignments such as __all__."""
    tree = ast.parse(entry.read_text(encoding="utf-8", errors="replace"))
    lines = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if targets and all(isinstance(t, ast.Name) and t.id.startswith("__") and t.id.endswith("__") for t in targets):
            continue
        lines.append(node.lineno)
    return lines


def check_entries(folders):
    """Entry rule for the modules in CONVERGED; other modules are not judged yet."""
    failures = []
    for name, form in CONVERGED.items():
        if name not in MODULES or name not in folders:
            failures.append(f"Converged module has no code mapping or module folder: {name}")
            continue
        entries = entry_files(name, folders[name])
        if not entries:
            failures.append(f"Converged module lists no entry files: {name}")
            continue
        relative = {entry: entry.relative_to(ROOT.resolve()) if entry.is_relative_to(ROOT.resolve()) else entry
                    for entry in entries}
        failures += [f"Entry file of {name} does not exist: {relative[entry]}"
                     for entry in sorted(entries) if not entry.is_file()]
        if form == PACKAGE:
            packages = package_entries(name)
            failures += [f"Package module {name} has an entry other than its __init__.py: {relative[entry]}"
                         for entry in sorted(entries - packages)]
            for entry in sorted(entries & packages):
                failures += [f"Entry of package module {name} holds more than imports and __all__: {relative[entry]}:{line}"
                             for line in logic_in_entry(entry)]
        elif form == FILES:
            if len(entries) > MAX_FILE_ENTRIES:
                failures.append(f"File-entry module {name} lists {len(entries)} entry files; at most {MAX_FILE_ENTRIES}")
        else:
            failures.append(f"Converged module {name} has unknown entry form {form!r}; use PACKAGE or FILES")
    return failures


def check_tests(folders):
    failures = []
    for name, folder in folders.items():
        # A large interface page splits into 接口/<分组>.md; every sub-page carries its own test column.
        for page in [folder / "接口.md", *sorted((folder / "接口").glob("*.md"))]:
            for cell in table_rows(page.read_text(encoding="utf-8"), "测试"):
                for item in re.split(r"[、,，]", plain(cell)):
                    item = item.strip()
                    if not item or item in ("—", "-"):
                        continue
                    file = item.split("::", 1)[0]
                    pattern = file if file.startswith("tests/") else f"tests/test_{file}.py"
                    if not list(ROOT.glob(pattern)):
                        failures.append(f"Test missing for interface row: {page.relative_to(ROOT)}: {item}")
    return failures


def check_progress():
    index = ROOT / DESIGN / "工程/进展.md"
    if not index.is_file():
        return ["Missing progress index: 设计/工程/进展.md"]
    text = index.read_text(encoding="utf-8")
    linked = {(index.parent / target.split("#")[0]).resolve() for _, target in LINK.findall(FENCE.sub("", text))
              if not target.startswith(("http:", "https:"))}
    counted = {}
    for cells in table_rows(text):
        if len(cells) >= 2 and cells[1].isdigit():
            for group in re.split(r"[、,，]", plain(cells[0])):
                counted[group.strip()] = int(cells[1])
    failures = []
    for progress in sorted((ROOT / DESIGN / "组件").rglob("进展")):
        if not progress.is_dir():
            continue
        group = progress.parent.name
        tickets = sorted(p for p in progress.iterdir() if TICKET.match(p.name))
        numbers = {p.name[:2] for p in tickets}
        if group not in counted:
            failures.append(f"Progress index has no row for module group: {group}")
        elif counted[group] != len(tickets):
            failures.append(f"Progress index counts {counted[group]} tickets for {group}; folder has {len(tickets)}")
        for ticket in tickets:
            body = ticket.read_text(encoding="utf-8")
            if ticket.resolve() not in linked:
                failures.append(f"Ticket missing from progress index: {ticket.relative_to(ROOT)}")
            blocked = re.search(r"\*\*阻塞于：\*\*\s*(.+)", body)
            status = re.search(r"\*\*状态：\*\*\s*(\S+)", body)
            if not blocked or not status:
                failures.append(f"Ticket lacks 阻塞于 or 状态 line: {ticket.relative_to(ROOT)}")
                continue
            if not status.group(1).startswith(("ready", "claimed")):
                failures.append(f"Ticket status must start with ready or claimed: {ticket.relative_to(ROOT)}")
            for ref in re.findall(r"\d\d", blocked.group(1)):
                if ref not in numbers:
                    failures.append(f"Ticket blocked by a missing ticket {ref}: {ticket.relative_to(ROOT)}")
    return failures


def main():
    folders = module_folders()
    failures = (check_architecture_table(folders) + check_dependencies(folders) + check_entries(folders)
                + check_tests(folders) + check_progress())
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Module folders passed: {len(folders)} migrated of {len(MODULES)} modules; "
          f"{sum(1 for _ in edges())} cross-module imports checked; "
          f"entry rule holds for {len(CONVERGED)} converged modules")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
