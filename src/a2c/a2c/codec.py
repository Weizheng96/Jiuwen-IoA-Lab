"""JSON, canonicalization, digests, times and durations (design: 设计/线上编码.md §2)."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import rfc8785

from .errors import INVALID_OBJECT, fail

WIRE_VERSION = "0.0.18"
MAX_SAFE_INT = 2**53
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")
_DURATION = re.compile(r"^P(?!$)(?:(\d+)D)?(?:T(?!$)(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


# ── JSON ────────────────────────────────────────────────────────────────

def check_ijson(obj: Any, path: str = "$") -> None:
    """Reject values outside I-JSON (RFC 7493): non-finite numbers, unsafe integers, non-string keys."""
    if obj is None or isinstance(obj, (bool, str)):
        return
    if isinstance(obj, int):
        if abs(obj) > MAX_SAFE_INT:
            fail(INVALID_OBJECT, f"{path}: integer outside ±2^53")
        return
    if isinstance(obj, float):
        if not math.isfinite(obj):
            fail(INVALID_OBJECT, f"{path}: non-finite number")
        return
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            check_ijson(v, f"{path}[{i}]")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                fail(INVALID_OBJECT, f"{path}: non-string key")
            check_ijson(v, f"{path}.{k}")
        return
    fail(INVALID_OBJECT, f"{path}: {type(obj).__name__} is not JSON")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            fail(INVALID_OBJECT, f"duplicate key {k!r}")
        out[k] = v
    return out


def _no_constant(name: str) -> Any:
    fail(INVALID_OBJECT, f"{name} is not I-JSON")


def loads(data: bytes | str) -> Any:
    """Parse strict I-JSON: UTF-8, no duplicate keys, no NaN/Infinity, safe integers."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            fail(INVALID_OBJECT, "not UTF-8")
    try:
        obj = json.loads(data, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except json.JSONDecodeError as e:
        fail(INVALID_OBJECT, f"malformed JSON: {e.msg}")
    check_ijson(obj)
    return obj


def canonical(obj: Any) -> bytes:
    """RFC 8785 (JCS) canonical form."""
    check_ijson(obj)
    return rfc8785.dumps(obj)


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest(obj: dict, exclude: Iterable[str] = ()) -> str:
    """Digest of ``obj`` without the members in ``exclude``."""
    ex = set(exclude)
    return digest_bytes(canonical({k: v for k, v in obj.items() if k not in ex}))


def is_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_DIGEST.match(value))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ── time ────────────────────────────────────────────────────────────────

def parse_time(text: Any) -> datetime:
    """RFC 3339 instant in UTC with a ``Z`` suffix."""
    if not isinstance(text, str) or not _TIME.match(text):
        fail(INVALID_OBJECT, f"not an RFC 3339 UTC instant: {text!r}")
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        fail(INVALID_OBJECT, f"not an RFC 3339 UTC instant: {text!r}")


def fmt_time(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration(text: Any) -> timedelta:
    """ISO 8601 ``PnDTnHnMnS`` with integer fields; no years, months or weeks."""
    m = _DURATION.match(text) if isinstance(text, str) else None
    if not m:
        fail(INVALID_OBJECT, f"not a duration PnDTnHnMnS: {text!r}")
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    try:
        return timedelta(days=d, hours=h, minutes=mi, seconds=s)
    except OverflowError:
        fail(INVALID_OBJECT, f"duration too large: {text!r}")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "WIRE_VERSION", "MAX_SAFE_INT", "check_ijson", "loads", "canonical", "digest", "digest_bytes",
    "is_digest", "b64url", "b64url_decode", "parse_time", "fmt_time", "parse_duration", "utcnow",
]
