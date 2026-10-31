import json


def verify(ctx):
    data = json.loads(ctx.read("posts.json").decode("utf-8"))
    posts = data.get("posts") if isinstance(data, dict) else None
    shop = ctx.values["spec.posts.shop"]
    count, max_chars = ctx.values["spec.posts.count"], ctx.values["spec.posts.maxChars"]
    checks = []
    ok = isinstance(posts, list) and len(posts) == count
    checks.append({"check": "count", "want": count, "got": len(posts) if isinstance(posts, list) else None})
    if ok:
        for i, p in enumerate(posts):
            good = isinstance(p, str) and len(p) <= max_chars and shop in p
            checks.append({"check": f"post{i + 1}", "chars": len(p) if isinstance(p, str) else None, "hasShop": isinstance(p, str) and shop in p})
            ok = ok and good
    return {"status": "pass" if ok else "fail", "checks": checks}
