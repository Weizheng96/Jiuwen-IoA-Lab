"""Running a target's verifiers, building the report, and arbitration recompute (design: 设计/验收器.md §4–§6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence, ContextManager

from ..codec import digest, parse_time
from ..errors import NOT_ARBITRABLE, A2CError, fail
from ..objects import new, object_digest
from .package import Package, load
from .runner import Outcome, run, values_for

Fetch = Callable[[str], bytes]                                              # uri → package bytes
ModelPort = Callable[[Mapping[str, Any], Mapping[str, bytes]], Any]         # (observe request, files) → answer
# (verifier ref, service, card digest, period, manifest digest, values, files) → (verified attestation, signed at)
AttestPort = Callable[[Mapping[str, Any], str, str, int, str, Any, Mapping[str, bytes]], tuple[Mapping[str, Any], str]]


@dataclass
class VerifierRun:
    kind: str
    ref: Mapping[str, Any]
    outcome: Outcome
    attestations: list = field(default_factory=list)

    @property
    def observations(self) -> list:
        return [r["response"] for r in self.outcome.responses] if self.kind == "skill" else []


@dataclass
class TargetResult:
    """``status``: pass, fail, pending (with ``not_before``) or error."""

    status: str
    runs: list[VerifierRun]
    not_before: str | None = None
    errors: list[str] = field(default_factory=list)


def _combine(runs: list[VerifierRun]) -> TargetResult:
    errors = [f"{r.ref['id']}: {r.outcome.error}" for r in runs if r.outcome.status == "error"]
    pending = [r.outcome.not_before for r in runs if r.outcome.status == "pending"]
    if errors:
        return TargetResult("error", runs, errors=errors)
    if pending:
        try:
            times = sorted((parse_time(t), t) for t in pending if t)
        except A2CError:
            return TargetResult("error", runs, errors=["malformed notBefore"])
        return TargetResult("pending", runs, not_before=times[-1][1] if times else None)
    return TargetResult("pass" if all(r.outcome.status == "pass" for r in runs) else "fail", runs)


def run_target(refs: Mapping[str, Mapping[str, Any]], *, fetch: Fetch, template: Mapping[str, Any],
               card: Mapping[str, Any], target: str, files: Mapping[str, bytes], period: int | None = None,
               anchored_at: str | None = None, manifest_digest: str | None = None, card_digest: str | None = None,
               model: ModelPort | None = None, attest: AttestPort | None = None,
               observation_context: Callable[[str, Mapping, int, Mapping], ContextManager[None]] | None = None) -> TargetResult:
    """Run every verifier of one deliverable (``period`` set) or one material (``period`` None)."""
    runs: list[VerifierRun] = []
    for kind in ("script", "skill", "service"):
        ref = refs.get(kind)
        if ref is None:
            continue
        pkg = load(fetch(ref["uri"]), ref, kind)
        attestations: list = []
        resolver = None
        if kind == "skill" and model is not None:
            resolver = lambda need, m=model: m(need["request"], files)  # noqa: E731
        if kind == "service" and attest is not None:
            def resolver(need, ref=ref, pkg=pkg, acc=attestations):
                a, signed_at = attest(ref, pkg.manifest["service"], card_digest, period, manifest_digest,
                                      need["request"], files)
                acc.append(dict(a))
                return {"statement": a["statement"], "signedAt": signed_at}
        values = values_for(pkg.manifest["reads"], template, card, material=None if period else target)
        outcome = run(pkg, target=target, values=values, files=files, period=period, anchored_at=anchored_at,
                      resolver=resolver, resolve_context=(
                          lambda ordinal, need, ref=ref: observation_context(target, ref, ordinal, need)
                      ) if kind == "skill" and observation_context is not None else None)
        runs.append(VerifierRun(kind, ref, outcome, attestations))
    return _combine(runs)


def report(card_digest: str, result: TargetResult, *, period: int | None = None, input: str | None = None) -> dict:
    """The report object of a concluded target (pass or fail)."""
    results = []
    for r in result.runs:
        entry = {"verifier": r.ref["id"], "status": "pass" if r.outcome.status == "pass" else "fail"}
        if r.kind == "skill":
            entry["observationsDigest"] = digest({"observations": r.observations})
        if r.kind == "service":
            entry["attestationDigests"] = [object_digest(a) for a in r.attestations]
        results.append(entry)
    return new("report", cardDigest=card_digest, period=period, input=input, results=results)


def recompute(refs: Mapping[str, Mapping[str, Any]], *, fetch: Fetch, template: Mapping[str, Any],
              card: Mapping[str, Any], target: str, files: Mapping[str, bytes], period: int | None,
              anchored_at: str | None, attestations: Sequence[Mapping[str, Any]],
              models: Mapping[str, ModelPort], verify_attestation: Callable[[Mapping[str, Any]], tuple[str, str]],
              card_digest: str | None = None, manifest_digest: str | None = None) -> TargetResult:
    """Arbitration: scripts once, skills once per dispute model (all must pass), services replayed
    from the stored attestations, each bound to this verifier, card, period and manifest and signed
    by the declared service (``verify_attestation`` returns its signer and signing time).
    A verifier that is still pending, errs or cannot be replayed fails."""
    runs: list[VerifierRun] = []
    for kind in ("script", "skill", "service"):
        ref = refs.get(kind)
        if ref is None:
            continue
        pkg: Package = load(fetch(ref["uri"]), ref, kind)
        values = values_for(pkg.manifest["reads"], template, card, material=None if period else target)
        if kind == "skill":
            for model_id in ref["disputeModels"]:
                if model_id not in models:
                    fail(NOT_ARBITRABLE, f"dispute model {model_id} is not available")
                model = models[model_id]
                outcome = run(pkg, target=target, values=values, files=files, period=period, anchored_at=anchored_at,
                              resolver=lambda need, m=model: m(need["request"], files))
                runs.append(VerifierRun(kind, {**ref, "id": f"{ref['id']}#{model_id}"}, _closed(outcome)))
            continue
        resolver = None
        if kind == "service":
            queue = list(attestations)

            def resolver(need, queue=queue, ref=ref, pkg=pkg):
                if not queue:
                    raise LookupError("no stored attestation left to replay")
                a = queue.pop(0)
                signer, signed_at = verify_attestation(a)
                bound = (a.get("verifier"), a.get("cardDigest"), a.get("period"), a.get("manifestDigest"))
                if signer != pkg.manifest["service"] or bound != (ref["id"], card_digest, period, manifest_digest):
                    raise LookupError("stored attestation is not bound to this verifier, card, period and manifest")
                return {"statement": a["statement"], "signedAt": signed_at}
        try:
            outcome = run(pkg, target=target, values=values, files=files, period=period, anchored_at=anchored_at,
                          resolver=resolver)
        except (LookupError, A2CError) as e:
            outcome = Outcome("error", error=str(e))
        runs.append(VerifierRun(kind, ref, _closed(outcome), list(attestations) if kind == "service" else []))
    return _combine(runs)


def _closed(outcome: Outcome) -> Outcome:
    """In arbitration nothing stays open: pending and error count as fail."""
    if outcome.status in ("pending", "error"):
        return Outcome("fail", outcome.not_before, outcome.checks, outcome.responses,
                       error=outcome.error or f"{outcome.status} at recompute", rounds=outcome.rounds)
    return outcome


__all__ = ["Fetch", "ModelPort", "AttestPort", "VerifierRun", "TargetResult", "run_target", "report", "recompute"]
