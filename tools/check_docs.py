"""Check current local links and coverage of the layered design index."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def design_documents():
    return sorted(p for p in (ROOT / "设计").rglob("*.md")
                  if "变更" not in p.relative_to(ROOT / "设计").parts)


def local_targets(path):
    text = re.sub(r"```[\s\S]*?```", "", path.read_text(encoding="utf-8"))
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = target.strip().strip("<>")
        if urlsplit(target).scheme or target.startswith("#"):
            continue
        target = unquote(target.split("#", 1)[0])
        if target:
            yield target, (path.parent / target).resolve()


def current_documents():
    files = [ROOT / name for name in ("README.md", "AGENTS.md", "历史备份/README.md")]
    files.extend(design_documents())
    files.append(ROOT / "设计/变更/README.md")
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / ".agents/skills").rglob("*.md"))
    files.extend((ROOT / "demo").glob("*/剧本.md"))
    for folder in (
        "src/platform", "src/access/sdk", "official-services", "personal-assistant", "connector",
        "experiments", "demo", "demo/verification",
    ):
        files.append(ROOT / folder / "README.md")
    return sorted(set(files))


def check_design_index():
    index = ROOT / "设计/README.md"
    if not index.is_file():
        return ["Missing design index: 设计/README.md"]
    indexed = {resolved for _, resolved in local_targets(index)}
    return [f"Design page missing from index: {path.relative_to(ROOT)}"
            for path in design_documents()
            if path != index and path.resolve() not in indexed]


def main():
    failures = check_design_index()
    files = current_documents()
    for path in files:
        if not path.is_file():
            failures.append(f"Missing documentation entry: {path.relative_to(ROOT)}")
            continue
        for target, resolved in local_targets(path):
            if not resolved.exists():
                failures.append(f"Broken link: {path.relative_to(ROOT)} -> {target}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Current documentation links and design index coverage passed: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
