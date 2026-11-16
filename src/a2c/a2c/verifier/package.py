"""Verifier packages: a zip of Python source with ``a2c-verifier.json`` (design: 设计/验收器.md §1)."""
from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..codec import digest_bytes
from ..errors import DIGEST_MISMATCH, TEMPLATE_INVALID, fail
from .sandbox import cache_dir, runtimes

MANIFEST = "a2c-verifier.json"
MAX_FILES = 256
MAX_UNPACKED = 8 * 1024 * 1024
_ENTRY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
_READ = re.compile(r"^(spec\.[A-Za-z_][A-Za-z0-9_]*|inputs\.[A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Package:
    """A loaded package; ``dir`` holds its files, read-only to the guest."""

    manifest: Mapping[str, Any]
    dir: Path
    digest: str
    blob: bytes


def load(blob: bytes, ref: Mapping[str, Any], kind: str, code: str = TEMPLATE_INVALID) -> Package:
    """Check ``blob`` against the reference ``ref`` found at a ``kind`` position and unpack it."""
    d = digest_bytes(blob)
    if d != ref["digest"]:
        fail(DIGEST_MISMATCH, f"verifier package {ref['id']} does not match its digest")
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        infos = z.infolist()
    except zipfile.BadZipFile:
        fail(code, f"{ref['id']}: not a zip file")
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED:
        fail(code, f"{ref['id']}: package too large")
    for i in infos:
        parts = i.filename.split("/")
        mode = (i.external_attr >> 16) & 0o170000
        if i.filename.startswith("/") or "\\" in i.filename or ".." in parts or (mode and mode not in (0o100000, 0o040000)):
            fail(code, f"{ref['id']}: unsafe entry {i.filename!r}")
    try:
        manifest = json.loads(z.read(MANIFEST))
    except (KeyError, ValueError):
        fail(code, f"{ref['id']}: missing or malformed {MANIFEST}")
    check_manifest(manifest, ref, kind, code)
    target = cache_dir() / "packages" / d.split(":")[1]
    if not (target / MANIFEST).exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=target.parent))
        z.extractall(tmp)
        try:
            tmp.rename(target)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
    return Package(manifest=manifest, dir=target, digest=d, blob=blob)


def check_manifest(manifest: Any, ref: Mapping[str, Any], kind: str, code: str = TEMPLATE_INVALID) -> None:
    if not isinstance(manifest, dict):
        fail(code, "verifier manifest must be an object")
    allowed = {"id", "kind", "runtime", "entry", "reads"} | ({"service"} if kind == "service" else set())
    if set(manifest) - allowed or not {"id", "kind", "runtime", "entry", "reads"} <= set(manifest):
        fail(code, f"{ref['id']}: manifest members must be {sorted(allowed)}")
    if manifest["id"] != ref["id"]:
        fail(code, f"manifest id {manifest['id']!r} is not {ref['id']!r}")
    if manifest["kind"] != kind:
        fail(code, f"{ref['id']}: manifest kind {manifest['kind']!r} at a {kind} position")
    if manifest["runtime"] not in runtimes():
        fail(code, f"{ref['id']}: unsupported runtime {manifest['runtime']!r}")
    if not isinstance(manifest["entry"], str) or not _ENTRY.match(manifest["entry"]):
        fail(code, f"{ref['id']}: entry must be module:function")
    reads = manifest["reads"]
    if not isinstance(reads, list) or not all(isinstance(r, str) and _READ.match(r) for r in reads):
        fail(code, f"{ref['id']}: reads are spec.<deliverable>.<key> or inputs.<material>.<key>")
    if kind == "service" and not isinstance(manifest.get("service"), str):
        fail(code, f"{ref['id']}: a service verifier declares its service")


def build(files: Mapping[str, str | bytes], manifest: Mapping[str, Any]) -> bytes:
    """Build a deterministic package zip (used by providers and tests)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        entries = {MANIFEST: json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode()}
        entries.update({k: v.encode() if isinstance(v, str) else v for k, v in files.items()})
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            z.writestr(info, entries[name])
    return buf.getvalue()


__all__ = ["MANIFEST", "Package", "load", "check_manifest", "build"]
