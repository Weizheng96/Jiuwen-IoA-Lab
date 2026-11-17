"""Running one verifier with request replay (design: 设计/验收器.md §3–§4)."""
from __future__ import annotations

import json
import copy
from contextlib import nullcontext
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, ContextManager

from ..codec import canonical, digest_bytes
from ..errors import TEMPLATE_INVALID, fail
from .package import Package
from .sandbox import DEFAULT_FUEL, Sandbox

GUEST_DIR = Path(__file__).parent / "guest"
ARGV = ["python", "-I", "-S", "-B", "/a2c/_boot.py"]
MAX_ROUNDS = 16
NEED_KIND = {"skill": "observe", "service": "attest"}

Resolver = Callable[[Mapping[str, Any]], Any]


@dataclass
class Outcome:
    """``status`` is pass, fail, pending or error; ``responses`` is the replay record."""

    status: str
    not_before: str | None = None
    checks: list = field(default_factory=list)
    responses: list = field(default_factory=list)
    error: str | None = None
    rounds: int = 0


def values_for(reads: Sequence[str], template: Mapping[str, Any], card: Mapping[str, Any], *,
               material: str | None = None) -> dict:
    """The card values a manifest declares in ``reads``; const keys come from the template.
    An Input Verifier (``material`` set) may read only that material's attributes."""
    out: dict[str, Any] = {}
    schemas = {d["name"]: d["specSchema"] for d in template["deliverables"]}
    materials = {m["name"]: m["specSchema"] for m in template.get("inputs", [])}
    for path in reads:
        head, owner, key = path.split(".")
        if material is not None and (head != "inputs" or owner != material):
            fail(TEMPLATE_INVALID, f"an Input Verifier of {material} cannot read {path}")
        table, filled = (schemas, card["spec"]) if head == "spec" else (materials, card["inputs"])
        schema = table.get(owner, {}).get(key)
        if schema is None:
            fail(TEMPLATE_INVALID, f"reads names {path}, which the template does not define")
        out[path] = schema["const"] if "const" in schema else (filled.get(owner) or {}).get(key)
    return out


def run(package: Package, *, target: str, values: Mapping[str, Any], files: Mapping[str, bytes],
        period: int | None = None, anchored_at: str | None = None, resolver: Resolver | None = None,
        recorded: Sequence[Mapping[str, Any]] = (), sandbox: Sandbox | None = None,
        fuel: int = DEFAULT_FUEL,
        resolve_context: Callable[[int, Mapping[str, Any]], ContextManager[None]] | None = None) -> Outcome:
    kind = package.manifest["kind"]
    sandbox = sandbox or Sandbox.get(package.manifest["runtime"])
    work = Path(tempfile.mkdtemp(prefix="a2c-files-"))
    try:
        listing = []
        for i, (name, data) in enumerate(files.items()):
            (work / f"f{i}").write_bytes(data)
            listing.append({"name": name, "digest": digest_bytes(data), "path": f"/files/f{i}"})
        responses = [dict(r) for r in recorded]
        for rounds in range(1, MAX_ROUNDS + 2):
            stdin = {"kind": kind, "entry": package.manifest["entry"], "period": period, "target": target,
                     "values": dict(values), "files": listing, "anchoredAt": anchored_at, "responses": responses}
            res = sandbox.run(ARGV, canonical(stdin),
                              {"/a2c": GUEST_DIR, "/pkg": package.dir, "/files": work}, fuel=fuel)
            if res.trap:
                return Outcome("error", error=f"sandbox: {res.trap}", responses=responses, rounds=rounds)
            try:
                out = json.loads(res.stdout.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                tail = res.stderr.decode("utf-8", "replace")[-300:]
                return Outcome("error", error=f"no output (exit {res.exit_code}): {tail}", responses=responses,
                               rounds=rounds)
            if "result" in out:
                r = out["result"]
                return Outcome(r["status"], r.get("notBefore"), list(r.get("checks", [])), responses, rounds=rounds)
            if "error" in out:
                return Outcome("error", error=f"{out['error'].get('type')}: {out['error'].get('message')}",
                               responses=responses, rounds=rounds)
            need = out["need"]
            if not isinstance(need, dict):
                return Outcome("error", error="need must be one {kind, request}", responses=responses, rounds=rounds)
            if need.get("kind") != NEED_KIND.get(kind):
                return Outcome("error", error=f"a {kind} verifier asked for {need.get('kind')}", responses=responses,
                               rounds=rounds)
            if resolver is None:
                return Outcome("error", error="no resolver for an external answer", responses=responses,
                               rounds=rounds)
            context = resolve_context(len(responses), copy.deepcopy(need)) if resolve_context else nullcontext()
            with context:
                response = resolver(need)
            responses.append({"need": need, "response": response})
        return Outcome("error", error="too many external requests", responses=responses, rounds=MAX_ROUNDS + 1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


__all__ = ["Outcome", "Resolver", "values_for", "run", "MAX_ROUNDS"]
