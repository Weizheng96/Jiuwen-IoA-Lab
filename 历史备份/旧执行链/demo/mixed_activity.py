"""Temporal acceptance of actual renderer counters, independent of business counts."""

import math


def density(samples):
    observed = samples[1:]
    counts = [len(s.get("initiators", [])) for s in observed]
    distinct = set(a for s in observed for a in s.get("initiators", []))
    seconds = samples[-1]["at"] - samples[0]["at"]
    rate = sum(samples[-1][k] - samples[0][k] for k in ("delegation", "p2p")) / max(
        0.001, seconds
    )
    concurrent = sum(c >= 2 for c in counts) / max(1, len(counts))
    result = {
        "transmissionsPerSecond": round(rate, 3),
        "maxConcurrentInitiators": max(counts, default=0),
        "multipleInitiatorSampleShare": round(concurrent, 4),
        "distinctInitiators": len(distinct),
        "maxActiveLines": max((s.get("activeLines", 0) for s in observed), default=0),
    }
    result["passed"] = (
        rate >= 6
        and result["maxConcurrentInitiators"] >= 4
        and concurrent >= 0.25
        and len(distinct) >= 24
    )
    return result


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
