import json
import re

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")


def _singular(w):
    """去复数（只处理常见英文规则）：cities→city · boxes→box · cards→card；glass 等以 ss 结尾的不动。"""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and (w.endswith("ches") or w.endswith("shes") or w.endswith("xes") or w.endswith("ses") or w.endswith("zes")):
        return w[:-2]
    if len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _norm(text):
    return " ".join(_singular(w.lower()) for w in _WORD.findall(text))


def verify(ctx):
    data = json.loads(ctx.read("translation.json").decode("utf-8"))
    text = data.get("translation") if isinstance(data, dict) else None
    chars = ctx.values["inputs.source.chars"]
    terms_raw = ctx.values["spec.translation.terms"] or ""
    checks = []
    if not isinstance(text, str) or not isinstance(chars, int) or chars <= 0:
        return {"status": "fail", "checks": [{"check": "shape", "ok": False}]}
    words = len(_WORD.findall(text))
    ratio = words / chars
    ok = 0.4 <= ratio <= 1.0
    checks.append({"check": "ratio", "words": words, "chars": chars, "ratio": round(ratio, 3), "want": "[0.4, 1.0]", "ok": ok})
    normed = " " + _norm(text) + " "
    for item in terms_raw.split(","):
        item = item.strip()
        if not item:
            continue
        term = item.split("=", 1)[1].strip() if "=" in item else item
        hit = (" " + _norm(term) + " ") in normed
        checks.append({"check": "term", "term": term, "ok": hit})
        ok = ok and hit
    return {"status": "pass" if ok else "fail", "checks": checks}
