"""Client SDK (design: 设计/合同协议.md, 设计/线上编码.md §5).

The client agent decides what to delegate and to whom; the SDK verifies what it receives,
signs, keeps the card and runs the verifiers. Signatures the user must give (slot 1 / slot 7
when ``signers`` names the user, information and step authorizations) use ``user``.
"""
from __future__ import annotations

import base64
import copy
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from functools import wraps
from typing import Any, Callable, Mapping

from . import objects
from .card import (build_card, build_request, card_digest, request_digest, sign_request, validate_card,
                   validate_request)
from .codec import digest_bytes, fmt_time, parse_duration, parse_time
from .common import Clock, Directory, Store, Transport, now_or
from .errors import (BAD_SIGNATURE, CARD_INVALID, DEADLINE_PASSED, INVALID_OBJECT, OUT_OF_ORDER, TEMPLATE_EXPIRED,
                     UNKNOWN_CARD, A2CError, fail)
from .jws import Signer, check_fresh, check_window, header_of, verify
from .payment import PaymentPort, verify_voucher
from .payment.terms import termination_balance, verify_quote
from .pricing import deposit_notice
from .slots import period_manifest, sign_slot, verify_card, verify_template
from .template import acceptance_of, deliverable_of, material, validate
from .timing import Status, StepWindow, status
from .verifier.acceptance import ModelPort, TargetResult, report, run_target


@dataclass
class Draft:
    card: dict
    notices: list[str] = field(default_factory=list)


@dataclass
class Acceptance:
    """Outcome of :meth:`Client.accept`: ``status`` pass, fail, pending or error."""

    status: str
    card: dict
    result: TargetResult
    voucher: dict | None = None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _serialized_card(method):
    """Keep one Client's operations on the same stored card in lifecycle order."""
    reference_name = method.__code__.co_varnames[1]
    @wraps(method)
    def call(self, *args, **kwargs):
        if not args and reference_name not in kwargs:
            return method(self, *args, **kwargs)
        reference = args[0] if args else kwargs[reference_name]
        cd = card_digest(reference) if isinstance(reference, Mapping) else reference
        with self._card_lock(cd):
            return method(self, *args, **kwargs)
    return call


class Client:
    def __init__(self, signer: Signer, *, keys: Directory, transport: Transport, fetch: Callable[[str], bytes],
                 payment_provider: Mapping[str, Any], clock: Clock | None = None, store: Store | None = None,
                 payment: PaymentPort | None = None, model: ModelPort | None = None, user: Signer | None = None,
                 markets: tuple[str, ...] = (), sleep: Callable[[float], None] = time.sleep,
                 ap2_key: Any = None, name: str = '') -> None:
        self.signer = signer
        self.keys = keys
        self.transport = transport
        self.fetch = fetch
        self.payment_provider = dict(payment_provider)
        self.now = now_or(clock)
        self.store = store or Store()
        self.payment = payment
        self.model = model
        self.sleep = sleep
        self.user = user
        self.markets = set(markets)
        self.ap2_key = ap2_key
        self.name = name or signer.id
        self._card_lock_guard = threading.RLock()
        self._card_locks = {}

    def _card_lock(self, cd):
        with self._card_lock_guard:
            if cd not in self._card_locks:
                self._card_locks[cd] = threading.RLock()
            return self._card_locks[cd]

    # ── identity ────────────────────────────────────────────────────────

    @property
    def party(self) -> dict:
        return {"id": self.signer.id, "jwk": self.signer.inline_jwk}

    @property
    def user_party(self) -> dict | None:
        return {"id": self.user.id, "jwk": self.user.inline_jwk} if self.user else None

    def _slot_signer(self, card: Mapping[str, Any], slot: int) -> Signer:
        if card["signers"][f"slot{slot}"] == "user":
            if self.user is None:
                fail(BAD_SIGNATURE, f"slot {slot} must be signed by the user")
            return self.user
        return self.signer

    def _user(self) -> Signer:
        """Authorizations are the user's; without a user identity the Client signs them (a2c-signer = client.id)."""
        return self.user or self.signer

    # ── templates ───────────────────────────────────────────────────────

    def get_templates(self, provider: str, template_digest: str | None = None) -> list[dict]:
        body = {"templateDigest": template_digest} if template_digest else {}
        out = []
        for t in self.transport.send(provider, "template.get", body).body["templates"]:
            validate(t)
            if t["provider"] != provider:
                fail(INVALID_OBJECT, "template names another provider")
            verify_template(t, self.keys)
            market = header_of(t["signatures"]["market"])["a2c-signer"]
            if market not in self.markets:            # fail closed: only markets the Client trusts
                continue
            for s in t.get("samples", []):            # a sample's provenance is vouched for by that same Market
                if "provenance" in s:
                    prov = s["provenance"]
                    sig = (prov.get("signatures") or {}).get("market") or \
                        fail(BAD_SIGNATURE, f"sample {s['name']}: provenance lacks the Market signature")
                    objects.verify_role(prov, "market", self.keys.jwk(market, header_of(sig).get("kid")),
                                        signer=market, expect_kid=True)
            if self.now() > parse_time(t["validUntil"]):
                continue
            self.store.put("templates", t["templateDigest"], t)
            out.append(t)
        return out

    def template(self, digest: str) -> dict:
        t = self.store.get("templates", digest)
        if t is None:
            fail(UNKNOWN_CARD, "unknown template")
        return t

    # ── drafting ────────────────────────────────────────────────────────

    def _declare(self, materials: Mapping[str, tuple[bytes, Mapping[str, Any]]]) -> dict:
        return {n: {**decl, "digest": digest_bytes(data)} for n, (data, decl) in materials.items()}

    def _keep(self, key: str, value: dict, materials: Mapping[str, tuple[bytes, Mapping[str, Any]]]) -> None:
        self.store.put("cards", key, {**value, "materials": {n: _b64(d) for n, (d, _x) in materials.items()}})

    def draft_card(self, template: Mapping[str, Any], *, card_id: str, spec: Mapping[str, Any],
                   materials: Mapping[str, tuple[bytes, Mapping[str, Any]]], signers: Mapping[str, str],
                   delegation_scope_digest: str, extensions: Mapping[str, Any] | None = None) -> Draft:
        """Draft a card (preapproval / countersign templates): values, material digests, derived amounts."""
        if template["formation"] == "proposal":
            fail(CARD_INVALID, "a proposal template takes a Request Card")
        card = build_card(template, card_id=card_id, client=self.party, signers=signers, spec=spec,
                          inputs=self._declare(materials), delegation_scope_digest=delegation_scope_digest,
                          payment_provider=self.payment_provider,
                          user=self.user_party if "user" in signers.values() else None, extensions=extensions)
        validate_card(template, card)
        notice = deposit_notice(card["derived"])
        self._keep(card_digest(card), {"card": card, "state": "draft"}, materials)
        return Draft(card, [notice] if notice else [])

    def precheck(self, template: Mapping[str, Any], card_like: Mapping[str, Any],
                 materials: Mapping[str, bytes]) -> dict[str, TargetResult]:
        """Run the input verifiers on the Client's own materials before signing."""
        out = {}
        for n, data in materials.items():
            refs = material(template, n).get("verifiers")
            if refs:
                out[n] = run_target(refs, fetch=self.fetch, template=template, card=card_like, target=n,
                                    files={n: data}, model=self.model)
        return out

    @_serialized_card
    def sign(self, card: Mapping[str, Any]) -> dict:
        """Slot 1 (the user signs it when ``signers.slot1`` is ``user``)."""
        signed = sign_slot(card, 1, self._slot_signer(card, 1), self.now())
        rec = self._record(card_digest(card))
        rec["card"] = signed
        self._save(rec)
        return signed

    def authorize_materials(self, names: list[str], *, card_digest_: str | None = None,
                            request_digest_: str | None = None) -> dict | None:
        """The user's Information Authorization for the materials about to be sent."""
        if not names:
            return None
        body = {"cardDigest": card_digest_} if card_digest_ else {"requestDigest": request_digest_}
        user = self._user()
        return objects.signed(objects.new("information-authorization", inputs=sorted(names), jwk=user.inline_jwk,
                                          **body), user, self.now())

    # ── records ─────────────────────────────────────────────────────────

    def _record(self, key: str) -> dict:
        rec = self.store.get("cards", key)
        if rec is None:
            fail(UNKNOWN_CARD, "unknown card")
        return rec

    def _save(self, rec: dict) -> None:
        self.store.put("cards", card_digest(rec["card"]), rec)

    def card(self, cd: str) -> dict:
        return self._record(cd)["card"]

    def _files(self, rec: Mapping[str, Any], names) -> dict[str, bytes]:
        return {n: base64.b64decode(rec["materials"][n]) for n in names}

    # ── countersign ─────────────────────────────────────────────────────

    def _await(self, to: str, reply, deadline) -> dict:
        """A task operation answers with the task; its result is retrieved with GetTask (线上编码 §一).
        Polls until the first artifact is there, the task ends, or ``deadline`` passes."""
        if reply.task_id is None or any(k in reply.body for k in ("card", "reason", "receipt", "rejection", "attestation")):
            return reply.body
        wait = 0.2
        while True:
            task = self.transport.get_task(to, reply.task_id)
            if task.artifacts:
                body = task.artifacts[0]
                if "error" in body:
                    raise A2CError(body["error"]["code"], body["error"].get("message", ""))
                return body
            if task.state in ("completed", "failed", "rejected", "canceled"):
                fail(INVALID_OBJECT, f"task {reply.task_id} ended without a result")
            if deadline is not None and self.now() > deadline:
                fail(DEADLINE_PASSED, "no answer within respondWithin")
            self.sleep(wait)
            wait = min(wait * 2, 5.0)

    def _last_known_ts(self, rec: Mapping[str, Any]):
        """The latest signed instant the Client holds on a started card; a retrieved signature may not precede it."""
        card = rec["card"]
        ts = [header_ts(card["fulfillment"]["receipt"]["signature"])] if card["fulfillment"].get("receipt") else []
        ts += [header_ts(s) for s in card["signatures"]["7"]]
        ts += [header_ts(s["answer"]["signature"]) for s in rec.get("steps", []) if s.get("answer")]
        return max(ts) if ts else None

    @_serialized_card
    def countersign(self, card: Mapping[str, Any]) -> dict | str:
        """Send a signed card for slot 2; returns the countersigned card or the provider's reason."""
        template = self.template(card["templateDigest"])
        cd = card_digest(card)
        rec = self._record(cd)
        review = [n for n in card["inputs"] if material(template, n).get("reviewBeforeSigning")]
        body = {"card": card}
        auth = self.authorize_materials(review, card_digest_=cd)
        if auth:
            body["informationAuthorization"] = auth
        reply = self.transport.send(card["provider"], "card.countersign", body, files=self._files(rec, review))
        result = self._await(card["provider"], reply,
                             header_ts(card["signatures"]["1"]) + parse_duration(template["deadlines"]["respondWithin"]))
        if "reason" in result:
            return result["reason"]
        signed = result["card"]
        validate_card(template, signed)
        verify_card(template, signed, self.keys)                 # slot 2 after slot 1 and within respondWithin
        check_window(header_of(signed["signatures"]["2"]), self.now())   # retrieved, not pushed: no future-dating
        rec.update(card=signed, state="formed")
        self._save(rec)
        return signed

    # ── proposal ────────────────────────────────────────────────────────

    def draft_request(self, template: Mapping[str, Any], *, request_id: str, spec: Mapping[str, Any],
                      materials: Mapping[str, tuple[bytes, Mapping[str, Any]]], signers: Mapping[str, str],
                      delegation_scope_digest: str, version: int = 1) -> dict:
        request = build_request(template, request_id=request_id, version=version, client=self.party, signers=signers,
                                spec=spec, inputs=self._declare(materials), delegation_scope_digest=delegation_scope_digest,
                                payment_provider=self.payment_provider,
                                user=self.user_party if "user" in signers.values() else None)
        validate_request(template, {**request, "signature": {}})
        self.store.put("requests", request_digest(request), {"request": request,
                                                             "materials": {n: _b64(d) for n, (d, _x) in materials.items()}})
        return request

    def submit_request(self, request: Mapping[str, Any]) -> dict | str:
        """Pay the proposal fee if any, sign and send the Request Card; returns the drafted card or a reason."""
        template = self.template(request["templateDigest"])
        rd = request_digest(request)
        saved = self.store.get("requests", rd)
        body: dict[str, Any] = {}
        if template["pricing"].get("proposalFee", 0):
            if not saved.get("voucher"):
                saved["voucher"] = self._pay("proposal-fee", template, request=request)
                self.store.put("requests", rd, saved)
            body["voucher"] = saved["voucher"]
        signed = sign_request(request, self.signer, self.now())
        review = [n for n in request["inputs"] if material(template, n).get("reviewBeforeSigning")]
        auth = self.authorize_materials(review, request_digest_=rd)
        if auth:
            body["informationAuthorization"] = auth
        body["request"] = signed
        files = {n: base64.b64decode(saved["materials"][n]) for n in review}
        reply = self.transport.send(request["provider"], "request.submit", body, files=files)
        result = self._await(request["provider"], reply,
                             header_ts(signed["signature"]) + parse_duration(template["deadlines"]["respondWithin"]))
        if "reason" in result:
            return result["reason"]
        card = result["card"]
        validate_card(template, card, request=signed)            # invariant 6: the Client's values and terms kept
        verify_card(template, card, self.keys, request_ts=header_ts(signed["signature"]))   # slot 3 in time
        check_window(header_of(card["signatures"]["3"]), self.now())
        self.store.put("cards", card_digest(card), {"card": card, "request": signed, "materials": saved["materials"],
                                                    "state": "offered"})
        return card

    def adopt(self, card: Mapping[str, Any]) -> dict:
        """Adopt a drafted proposal card: slot 1."""
        return self.sign(card)

    # ── start ───────────────────────────────────────────────────────────

    def _pay(self, purpose: str, template, *, card=None, request=None, period=None, termination=None) -> dict:
        if self.payment is None:
            fail(CARD_INVALID, f"a {purpose} payment is due but no payment port is configured")
        return self.payment.pay(purpose=purpose, template=template, card=card, request=request, period=period,
                                termination=termination,
                                deposit_paid=bool(self._record(card_digest(card)).get('depositVoucher'))
                                if purpose == 'termination' else False)

    @_serialized_card
    def start(self, card: Mapping[str, Any]) -> dict:
        """Pay the deposit (if any), send the materials and open the delivery task.
        Returns ``{"receipt": …}`` or ``{"rejection": …}``."""
        template = self.template(card["templateDigest"])
        cd = card_digest(card)
        rec = self._record(cd)
        if rec.get("state") in ("started", "lapsed", "terminated"):
            fail(OUT_OF_ORDER, f"card is {rec['state']}")
        chain = verify_card(template, card, self.keys)
        if chain.effective is None:
            fail(OUT_OF_ORDER, "card is not effective")
        if chain.effective > parse_time(template["validUntil"]):
            fail(TEMPLATE_EXPIRED, "template expired before the card took effect")
        if self.now() > chain.effective + parse_duration(template["deadlines"]["startWithin"]):
            fail(DEADLINE_PASSED, "startWithin passed; the card has lapsed")
        body: dict[str, Any] = {"card": card}
        if Decimal(str(card["derived"]["deposit"])) > 0:
            body["voucher"] = rec.get("depositVoucher") or self._pay("deposit", template, card=card)
            rec["depositVoucher"] = body["voucher"]
            self._save(rec)
        names = [n for n in card["inputs"] if not material(template, n).get("reviewBeforeSigning")]
        auth = self.authorize_materials(names, card_digest_=cd)
        if auth:
            body["informationAuthorization"] = auth
        reply = self.transport.send(card["provider"], "work.start", body, files=self._files(rec, names))
        result = self._await(card["provider"], reply, chain.effective + parse_duration(template["deadlines"]["startWithin"]))
        if "rejection" in result:
            rej = result["rejection"]
            h = objects.verify_signed(rej, self.keys.jwk(template["provider"], header_of(rej["signature"]).get("kid")),
                                      signer=template["provider"], expect_kid=True)
            check_window(h, self.now(), not_before=chain.effective)
            if rej["cardDigest"] != cd:
                fail(INVALID_OBJECT, "rejection is for another card")
            rec["rejection"] = rej
            if rej["reason"] == "declaration-mismatch":
                rec["state"] = "lapsed"
            self._save(rec)
            return {"rejection": rej}
        receipt = result["receipt"]
        h = objects.verify_signed(receipt, self.keys.jwk(template["provider"], header_of(receipt["signature"]).get("kid")),
                                  signer=template["provider"], expect_kid=True)
        check_window(h, self.now(), not_before=chain.effective)
        if receipt["cardDigest"] != cd:
            fail(INVALID_OBJECT, "receipt is for another card")
        card = copy.deepcopy(card)
        card["fulfillment"]["receipt"] = receipt
        verify_card(template, card, self.keys)
        rec.update(card=card, state="started", task=reply.task_id, notices=[], steps=[])
        self._save(rec)
        return {"receipt": receipt}

    # ── delivery ────────────────────────────────────────────────────────

    @_serialized_card
    def watch(self, cd: str, market: str) -> str:
        reply = self.transport.send(market, "anchor.watch", {"cardDigest": cd})
        rec = self._record(cd)
        rec["watch"] = {"market": market, "task": reply.task_id}
        self._save(rec)
        return reply.task_id

    @_serialized_card
    def poll(self, cd: str) -> list[dict]:
        """New events: ``anchored`` (slot 5(i) notices) and ``step-request``."""
        rec = self._record(cd)
        template = self.template(rec["card"]["templateDigest"])
        events = []
        if rec.get("watch"):
            task = self.transport.get_task(rec["watch"]["market"], rec["watch"]["task"])
            seen = {n["period"] for n in rec.get("notices", [])}
            market = header_of(template["signatures"]["market"])["a2c-signer"]
            for n in task.artifacts:
                if n["period"] not in seen:
                    h = header_of(n["signature"])
                    verify(n["signature"], cd, self.keys.jwk(market, h.get("kid")), slot="5", signer=market,
                           period=n["period"], manifest=h.get("a2c-manifest"), expect_kid=True)
                    rec.setdefault("notices", []).append(n)
                    events.append({"type": "anchored", "period": n["period"]})
        if rec.get("task"):
            task = self.transport.get_task(rec["card"]["provider"], rec["task"])
            if task.state == "auth-required" and task.status:
                sr = task.status["stepRequest"]
                h = objects.verify_signed(sr, self.keys.jwk(template["provider"], header_of(sr["signature"]).get("kid")),
                                          signer=template["provider"], expect_kid=True)
                known = [s["request"] for s in rec.get("steps", [])]
                if sr not in known:
                    check_window(h, self.now(), not_before=self._last_known_ts(rec))
                    if sr["step"] not in [s["name"] for s in template.get("authorizableSteps", [])]:
                        fail(CARD_INVALID, "step is not declared in the template")
                    rec.setdefault("steps", []).append({"request": sr, "answer": None})
                    events.append({"type": "step-request", "stepRequest": sr})
        self._save(rec)
        return events

    @_serialized_card
    def answer_step(self, cd: str, granted: bool) -> dict:
        """The user's Step Authorization (granted or refused) for the waiting step request."""
        rec = self._record(cd)
        pending = [s for s in rec.get("steps", []) if s["answer"] is None]
        if not pending:
            fail(OUT_OF_ORDER, "no step request is waiting")
        user = self._user()
        sa = objects.signed(objects.new("step-authorization", stepRequestDigest=objects.object_digest(pending[-1]["request"]),
                                        granted=granted, jwk=user.inline_jwk), user, self.now())
        self.transport.send(rec["card"]["provider"], "step.authorize", {"stepAuthorization": sa}, task_id=rec["task"])
        pending[-1]["answer"] = sa
        self._save(rec)
        return sa

    @_serialized_card
    def fetch_delivery(self, cd: str, period: int) -> dict[str, bytes]:
        """Fetch period ``period``, check the files against the manifest, sign slot 6(i) and notify."""
        rec = self._record(cd)
        if rec.get("state") != "started":
            fail(OUT_OF_ORDER, f"card is {rec.get('state')}")
        template = self.template(rec["card"]["templateDigest"])
        reply = self.transport.send(rec["card"]["provider"], "delivery.fetch", {"cardDigest": cd, "period": period},
                                    task_id=rec.get("task"))
        card = reply.body["card"]
        if card_digest(card) != cd:
            fail(INVALID_OBJECT, "provider returned another card")
        validate_card(template, card)
        verify_card(template, card, self.keys)
        if len(card["signatures"]["5"]) < period:
            fail(OUT_OF_ORDER, f"period {period} is not anchored")
        manifest = period_manifest(card, period)
        want = {f["name"]: f["digest"] for f in manifest["files"]}
        got = {n: digest_bytes(b) for n, b in reply.files.items()}
        if want != got:
            fail(INVALID_OBJECT, "delivered files do not match the manifest")
        card = self._merge(rec["card"], card)
        card = sign_slot(card, 6, self.signer, self.now(), period=period)
        rec["card"] = card
        rec.setdefault("deliveries", {})[str(period)] = {n: _b64(b) for n, b in reply.files.items()}
        self._save(rec)
        self.transport.send(card["provider"], "acceptance.notify", {"card": card})
        return dict(reply.files)

    def _merge(self, mine: Mapping[str, Any], theirs: Mapping[str, Any]) -> dict:
        """Take the provider's copy (it carries the new slots) but keep our own fulfillment additions."""
        card = copy.deepcopy(dict(theirs))
        for i, p in enumerate(mine["fulfillment"].get("periods", [])):
            for k in ("report", "attestations"):
                if k in p and i < len(card["fulfillment"].get("periods", [])):
                    card["fulfillment"]["periods"][i].setdefault(k, p[k])
        return card

    def _run_period(self, cd: str, period: int):
        rec = self._record(cd)
        if rec.get("state") != "started":
            fail(OUT_OF_ORDER, f"card is {rec.get('state')}")
        card = copy.deepcopy(rec["card"])
        template = self.template(card["templateDigest"])
        if len(card["signatures"]["6"]) != period or len(card["signatures"]["7"]) >= period:
            fail(OUT_OF_ORDER, f"period {period} is not waiting for acceptance")
        chain = verify_card(template, card, self.keys)
        d = deliverable_of(template, period)
        refs = acceptance_of(template, card)[d]["verifiers"]
        files = {n: base64.b64decode(b) for n, b in rec["deliveries"][str(period)].items()}
        manifest = period_manifest(card, period)
        result = run_target(refs, fetch=self.fetch, template=template, card=card, target=d, files=files, period=period,
                            anchored_at=fmt_time(chain.period_ts(5, period)), manifest_digest=objects.object_digest(manifest),
                            card_digest=cd, model=self.model, attest=self._attest)
        return rec, card, template, result

    @_serialized_card
    def verify(self, cd: str, period: int):
        """Run the period's verifiers and sign nothing, so that a User who signs slot 7 sees the result first."""
        return self._run_period(cd, period)[3]

    @_serialized_card
    def accept(self, cd: str, period: int, *, provenance: Mapping[str, Any] | None = None) -> Acceptance:
        """Run the period's verifiers. On pass: report, slot 7(i), notify, pay. On fail: signed report, notify.
        On pending: nothing is signed; call again after ``result.not_before``."""
        rec, card, template, result = self._run_period(cd, period)
        if result.status == "pending":
            return Acceptance(result.status, card, result)          # nothing signed; run again after not_before
        rep = objects.signed(report(cd, result, period=period), self.signer, self.now())
        slot = card["fulfillment"]["periods"][period - 1]
        slot["report"] = rep
        attestations = [a for r in result.runs for a in r.attestations]
        if attestations:
            slot["attestations"] = attestations
        if result.status == "pass":
            card = sign_slot(card, 7, self._slot_signer(card, 7), self.now(), period=period)
        rec["card"] = card
        self._save(rec)
        body = {"card": card}
        if provenance:
            body["provenance"] = provenance
        self.transport.send(card["provider"], "acceptance.notify", body)
        voucher = None
        if result.status == "pass":
            voucher = self._pay_accepted(cd, period, acceptance_notified=True)
        return Acceptance(result.status, card, result, voucher)

    def _save_installment(self, cd: str, period: int, voucher: dict, *, notified: bool = False) -> None:
        rec = self._record(cd)
        if voucher not in rec.setdefault("vouchers", []):
            rec["vouchers"].append(voucher)
        if notified and period not in rec.setdefault("paymentNotifiedPeriods", []):
            rec["paymentNotifiedPeriods"].append(period)
        self._save(rec)

    def pay(self, cd: str, period: int) -> dict | None:
        """Recover one already-accepted installment without verifying or signing it again.

        The payment port owns the exact original attempt and its known/unknown
        result. A failed notification leaves the verified voucher saved locally.
        """
        with self._card_lock(cd):
            return self._pay_accepted(cd, period)

    def _pay_accepted(self, cd: str, period: int, *, acceptance_notified: bool = False) -> dict | None:
        rec = self._record(cd)
        card = rec["card"]
        template = self.template(card["templateDigest"])
        chain = verify_card(template, card, self.keys)
        if card["client"] != self.party:
            fail(BAD_SIGNATURE, "the accepted card belongs to another client")
        if type(period) is not int or not 1 <= period <= chain.accepted:
            fail(OUT_OF_ORDER, "payment requires this original accepted period")
        amount = card["derived"]["periods"][period - 1]["amount"]
        if Decimal(str(amount)) == 0:
            return None
        voucher = next((v for v in rec.get("vouchers", [])
                        if v.get("purpose") == "installment" and v.get("period") == period), None)
        if voucher is None:
            if not acceptance_notified:
                # Continue the exact original facts, never another report/slot7.
                self.transport.send(card["provider"], "acceptance.notify", {"card": card})
            voucher = self._pay("installment", template, card=card, period=period)
        verify_voucher(voucher, self.keys, payment_provider=card["paymentProvider"],
                       purpose="installment", amount=amount, currency=template["pricing"]["currency"],
                       card_digest=cd, period=period)
        self._save_installment(cd, period, voucher)
        if period not in rec.get("paymentNotifiedPeriods", []):
            notified = self.transport.send(card["provider"], "payment.notify", {"voucher": voucher})
            if notified.body.get('ok') is not True:
                fail(INVALID_OBJECT, 'the original payment notification has no positive receipt')
            self._save_installment(cd, period, voucher, notified=True)
        return voucher

    def _attest(self, ref, service, cd, period, manifest_digest, values, files):
        body = {"verifier": ref["id"], "cardDigest": cd, "period": period, "manifestDigest": manifest_digest,
                "values": values}
        reply = self.transport.send(service, "attestation.request", body, files=files)
        result = self._await(service, reply, None)
        if "attestation" not in result:
            fail(INVALID_OBJECT, f"no attestation: {result.get('reason')}")
        a = result["attestation"]
        h = header_of(a["signature"])
        if h["a2c-signer"] != service:
            fail(BAD_SIGNATURE, "attestation is not from the declared service")
        objects.verify_signed(a, self.keys.jwk(service, h.get("kid")), signer=service, expect_kid=True)
        if (a["verifier"], a["cardDigest"], a["period"], a["manifestDigest"]) != (ref["id"], cd, period, manifest_digest):
            fail(INVALID_OBJECT, "attestation is for another verifier, card, period or manifest")
        return a, h["a2c-ts"]

    # ── termination ─────────────────────────────────────────────────────

    @_serialized_card
    def termination_quote(self, cd: str) -> dict:
        rec = self._record(cd)
        template = self.template(rec["card"]["templateDigest"])
        accepted, n = len(rec["card"]["signatures"]["7"]), len(template["deliverables"])
        if accepted >= n or rec.get("state") in ("terminated", "lapsed"):
            fail(OUT_OF_ORDER, "nothing to terminate")
        quote = self.transport.send(rec["card"]["provider"], "termination.quote", {"cardDigest": cd}).body["terminationQuote"]
        h = verify_quote(template, rec['card'], quote, self.keys)
        check_fresh(h, self.now())
        rec["quote"] = quote
        self._save(rec)
        return quote

    @_serialized_card
    def terminate(self, cd: str, *, quote: Mapping[str, Any] | None = None) -> dict:
        """Terminate: with ``quote`` it is agreed (balance settled); without, unilateral."""
        rec = self._record(cd)
        card = rec['card']; template = self.template(card['templateDigest'])
        pending = rec.get('terminationPending')
        if pending:
            if pending['quote'] != quote:
                fail(OUT_OF_ORDER, 'resume the exact original termination decision')
        else:
            chain = verify_card(template, card, self.keys)
            if chain.effective is None or chain.accepted >= len(template['deliverables']) or rec.get('state') in ('terminated','closed','lapsed'):
                fail(OUT_OF_ORDER, 'there is no active unaccepted work to terminate')
            body = {'cardDigest':cd}
            if quote is not None:
                header = verify_quote(template, card, quote, self.keys)
                if not parse_time(header['a2c-ts']) <= self.now() <= parse_time(header['a2c-ts']) + parse_duration(template['deadlines']['respondWithin']):
                    fail(DEADLINE_PASSED, 'the original termination quote response window ended')
                body['terminationQuoteDigest'] = objects.object_digest(quote)
            term = objects.signed(objects.new('termination', **body), self.signer, self.now())
            card = copy.deepcopy(card)
            card['fulfillment']['termination'] = {'termination':term, **({'quote':copy.deepcopy(quote)} if quote is not None else {})}
            verify_card(template, card, self.keys)
            pending = {'termination':term,'quote':copy.deepcopy(quote),'notified':False}
            rec.update(card=card,state='terminated',terminationPending=pending)
            self._save(rec)  # Signing is effective even if the delivery receipt is lost.
        term = pending['termination']
        if not pending['notified']:
            reply = self.transport.send(card['provider'], 'termination.notify', {'termination':term})
            if reply.body.get('ok') is not True: fail(INVALID_OBJECT, 'original termination was not acknowledged')
            pending = {**pending,'notified':True}; rec['terminationPending'] = pending; self._save(rec)
        if quote is not None:
            balance = termination_balance(template,card,card['fulfillment']['termination'],self.keys,
                                          deposit_paid=bool(rec.get('depositVoucher')))
            if balance > 0:
                voucher = rec.get('terminationVoucher')
                if voucher is None:
                    voucher = self._pay('termination',template,card=card,termination=card['fulfillment']['termination'])
                    rec['terminationVoucher'] = voucher; self._save(rec)
                if not pending.get('paymentNotified'):
                    reply = self.transport.send(card['provider'],'payment.notify',{'voucher':voucher})
                    if reply.body.get('ok') is not True: fail(INVALID_OBJECT, 'original termination payment was not acknowledged')
                    rec['terminationPending'] = {**pending,'paymentNotified':True}; self._save(rec)
        return term

    def refund_checkout(self, cd: str) -> str:
        """Issue only this terminated card's negative-balance AP2 Checkout JWT.

        The host dispatches the original peer's ``ap2.checkout`` to this helper;
        it is not a public personal-assistant service or a new A2C operation.
        """
        from .payment.ap2 import checkout
        rec = self._record(cd); card = rec['card']; template = self.template(card['templateDigest'])
        if self.ap2_key is None or rec.get('state') not in ('terminated','closed'):
            fail(OUT_OF_ORDER, 'restore the terminated card and its AP2 payee identity')
        voucher = rec.get('depositVoucher')
        if voucher:
            verify_voucher(voucher,self.keys,payment_provider=card['paymentProvider'],purpose='deposit',
                amount=card['derived']['deposit'],currency=template['pricing']['currency'],card_digest=cd)
        balance = termination_balance(template,card,card['fulfillment'].get('termination'),self.keys,
                                      deposit_paid=bool(voucher))
        if balance >= 0: fail(OUT_OF_ORDER, 'this card has no agreed refund to the client')
        amount = -balance
        value = int(amount) if amount == amount.to_integral_value() else float(amount)
        return checkout({'id':self.signer.id,'name':self.name},self.ap2_key,purpose='termination',
            digest_ref={'cardDigest':cd},period=None,amount=value,currency=template['pricing']['currency'],
            now=int(self.now().timestamp()))

    # ── provenance and status ───────────────────────────────────────────

    def sign_provenance(self, cd: str, sample_digest: str) -> dict:
        rec = self._record(cd)
        delivered = [f["digest"] for p in rec["card"]["fulfillment"].get("periods", []) for f in p["manifest"]["files"]]
        if sample_digest not in delivered:
            fail(INVALID_OBJECT, "the sample must be a delivered file of this card")
        return objects.sign_role(objects.new("provenance", cardDigest=cd, sampleDigest=sample_digest), "client",
                                 self.signer, self.now())

    def status(self, cd: str) -> Status:
        rec = self._record(cd)
        template = self.template(rec["card"]["templateDigest"])
        chain = verify_card(template, rec["card"], self.keys)
        steps = [StepWindow(s["request"]["period"], header_ts(s["request"]["signature"]),
                            header_ts(s["answer"]["signature"]) if s["answer"] else None)
                 for s in rec.get("steps", [])]
        return status(template, rec["card"], chain, self.now(), steps, rec.get("vouchers", []))


def header_ts(sig: Mapping[str, Any]):
    return parse_time(header_of(sig)["a2c-ts"])


__all__ = ["Client", "Draft", "Acceptance"]
