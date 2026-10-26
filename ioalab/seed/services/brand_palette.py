import json
import re

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _luminance(hex_color):
    """WCAG 2.x 相对亮度（sRGB）。"""
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def verify(ctx):
    data = json.loads(ctx.read("palette.json").decode("utf-8"))
    colors = data.get("colors") if isinstance(data, dict) else None
    background = ctx.values["spec.palette.background"]
    min_contrast = ctx.values["spec.palette.minContrast"]
    checks = []
    ok = isinstance(colors, list) and len(colors) == 5
    checks.append({"check": "count", "want": 5, "got": len(colors) if isinstance(colors, list) else None})
    if not ok:
        return {"status": "fail", "checks": checks}
    hexes = []
    for i, c in enumerate(colors):
        h = c.get("hex") if isinstance(c, dict) else c
        good = isinstance(h, str) and bool(_HEX.match(h))
        checks.append({"check": f"hex{i + 1}", "value": h, "ok": good})
        ok = ok and good
        hexes.append(h if good else None)
    if ok:
        ratio = _contrast(hexes[0], background)
        good = ratio >= min_contrast
        checks.append({"check": "primaryContrast", "primary": hexes[0], "background": background,
                       "ratio": round(ratio, 2), "want": min_contrast, "ok": good})
        ok = ok and good
    return {"status": "pass" if ok else "fail", "checks": checks}
