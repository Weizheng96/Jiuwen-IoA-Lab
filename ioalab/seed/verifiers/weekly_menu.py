import json


def verify(ctx):
    data = json.loads(ctx.read("menu.json").decode("utf-8"))
    days = data.get("days") if isinstance(data, dict) else None
    allergens = [a.strip() for a in (ctx.values["spec.menu.allergens"] or "").split(",") if a.strip()]
    checks = []
    ok = isinstance(days, list) and len(days) == 7
    checks.append({"check": "days", "want": 7, "got": len(days) if isinstance(days, list) else None})
    if not ok:
        return {"status": "fail", "checks": checks}
    for di, d in enumerate(days):
        meals = d.get("meals") if isinstance(d, dict) else None
        good_day = isinstance(meals, list) and len(meals) == 3
        checks.append({"check": f"day{di + 1}:meals", "want": 3, "got": len(meals) if isinstance(meals, list) else None})
        ok = ok and good_day
        if not good_day:
            continue
        for mi, m in enumerate(meals):
            ings = m.get("ingredients") if isinstance(m, dict) else None
            good_shape = isinstance(ings, list) and len(ings) > 0 and all(isinstance(x, str) for x in ings)
            hits = sorted({a for a in allergens for x in ings if a in x}) if good_shape else None
            good = good_shape and not hits
            checks.append({"check": f"day{di + 1}:meal{mi + 1}", "dish": m.get("dish") if isinstance(m, dict) else None,
                           "ingredients": len(ings) if isinstance(ings, list) else None, "allergenHit": hits})
            ok = ok and good
    return {"status": "pass" if ok else "fail", "checks": checks}
