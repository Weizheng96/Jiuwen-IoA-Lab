"""Temporal acceptance of actual renderer counters, independent of business counts."""

import math


def summarize(samples, *, window_seconds=10):
    if len(samples) < 2:
        raise ValueError("Need timed renderer samples")
    if any(b["at"] <= a["at"] for a, b in zip(samples, samples[1:])):
        raise ValueError("Renderer sample times must increase")
    for family in ("delegation", "p2p"):
        if any(b[family] < a[family] for a, b in zip(samples, samples[1:])):
            raise ValueError("Renderer counters must not reset inside the capture")
    windows = []
    for i in range(math.floor((samples[-1]["at"] - samples[0]["at"]) / window_seconds)):
        start = samples[0]["at"] + i * window_seconds
        before = min(samples, key=lambda s: abs(s["at"] - start))
        after = min(samples, key=lambda s: abs(s["at"] - (start + window_seconds)))
        counts = {k: after[k] - before[k] for k in ("delegation", "p2p")}
        windows.append(
            {
                "start": round(start, 3),
                "end": round(start + window_seconds, 3),
                **counts,
                "mixed": all(v > 0 for v in counts.values()),
            }
        )
    totals = {k: samples[-1][k] - samples[0][k] for k in ("delegation", "p2p")}
    total = sum(totals.values())
    mixed = sum(w["mixed"] for w in windows)
    return {
        "windowSeconds": window_seconds,
        "windows": windows,
        "mixedWindows": mixed,
        "totalWindows": len(windows),
        "mixedShare": mixed / len(windows) if windows else 0,
        "renderedTransmissions": totals,
        "delegationShare": totals["delegation"] / total if total else 0,
        "passed": bool(windows) and mixed == len(windows),
    }
