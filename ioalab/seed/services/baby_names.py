import json


def verify(ctx):
    data = json.loads(ctx.read("names.json").decode("utf-8"))
    items = data.get("names") if isinstance(data, dict) else None
    surname = ctx.values["spec.names.surname"] or ""
    forbidden = set(ctx.values["spec.names.forbidden"] or "")
    count = ctx.values["spec.names.count"]
    checks = []
    ok = isinstance(items, list) and len(items) == count
    checks.append({"check": "count", "want": count, "got": len(items) if isinstance(items, list) else None})
    if ok:
        for i, it in enumerate(items):
            name = it.get("name") if isinstance(it, dict) else it
            good_shape = isinstance(name, str) and name.startswith(surname) and 2 <= len(name) <= 3
            hits = sorted(set(name) & forbidden) if isinstance(name, str) else None
            good = good_shape and not hits
            checks.append({"check": f"name{i + 1}", "name": name, "startsWithSurname": isinstance(name, str) and name.startswith(surname),
                           "length": len(name) if isinstance(name, str) else None, "forbiddenHit": hits})
            ok = ok and good
    return {"status": "pass" if ok else "fail", "checks": checks}
