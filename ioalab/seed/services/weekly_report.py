import json

SECTIONS = ("thisWeek", "nextWeek", "risks")


def verify(ctx):
    data = json.loads(ctx.read("report.json").decode("utf-8"))
    keywords = ctx.values["spec.report.keywords"] or ""
    checks = []
    ok = isinstance(data, dict)
    for s in SECTIONS:
        v = data.get(s) if isinstance(data, dict) else None
        good = isinstance(v, list) and len(v) > 0 and all(isinstance(x, str) and x.strip() for x in v)
        checks.append({"check": f"section:{s}", "items": len(v) if isinstance(v, list) else None, "ok": good})
        ok = ok and good
    full = "\n".join("\n".join(x for x in (data.get(s) or []) if isinstance(x, str)) for s in SECTIONS) if isinstance(data, dict) else ""
    for item in keywords.split(","):
        item = item.strip()
        if not item:
            continue
        words = [w.strip() for w in item.split("|") if w.strip()]
        missing = [w for w in words if w not in full]
        checks.append({"check": "keywords", "item": item, "missing": missing, "ok": not missing})
        ok = ok and not missing
    return {"status": "pass" if ok else "fail", "checks": checks}
