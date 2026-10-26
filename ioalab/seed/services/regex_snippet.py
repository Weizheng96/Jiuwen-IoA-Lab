import json
import re


def verify(ctx):
    data = json.loads(ctx.read("snippet.json").decode("utf-8"))
    pattern = data.get("pattern") if isinstance(data, dict) else None
    checks = []
    try:
        tests = json.loads(ctx.values["spec.snippet.tests"] or "[]")
    except ValueError:
        return {"status": "fail", "checks": [{"check": "tests", "error": "spec.tests 不是合法 JSON"}]}
    if not isinstance(pattern, str) or not isinstance(tests, list) or not tests:
        return {"status": "fail", "checks": [{"check": "shape", "pattern": pattern, "tests": len(tests) if isinstance(tests, list) else None}]}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"status": "fail", "checks": [{"check": "compile", "pattern": pattern, "error": str(e)[:200]}]}
    ok = True
    for i, t in enumerate(tests):
        inp = t.get("input") if isinstance(t, dict) else None
        want = t.get("match") if isinstance(t, dict) else None
        got = bool(rx.fullmatch(inp)) if isinstance(inp, str) else None
        good = isinstance(want, bool) and got == want
        checks.append({"check": f"test{i + 1}", "input": inp, "want": want, "got": got, "ok": good})
        ok = ok and good
    return {"status": "pass" if ok else "fail", "checks": checks}
