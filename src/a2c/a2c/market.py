"""Platform SDK, run by the Agent Market (design: 设计/交易市场.md).

In the protocol the Market signs templates, slot 3 (proposal) and slot 5(i), pushes anchor
notices, records closed cards and countersigns provenance. Arbitration is the Market's own
service; :meth:`Market.recompute` gives it the same verdict machinery the parties use.
"""
from __future__ import annotations

import base64
from typing import Any, Callable, Mapping

from .card import card_digest, validate_card
from .codec import fmt_time, parse_time
from .common import Clock, Directory, Endpoint, Reply, Store, now_or
from .errors import (CARD_INVALID, INVALID_OBJECT, NOT_ARBITRABLE, OUT_OF_ORDER, TEMPLATE_EXPIRED, TEMPLATE_INVALID,
                     UNKNOWN_CARD, fail)
from .jws import Signer, check_fresh, header_of, sign
from .objects import check, object_digest, sign_role, verify_role, verify_signed
from .slots import market_of, sign_slot, verify_card, verify_template
from .template import acceptance_of, deliverable_of, validate, verifier_refs
from .verifier.acceptance import ModelPort, TargetResult, recompute
from .verifier.package import load
from .verifier.sandbox import Sandbox


class Market(Endpoint):
    OPS = ("template.endorse", "card.endorse", "delivery.anchor", "anchor.watch", "card.close", "provenance.endorse")

    def __init__(self, signer: Signer, *, keys: Directory, fetch: Callable[[str], bytes], clock: Clock | None = None,
                 store: Store | None = None, recognized_services: Mapping[str, Any] | None = None,
                 models: Mapping[str, ModelPort] | None = None) -> None:
        super().__init__()
        self.signer = signer
        self.keys = keys
        self.fetch = fetch
        self.now = now_or(clock)
        self.store = store or Store()
        self.recognized_services = dict(recognized_services or {})   # service Agent Card URL → JWKS
        self.models = dict(models or {})                             # model id → ModelPort
        for service, jwks in self.recognized_services.items():
            keys.add(service, jwks)
        self.watchers: dict[str, list[str]] = {}

    @property
    def id(self) -> str:
        return self.signer.id

    # ── snapshots ───────────────────────────────────────────────────────

    def _snapshot(self, key: str, template: Mapping[str, Any], card: Mapping[str, Any] | None = None) -> None:
        """Fetch and check every applicable verifier, then keep the packages with the terms."""
        packages, runtimes_used = [], {}
        code = CARD_INVALID if card is not None else TEMPLATE_INVALID
        for _target, _name, kind, ref in verifier_refs(template, card):
            if card is not None and _target == "input":
                continue
            pkg = load(self.fetch(ref["uri"]), ref, kind, code)
            runtimes_used[pkg.manifest["runtime"]] = Sandbox.get(pkg.manifest["runtime"]).wasm_sha256
            if kind == "service" and pkg.manifest["service"] not in self.recognized_services:
                fail(NOT_ARBITRABLE, f"{ref['id']}: service {pkg.manifest['service']} is not recognized")
            if kind == "skill":
                missing = [m for m in ref["disputeModels"] if m not in self.models]
                if missing:
                    fail(NOT_ARBITRABLE, f"{ref['id']}: cannot run dispute models {missing}")
            packages.append({"uri": ref["uri"], "digest": ref["digest"], "blob": base64.b64encode(pkg.blob).decode()})
        self.store.put("snapshots", key, {"template": template, "card": card, "packages": packages,
                                          "runtimes": runtimes_used})

    def snapshot_fetch(self, key: str) -> Callable[[str], bytes]:
        snap = self.store.get("snapshots", key)
        if snap is None:
            fail(UNKNOWN_CARD, f"no snapshot {key}")
        blobs = {p["uri"]: base64.b64decode(p["blob"]) for p in snap["packages"]}
        return lambda uri: blobs[uri]

    def _template(self, template_digest: str) -> dict:
        snap = self.store.get("snapshots", template_digest)
        if snap is None:
            fail(UNKNOWN_CARD, "template not endorsed by this market")
        return snap["template"]

    # ── operations ──────────────────────────────────────────────────────

    def op_template_endorse(self, body, files, task_id) -> Reply:
        template = validate(body["template"], endorsed=False)
        verify_template(template, self.keys, endorsed=False)
        if self.now() > parse_time(template["validUntil"]):
            fail(TEMPLATE_EXPIRED, "template already expired")
        self._snapshot(template["templateDigest"], template)
        signed = dict(template)
        signed["signatures"] = {**template["signatures"],
                                "market": sign(template["templateDigest"], self.signer, "market", self.now())}
        self.store.put("snapshots", template["templateDigest"],
                       {**self.store.get("snapshots", template["templateDigest"]), "template": signed})
        return Reply({"op": "template.endorse", "template": signed})

    def op_card_endorse(self, body, files, task_id) -> Reply:
        """Slot 3 says only that the card's verifiers are in the snapshot. Whether the Provider kept the
        Client's values (invariant 6) and drafted in time is the Client's to check before slot 1."""
        card = body["card"]
        template = self._template(card["templateDigest"])
        if template["formation"] != "proposal" or market_of(template) != self.id:
            fail(TEMPLATE_INVALID, "slot 3 is for proposal templates this market endorsed")
        validate_card(template, card)
        sigs = card["signatures"]
        if sigs["2"] is None or sigs["3"] is not None or sigs["1"] is not None:
            fail(OUT_OF_ORDER, "slot 3 comes after slot 2 and before slot 1")
        verify_card(template, card, self.keys)
        check_fresh(header_of(sigs["2"]), self.now())
        self._snapshot(card_digest(card), template, card)
        signed = sign_slot(card, 3, self.signer, self.now())
        return Reply({"op": "card.endorse", "card": signed})

    def op_delivery_anchor(self, body, files, task_id) -> Reply:
        card = body["card"]
        template = self._template(card["templateDigest"])
        if market_of(template) != self.id:
            fail(TEMPLATE_INVALID, "template endorsed by another market")
        validate_card(template, card)
        if "termination" in card["fulfillment"]:
            fail(OUT_OF_ORDER, "card is terminated")
        cd = card_digest(card)
        if self.store.get("rulings", cd) is not None:
            fail(OUT_OF_ORDER, "a ruling has ended this card")
        verify_card(template, card, self.keys)
        period = len(card["signatures"]["5"]) + 1
        if len(card["signatures"]["4"]) != period:
            fail(OUT_OF_ORDER, f"slot 5({period}) needs slot 4({period})")
        check_fresh(header_of(card["signatures"]["4"][period - 1]), self.now())
        signed = sign_slot(card, 5, self.signer, self.now(), period=period)
        self.store.put("cards", cd, signed)
        notice = {"cardDigest": cd, "period": period, "signature": signed["signatures"]["5"][period - 1]}
        for tid in self.watchers.get(cd, []):
            self.tasks[tid].artifacts.append(notice)
        return Reply({"op": "delivery.anchor", "card": signed})

    def op_anchor_watch(self, body, files, task_id) -> Reply:
        cd = body["cardDigest"]
        task = self.new_task("anchor.watch")
        self.watchers.setdefault(cd, []).append(task.id)
        card = self.store.get("cards", cd)
        for i, sig in enumerate((card or {}).get("signatures", {}).get("5", []), start=1):
            task.artifacts.append({"cardDigest": cd, "period": i, "signature": sig})
        return Reply({"op": "anchor.watch"}, task_id=task.id, state=task.state)

    def op_card_close(self, body, files, task_id) -> Reply:
        card = body["card"]
        template = self._template(card["templateDigest"])
        validate_card(template, card)
        chain = verify_card(template, card, self.keys)
        cd = card_digest(card)
        if self.store.get("rulings", cd) is not None:
            fail(OUT_OF_ORDER, "a ruling has ended this card; the Market recorded it")
        if "termination" in card["fulfillment"]:
            outcome = "client-terminated"
        elif chain.accepted == len(template["deliverables"]):
            outcome = "completed"
        else:
            fail(OUT_OF_ORDER, "card is neither fully accepted nor terminated")
        # the record of a closed card is its digest, outcome and time; the card itself is not kept
        self.store.put("ledger", cd, {"provider": template["provider"], "outcome": outcome, "closedAt": fmt_time(self.now())})
        for tid in self.watchers.pop(cd, []):
            self.tasks[tid].state = "completed"
        return Reply({"op": "card.close", "ok": True})

    def op_provenance_endorse(self, body, files, task_id) -> Reply:
        """The Provider brings the closed card; the Market only checks that it is one it closed."""
        prov = check(body["provenance"])
        card = body["card"]
        cd = card_digest(card)
        if cd != prov["cardDigest"]:
            fail(INVALID_OBJECT, "provenance names another card")
        if self.store.get("ledger", cd) is None:
            fail(OUT_OF_ORDER, "provenance needs a card this market closed")
        template = self._template(card["templateDigest"])
        validate_card(template, card)
        chain = verify_card(template, card, self.keys)
        digests = [f["digest"] for p in card["fulfillment"].get("periods", [])[:chain.accepted]
                   for f in p["manifest"]["files"]]
        if prov["sampleDigest"] not in digests:
            fail(OUT_OF_ORDER, "the sample is not a file of an accepted period of this card")
        client = card["client"]
        verify_role(prov, "client", client["jwk"], signer=client["id"])
        return Reply({"op": "provenance.endorse", "provenance": sign_role(prov, "market", self.signer, self.now())})

    # ── arbitration (the Market's own service) ──────────────────────────

    def recompute(self, card: Mapping[str, Any], period: int, files: Mapping[str, bytes]) -> TargetResult:
        """Rerun the period's verifiers from the snapshot taken when the Market signed."""
        template = self._template(card["templateDigest"])
        chain = verify_card(template, card, self.keys)
        key = card_digest(card) if template["formation"] == "proposal" else card["templateDigest"]
        d = deliverable_of(template, period)
        refs = acceptance_of(template, card)[d]["verifiers"]
        stored = (card["fulfillment"].get("periods") or [{}] * period)[period - 1].get("attestations", [])
        anchored = chain.period_ts(5, period)

        def verify_attestation(a: Mapping[str, Any]) -> tuple[str, str]:
            h = header_of(a.get("signature"))
            if h["a2c-signer"] not in self.recognized_services:
                fail(NOT_ARBITRABLE, "attestation signer is not recognized")
            verify_signed(a, self.keys.jwk(h["a2c-signer"], h.get("kid")), signer=h["a2c-signer"], expect_kid=True)
            return h["a2c-signer"], h["a2c-ts"]

        manifest = (card["fulfillment"].get("periods") or [{}] * period)[period - 1].get("manifest")
        return recompute(refs, fetch=self.snapshot_fetch(key), template=template, card=card, target=d, files=files,
                         period=period, anchored_at=fmt_time(anchored) if anchored else None, attestations=stored,
                         models=self.models, verify_attestation=verify_attestation, card_digest=card_digest(card),
                         manifest_digest=object_digest(manifest) if manifest else None)

    def record_ruling(self, card: Mapping[str, Any], ruling: Mapping[str, Any]) -> None:
        """A ruling ends the card (合同协议 §十一): no further anchors or closing."""
        cd = card_digest(card)
        self.store.put("rulings", cd, dict(ruling))
        self.store.put("ledger", cd, {"card": card, "outcome": "arbitrated", "closedAt": fmt_time(self.now())})


__all__ = ["Market"]
