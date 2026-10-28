"""Check local links in current project documentation, excluding archived and vendor content."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def current_documents():
    files = [ROOT / name for name in ("README.md", "AGENTS.md", "历史备份/README.md")]
    for folder in ("设计", "docs"):
        files.extend((ROOT / folder).glob("*.md"))
    files.extend((ROOT / "demo").glob("*/剧本.md"))
    for folder in (
        "sdk",
        "official-services",
        "personal-assistant",
        "connector",
        "experiments",
        "demo",
        "demo/verification",
    ):
        files.append(ROOT / folder / "README.md")
    return sorted(set(files))


def main():
    failures = []
    files = current_documents()
    for path in files:
        if not path.is_file():
            failures.append(f"Missing documentation entry: {path.relative_to(ROOT)}")
            continue
        text = re.sub(r"```[\s\S]*?```", "", path.read_text(encoding="utf-8"))
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = target.strip().strip("<>")
            if urlsplit(target).scheme or target.startswith("#"):
                continue
            target = unquote(target.split("#", 1)[0])
            if target and not (path.parent / target).exists():
                failures.append(f"Broken link: {path.relative_to(ROOT)} -> {target}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Current documentation entries and local links passed: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
