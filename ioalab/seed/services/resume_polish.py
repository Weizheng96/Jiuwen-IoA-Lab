import json


def _chars(obj):
    """全部字符串值的总字数（递归累加），与材料声明 chars 的口径一致。"""
    if isinstance(obj, str):
        return len(obj)
    if isinstance(obj, dict):
        return sum(_chars(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_chars(v) for v in obj)
    return 0


def verify(ctx):
    data = json.loads(ctx.read("resume.json").decode("utf-8"))
    entries = ctx.values["inputs.resume.entries"]
    chars = ctx.values["inputs.resume.chars"]
    checks = []
    exp = data.get("experience") if isinstance(data, dict) else None
    got_entries = len(exp) if isinstance(exp, list) else None
    ok_entries = got_entries == entries
    checks.append({"check": "entries", "want": entries, "got": got_entries, "ok": ok_entries})
    total = _chars(data)
    lo, hi = int(chars * 0.7), int(chars * 1.3 + 0.999)
    ok_chars = lo <= total <= hi
    checks.append({"check": "chars", "declared": chars, "got": total, "want": f"[{lo}, {hi}]", "ok": ok_chars})
    return {"status": "pass" if ok_entries and ok_chars else "fail", "checks": checks}
