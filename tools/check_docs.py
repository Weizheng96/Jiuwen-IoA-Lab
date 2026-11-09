"""Check current local links, heading anchors and coverage of the layered design index."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"```[\s\S]*?```")


def design_documents():
    return sorted(p for p in (ROOT / "设计").rglob("*.md")
                  if "变更" not in p.relative_to(ROOT / "设计").parts)


def slug(heading):
    """Heading anchor as rendered by GitHub: keep word characters, hyphens and spaces."""
    heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path):
    text = path.read_text(encoding="utf-8")
    found, seen = set(re.findall(r'<a\s+(?:id|name)="([^"]+)"', text)), {}
    for heading in re.findall(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", FENCE.sub("", text), re.M):
        name = slug(heading)
        count = seen.get(name, 0)
        seen[name] = count + 1
        found.add(name if not count else f"{name}-{count}")
    return found


def local_links(path):
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = target.strip().strip("<>")
        if urlsplit(target).scheme:
            continue
        name, _, fragment = target.partition("#")
        name = unquote(name)
        resolved = (path.parent / name).resolve() if name else path.resolve()
        # Product references generated from the one shared source at build time.
        for role in ('assistant', 'provider'):
            references = ROOT / 'src/access/skills' / role / 'references'
            if resolved.is_relative_to(references) and not resolved.exists():
                shared = ROOT / 'src/access/skills/_shared/references' / resolved.relative_to(references)
                if shared.is_file():
                    resolved = shared
        yield target, name, resolved, unquote(fragment)


def local_targets(path):
    for _, name, resolved, _ in local_links(path):
        if name:
            yield name, resolved


def current_documents():
    files = [ROOT / name for name in ("README.md", "AGENTS.md", "历史备份/README.md")]
    files.extend(design_documents())
    files.append(ROOT / "设计/变更/README.md")
    files.extend((ROOT / ".agents/skills").rglob("*.md"))
    files.extend((ROOT / "src/access/skills").rglob("*.md"))
    files.extend((ROOT / "tools/media").rglob("*.md"))

    for folder in (
        "src/platform", "src/access/sdk", "src/access/examples", "src/hosts/jiuwen", "src/official",
        "tools", "tools/media", "tools/verification",
    ):
        files.append(ROOT / folder / "README.md")
    return sorted(set(files))


def check_design_index():
    index = ROOT / "设计/README.md"
    if not index.is_file():
        return ["Missing design index: 设计/README.md"]
    indexed = {resolved for _, resolved in local_targets(index)}
    # A split page registers its own sub-pages; the root index lists only the parent.
    for parent in sorted(indexed):
        folder = parent.with_suffix("")
        if parent.suffix == ".md" and parent.is_file() and folder.is_dir():
            indexed |= {resolved for _, resolved in local_targets(parent) if resolved.parent == folder}
    return [f"Design page missing from index: {path.relative_to(ROOT)}"
            for path in design_documents()
            if path != index and path.resolve() not in indexed]


def link_failures(files):
    failures, known = [], {}
    for path in files:
        if not path.is_file():
            failures.append(f"Missing documentation entry: {path.relative_to(ROOT)}")
            continue
        for target, _, resolved, fragment in local_links(path):
            if not resolved.exists():
                failures.append(f"Broken link: {path.relative_to(ROOT)} -> {target}")
            elif fragment and resolved.is_file() and resolved.suffix == ".md":
                if resolved not in known:
                    known[resolved] = anchors(resolved)
                if fragment not in known[resolved] and fragment.lower() not in known[resolved]:
                    failures.append(f"Broken anchor: {path.relative_to(ROOT)} -> {target}")
    return failures


def main():
    files = current_documents()
    failures = check_design_index() + link_failures(files)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Current documentation links, anchors and design index coverage passed: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
