import json


def verify(ctx):
    data = json.loads(ctx.read("outline.json").decode("utf-8"))
    pages = data.get("pages") if isinstance(data, dict) else None
    min_pages = ctx.values["spec.outline.minPages"]
    min_bullets = ctx.values["spec.outline.minBullets"]
    checks = []
    ok = isinstance(pages, list) and len(pages) >= min_pages
    checks.append({"check": "pages", "want": f">= {min_pages}", "got": len(pages) if isinstance(pages, list) else None, "ok": ok})
    if not isinstance(pages, list):
        return {"status": "fail", "checks": checks}
    for i, p in enumerate(pages):
        title = p.get("title") if isinstance(p, dict) else None
        bullets = p.get("bullets") if isinstance(p, dict) else None
        good_title = isinstance(title, str) and bool(title.strip())
        good_bullets = isinstance(bullets, list) and len(bullets) >= min_bullets and all(isinstance(b, str) and b.strip() for b in bullets)
        checks.append({"check": f"page{i + 1}", "title": title, "bullets": len(bullets) if isinstance(bullets, list) else None,
                       "ok": good_title and good_bullets})
        ok = ok and good_title and good_bullets
    return {"status": "pass" if ok else "fail", "checks": checks}
