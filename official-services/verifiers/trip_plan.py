import json


def verify(ctx):
    data = json.loads(ctx.read("trip.json").decode("utf-8"))
    days = data.get("days") if isinstance(data, dict) else None
    want_days = ctx.values["spec.trip.days"]
    budget = ctx.values["spec.trip.budget"]
    checks = []
    ok = isinstance(days, list) and len(days) == want_days
    checks.append({"check": "days", "want": want_days, "got": len(days) if isinstance(days, list) else None})
    if not ok:
        return {"status": "fail", "checks": checks}
    total = 0
    for i, d in enumerate(days):
        spots = d.get("spots") if isinstance(d, dict) else None
        cost = d.get("cost") if isinstance(d, dict) else None
        good_spots = isinstance(spots, list) and len(spots) >= 3 and all(isinstance(s, str) and s.strip() for s in spots)
        good_cost = isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0
        checks.append({"check": f"day{i + 1}", "spots": len(spots) if isinstance(spots, list) else None, "wantSpots": ">= 3",
                       "cost": cost, "ok": good_spots and good_cost})
        ok = ok and good_spots and good_cost
        if good_cost:
            total += cost
    within = total <= budget
    checks.append({"check": "budget", "total": total, "limit": budget, "ok": within})
    return {"status": "pass" if ok and within else "fail", "checks": checks}
