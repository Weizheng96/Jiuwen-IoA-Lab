# 通用 L1 验收器：按服务定义里的 verifier.checks（声明式规则）机械核交付件。
# 在 python.wasm 沙箱里运行（CPython 标准库，无网络）；加载时 __RULES__ 被替换成规则 JSON。
# 规则里以 "spec." / "inputs." 开头的字符串从卡上声明的标量（ctx.values）取值，其余是字面量。
import json
import re

RULES = __RULES__


def _val(ctx, v):
    if isinstance(v, str) and (v.startswith("spec.") or v.startswith("inputs.")):
        return ctx.values[v]
    return v


def _resolve(data, path):
    """按点路径取值；"a.b[].c" 对列表逐项展开。返回叶值列表（空路径 → [data]）。"""
    cur = [data]
    for part in [p for p in path.split(".") if p]:
        each = part.endswith("[]")
        key = part[:-2] if each else part
        nxt = []
        for c in cur:
            if not isinstance(c, dict) or key not in c:
                return None
            v = c[key]
            if each:
                if not isinstance(v, list):
                    return None
                nxt.extend(v)
            else:
                nxt.append(v)
        cur = nxt
    return cur


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _words(ctx, r):
    """规则里的词表：列表字面量、spec 里的列表，或 spec 里按 split（默认逗号）切开的字符串。"""
    w = _val(ctx, r["words"])
    if isinstance(w, str):
        sep = r.get("split", ",")
        w = [x.strip() for x in re.split("[" + re.escape(sep) + "，、]", w) if x.strip()]
    return w if isinstance(w, list) else None


def _cjk(s):
    return sum(1 for ch in s if "一" <= ch <= "鿿")


def c_keys(ctx, data, r):
    vals = _resolve(data, r.get("path", ""))
    ok = vals is not None and all(isinstance(v, dict) and all(k in v for k in r["required"]) for v in vals)
    return {"ok": bool(ok), "required": r["required"], "n": len(vals) if vals else None}


def c_list_len(ctx, data, r):
    vals = _resolve(data, r["path"])
    if not vals or not all(isinstance(v, list) for v in vals):
        return {"ok": False, "got": None}
    lo, hi, eq = _val(ctx, r.get("min")), _val(ctx, r.get("max")), _val(ctx, r.get("eq"))
    ok = all((eq is None or len(v) == eq) and (lo is None or len(v) >= lo) and (hi is None or len(v) <= hi) for v in vals)
    return {"ok": ok, "got": [len(v) for v in vals], "eq": eq, "min": lo, "max": hi}


def c_each_str_len(ctx, data, r):
    vals = _resolve(data, r["path"])
    if vals is None:
        return {"ok": False}
    lo, hi = _val(ctx, r.get("min", 1)), _val(ctx, r.get("max"))
    lens = [len(v) if isinstance(v, str) else None for v in vals]
    ok = all(l is not None and l >= lo and (hi is None or l <= hi) for l in lens)
    return {"ok": ok, "lens": lens, "min": lo, "max": hi}


def c_each_contains(ctx, data, r):
    vals = _resolve(data, r["path"])
    needle = _val(ctx, r["value"])
    if vals is None or not isinstance(needle, str):
        return {"ok": False}
    hits = [isinstance(v, str) and needle in v for v in vals]
    return {"ok": bool(hits) and all(hits), "needle": needle, "hits": hits}


def c_contains_all(ctx, data, r):
    """path 指向的值（或整份交付件）的全部字符串须包含 words 里的每一个词（words 可来自 spec，字符串按 split 切开）。"""
    vals = _resolve(data, r.get("path", ""))
    words = _words(ctx, r)
    if vals is None or words is None:
        return {"ok": False}
    text = "\n".join(s for v in vals for s in _strings(v))
    missing = [w for w in words if str(w) not in text]
    return {"ok": not missing, "missing": missing}


def c_forbid(ctx, data, r):
    vals = _resolve(data, r.get("path", ""))
    words = _words(ctx, r)
    if vals is None or words is None:
        return {"ok": False}
    text = "\n".join(s for v in vals for s in _strings(v))
    found = [w for w in words if str(w) and str(w) in text]
    return {"ok": not found, "found": found}


def c_cjk_ratio(ctx, data, r):
    """path 指向的字符串里中文字符占比 ≥ min（译中、中文文案）或 ≤ max（译英）。"""
    vals = _resolve(data, r["path"])
    if vals is None or not vals or not all(isinstance(v, str) and v for v in vals):
        return {"ok": False}
    text = "".join(vals)
    ratio = _cjk(text) / max(len(text), 1)
    lo, hi = r.get("min", 0.0), r.get("max", 1.0)
    return {"ok": lo <= ratio <= hi, "ratio": round(ratio, 3), "min": lo, "max": hi}


def c_sum_le(ctx, data, r):
    vals = _resolve(data, r["path"])
    cap = _val(ctx, r["max"])
    if vals is None or not all(_num(v) for v in vals) or not _num(cap):
        return {"ok": False, "sum": None, "max": cap}
    return {"ok": sum(vals) <= cap, "sum": sum(vals), "max": cap}


def c_each_num(ctx, data, r):
    vals = _resolve(data, r["path"])
    lo, hi = _val(ctx, r.get("min")), _val(ctx, r.get("max"))
    if vals is None or not vals:
        return {"ok": False}
    ok = all(_num(v) and (lo is None or v >= lo) and (hi is None or v <= hi) for v in vals)
    return {"ok": ok, "n": len(vals), "min": lo, "max": hi}


def c_each_in(ctx, data, r):
    vals = _resolve(data, r["path"])
    allowed = _val(ctx, r["in"])
    if vals is None or not vals:
        return {"ok": False}
    bad = [v for v in vals if v not in allowed]
    return {"ok": not bad, "bad": bad[:5]}


def c_each_regex(ctx, data, r):
    vals = _resolve(data, r["path"])
    if vals is None or not vals:
        return {"ok": False}
    pat = re.compile(r["pattern"])
    bad = [v for v in vals if not (isinstance(v, str) and pat.fullmatch(v))]
    return {"ok": not bad, "bad": bad[:5]}


def c_unique(ctx, data, r):
    vals = _resolve(data, r["path"])
    if vals is None:
        return {"ok": False}
    flat = [json.dumps(v, ensure_ascii=False, sort_keys=True) for v in vals]
    return {"ok": len(flat) == len(set(flat)), "n": len(flat), "distinct": len(set(flat))}


def c_total_chars(ctx, data, r):
    """全部字符串值的总字数：绝对区间 min/max，或相对 base（卡上声明的字数）的 ratio 区间。"""
    vals = _resolve(data, r.get("path", ""))
    if vals is None:
        return {"ok": False}
    total = sum(len(s) for v in vals for s in _strings(v))
    lo, hi = _val(ctx, r.get("min")), _val(ctx, r.get("max"))
    base = _val(ctx, r.get("base"))
    if _num(base):
        lo = base * r.get("min_ratio", 0.5)
        hi = base * r.get("max_ratio", 2.0)
    ok = (lo is None or total >= lo) and (hi is None or total <= hi)
    return {"ok": ok, "total": total, "min": lo, "max": hi}


def c_str_len(ctx, data, r):
    vals = _resolve(data, r["path"])
    if vals is None or len(vals) != 1 or not isinstance(vals[0], str):
        return {"ok": False}
    n = len(vals[0])
    lo, hi = _val(ctx, r.get("min", 1)), _val(ctx, r.get("max"))
    return {"ok": n >= lo and (hi is None or n <= hi), "chars": n, "min": lo, "max": hi}


def c_word_ratio(ctx, data, r):
    """英文词数 / 卡上声明的中文字数 在区间内（翻译类）。"""
    vals = _resolve(data, r["path"])
    base = _val(ctx, r["base"])
    if vals is None or len(vals) != 1 or not isinstance(vals[0], str) or not _num(base) or base <= 0:
        return {"ok": False}
    words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", vals[0]))
    ratio = words / base
    return {"ok": r.get("min", 0.4) <= ratio <= r.get("max", 1.0), "words": words, "base": base, "ratio": round(ratio, 3)}


def c_count_true(ctx, data, r):
    """path 展开后的布尔值里 True 的个数 ≥ min（如 tests[].pass）。"""
    vals = _resolve(data, r["path"])
    if vals is None:
        return {"ok": False}
    n = sum(1 for v in vals if v is True)
    return {"ok": n >= _val(ctx, r.get("min", 1)) and (r.get("all") is not True or n == len(vals)), "true": n, "n": len(vals)}


CHECKS = {"keys": c_keys, "list_len": c_list_len, "each_str_len": c_each_str_len, "each_contains": c_each_contains,
          "contains_all": c_contains_all, "forbid": c_forbid, "sum_le": c_sum_le, "each_num": c_each_num, "each_in": c_each_in,
          "each_regex": c_each_regex, "unique": c_unique, "total_chars": c_total_chars, "str_len": c_str_len,
          "word_ratio": c_word_ratio, "count_true": c_count_true, "cjk_ratio": c_cjk_ratio}


def verify(ctx):
    try:
        data = json.loads(ctx.read(RULES["file"]).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "checks": [{"check": "json", "ok": False, "error": str(e)[:200]}]}
    checks = []
    ok = True
    for r in RULES["checks"]:
        fn = CHECKS.get(r["kind"])
        res = fn(ctx, data, r) if fn else {"ok": False, "error": "unknown check " + str(r.get("kind"))}
        res = {"check": r.get("name") or r["kind"], **res}
        checks.append(res)
        ok = ok and bool(res.get("ok"))
    return {"status": "pass" if ok else "fail", "checks": checks}
