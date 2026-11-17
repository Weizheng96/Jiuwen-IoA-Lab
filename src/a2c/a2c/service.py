"""Certified Service helper: answers ``attestation.request`` with a signed Attestation (design: 设计/验收器.md §5)."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from . import objects
from .common import Clock, Endpoint, Reply, now_or
from .errors import INVALID_OBJECT, fail
from .jws import Signer

# (verifier id, values, files) → statement, or a string reason to refuse
Issue = Callable[[str, Any, Mapping[str, bytes]], Any]


class CertifiedService(Endpoint):
    OPS = ("attestation.request",)

    def __init__(self, signer: Signer, *, issue: Issue, verifiers: tuple[str, ...] = (),
                 clock: Clock | None = None) -> None:
        super().__init__()
        self.signer = signer
        self.issue = issue
        self.verifiers = set(verifiers)
        self.now = now_or(clock)

    @property
    def id(self) -> str:
        return self.signer.id

    def op_attestation_request(self, body, files, task_id) -> Reply:
        for k in ("verifier", "cardDigest", "period", "manifestDigest", "values"):
            if k not in body:
                fail(INVALID_OBJECT, f"attestation.request needs {k}")
        task = self.new_task("attestation.request")
        if self.verifiers and body["verifier"] not in self.verifiers:
            statement: Any = f"no declaration for {body['verifier']}"
        else:
            statement = self.issue(body["verifier"], body["values"], files)
        task.state = "completed"
        if isinstance(statement, str):
            task.artifacts.append({"reason": statement})
            return Reply({"op": "attestation.request", "reason": statement}, task_id=task.id, state=task.state)
        attestation = objects.signed(objects.new("attestation", verifier=body["verifier"], cardDigest=body["cardDigest"],
                                                 period=body["period"], manifestDigest=body["manifestDigest"],
                                                 statement=statement), self.signer, self.now())
        task.artifacts.append({"attestation": attestation})
        return Reply({"op": "attestation.request", "attestation": attestation}, task_id=task.id, state=task.state)


__all__ = ["CertifiedService"]
