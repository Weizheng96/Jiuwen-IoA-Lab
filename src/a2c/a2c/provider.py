"""Provider SDK (design: 设计/合同协议.md, 设计/线上编码.md §5).

The SDK checks, signs and stores; business decisions — whether to countersign, how to draft
a proposal, what a termination costs, when work starts — are the ``policy`` callbacks of
the provider agent.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, ContextManager

from . import objects
from .card import build_proposal_card, card_digest, request_digest, validate_card, validate_request, verify_request
from .codec import digest, digest_bytes, fmt_time, parse_duration, parse_time
from .common import Clock, Directory, Endpoint, Reply, Store, Task, Transport, now_or
from .errors import (CARD_INVALID, DEADLINE_PASSED, INVALID_OBJECT, OUT_OF_ORDER, TEMPLATE_EXPIRED, UNKNOWN_CARD,
                     fail)
from .jws import Signer, check_fresh, header_of, ts_of
from .payment import PaymentPort, verify_voucher
from .payment.terms import termination_balance
from .slots import sign_slot, sign_template, verify_card, verify_template
from .template import deliverable_names, material, template_digest, validate
from .verifier.acceptance import ModelPort, report, run_target


@dataclass(frozen=True)
class MaterialReviewScope:
    task_id: str
    op: str
    provider: str
    client: str
    subject_digest: str
    template_digest: str
    template_fingerprint: str
    body_digest: str
    materials: tuple[tuple[str, str], ...]
    information_authorization_digest: str | None
    delegation_scope_digest: str
    signature_time: str
    deadline: str
    review_plan: tuple[tuple[str, str, str, str], ...]
    request_id: str | None = None
    request_version: int | None = None


@dataclass(frozen=True)
class MaterialObservation:
    task_id: str
    op: str
    subject_digest: str
    material_name: str
    verifier_id: str
    verifier_digest: str
    ordinal: int
    need_digest: str
    files: tuple[tuple[str, str], ...]


@dataclass
class Proposal:
    """What the provider agent fills in when it drafts a card from a Request Card."""

    acceptance: Mapping[str, Any]
    accept_within: Mapping[str, str]
    line_items: Mapping[str, Any]
    card_id: str | None = None


class ProviderPolicy(Protocol):
    def review(self, template: Mapping, card: Mapping, materials: Mapping[str, bytes]) -> str | None:
        """Countersign? ``None`` accepts, a string declines with that reason."""

    def draft(self, template: Mapping, request: Mapping, materials: Mapping[str, bytes]) -> Proposal | str:
        """Draft a card from a Request Card, or decline with a reason."""

    def termination_fee(self, template: Mapping, card: Mapping, period: int) -> Any:
        """The fee quoted when the Client asks to terminate."""

    def on_start(self, template: Mapping, card: Mapping, materials: Mapping[str, bytes]) -> None:
        """Work may start: the card is effective and the receipt is issued."""


class DefaultPolicy:
    def review(self, template, card, materials):
        return None

    def draft(self, template, request, materials):
        return "this provider does not draft proposals"

    def termination_fee(self, template, card, period):
        return card["derived"]["periods"][period - 1]["amount"]

    def on_start(self, template, card, materials):
        return None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _unb64(text: str) -> bytes:
    return base64.b64decode(text)


class Provider(Endpoint):
    OPS = ("template.get", "request.submit", "card.countersign", "work.start", "step.authorize", "delivery.fetch",
           "acceptance.notify", "payment.notify", "termination.quote", "termination.notify",
           "ap2.checkout")   # the last one is the AP2 payee role, outside A2C

    def __init__(self, signer: Signer, *, market: str, keys: Directory, transport: Transport,
                 fetch: Callable[[str], bytes], clock: Clock | None = None, store: Store | None = None,
                 policy: ProviderPolicy | None = None, model: ModelPort | None = None,
                 ap2_key: Any = None, name: str = "", payment: PaymentPort | None = None,
                 material_observation_context: Callable[[MaterialObservation], ContextManager[None]] | None = None) -> None:
        super().__init__()
        self.signer = signer
        self.market = market
        self.keys = keys
        self.transport = transport
        self.fetch = fetch
        self.now = now_or(clock)
        self.store = store or Store()
        self.policy = policy or DefaultPolicy()
        self.model = model
        self.ap2_key = ap2_key
        self.name = name or signer.id
        self.payment = payment
        self._active_countersigns: set[str] = set()
        self._active_requests: set[str] = set()
        self.material_observation_context = material_observation_context

    @property
    def id(self) -> str:
        return self.signer.id

    # ── templates ───────────────────────────────────────────────────────

    def publish(self, draft: Mapping[str, Any]) -> dict:
        """Sign a template and have the Market endorse it."""
        t = {k: v for k, v in copy.deepcopy(dict(draft)).items() if k != "signatures"}
        t["provider"] = self.id
        t["templateDigest"] = template_digest(t)
        t["signatures"] = {}
        t = sign_template(t, self.signer, "provider", self.now())
        validate(t, endorsed=False)
        endorsed = self.transport.send(self.market, "template.endorse", {"template": t}).body["template"]
        validate(endorsed)
        verify_template(endorsed, self.keys)
        self.store.put("templates", endorsed["templateDigest"], endorsed)
        return endorsed

    def template(self, digest: str) -> dict:
        t = self.store.get("templates", digest)
        if t is None:
            fail(UNKNOWN_CARD, "unknown template")
        return t

    def op_template_get(self, body, files, task_id) -> Reply:
        templates = list(self.store.all("templates").values())
        if body.get("templateDigest"):
            templates = [t for t in templates if t["templateDigest"] == body["templateDigest"]]
        return Reply({"op": "template.get", "templates": templates})

    # ── helpers ─────────────────────────────────────────────────────────

    def _record(self, cd: str) -> dict:
        rec = self.store.get("cards", cd)
        if rec is None:
            fail(UNKNOWN_CARD, "unknown card")
        return rec

    def _save(self, rec: dict) -> None:
        self.store.put("cards", card_digest(rec["card"]), rec)

    def bind_contract(self, card: Mapping, *, deposit_voucher: Mapping | None = None) -> dict:
        """Bind the original effective card received by the host, without starting work.

        In a registry-mediated integration the host can receive proposal slot 1
        separately from work.start, including its already-paid deposit voucher.
        Existing progress and terminal records are never replaced; importing a
        verified payment does not start work or create a payment.
        """
        with self._lock:
            cd = card_digest(card); rec = self._record(cd); previous = rec['card']
            template = self.template(previous['templateDigest'])
            validate_card(template,card)
            if (card['provider'] != self.id or verify_card(template,card,self.keys).effective is None
                    or any(card['signatures'][slot] != previous['signatures'][slot] for slot in ('2','3'))
                    or previous['signatures']['1'] and previous['signatures']['1'] != card['signatures']['1']):
                fail(CARD_INVALID, 'bind the original effective offer without changing its signatures')
            bound = copy.deepcopy(previous)
            if not previous['signatures']['1']:
                if rec.get('state') != 'offered':
                    fail(OUT_OF_ORDER, 'the original offer can no longer bind a new acceptance')
                if card['fulfillment'] or any(card['signatures'][str(slot)] for slot in range(4,8)):
                    fail(OUT_OF_ORDER, 'bind the initial effective card without importing execution progress')
                bound['signatures']['1'] = copy.deepcopy(card['signatures']['1'])
                verify_card(template,bound,self.keys)
            if deposit_voucher is not None:
                if Decimal(str(card['derived']['deposit'])) <= 0:
                    fail(CARD_INVALID, 'zero deposit requires no payment voucher')
                verify_voucher(deposit_voucher,self.keys,payment_provider=card['paymentProvider'],purpose='deposit',
                    amount=card['derived']['deposit'],currency=template['pricing']['currency'],card_digest=cd)
                existing = rec.get('depositVoucher')
                if existing is not None and existing != deposit_voucher:
                    fail(OUT_OF_ORDER, 'retain the original deposit voucher without replacing it')
                if existing is None and rec.get('state') == 'closed':
                    fail(OUT_OF_ORDER, 'reconcile the closed record before importing missing payment evidence')
            changed = bound != previous or deposit_voucher is not None and rec.get('depositVoucher') != deposit_voucher
            if changed:
                rec.update(card=bound,**({'state':'formed'} if not previous['signatures']['1'] else {}))
                if deposit_voucher is not None: rec['depositVoucher'] = copy.deepcopy(deposit_voucher)
                self._save(rec)
            return bound

    def _seal_material_review(self, task_id, op, template, body, files, names):
        verify_template(template, self.keys)
        trusted = {}
        for role, signature in template["signatures"].items():
            if role not in ("provider", "market"):
                continue
            header = header_of(signature)
            actor = template["provider"] if role == "provider" else header["a2c-signer"]
            trusted.setdefault(actor, []).append(self.keys.jwk(actor, header.get("kid")))
        subject = body["request"] if op == "request.submit" else body["card"]
        signature = subject["signature"] if op == "request.submit" else subject["signatures"]["1"]
        seal = {"taskId": task_id, "op": op, "provider": self.id,
                "client": subject["client"]["id"], "template": copy.deepcopy(template),
                "templateFingerprint": digest(template), "templateKeys": trusted,
                "bodyDigest": digest(body), "files": {n: digest_bytes(files[n]) for n in names if n in files},
                "reviewNames": list(names), "deadline": fmt_time(min(parse_time(template["validUntil"]),
                    ts_of(signature) + parse_duration(template["deadlines"]["respondWithin"]))) }
        return {**seal, "fingerprint": digest(seal)}

    def _sealed_template(self, record, op, task_id):
        subject = record["body"]["request"] if op == "request.submit" else record["body"]["card"]
        seal = record.get("material_seal")
        if seal is None:
            # Existing reviewed-only records retain their original public contract.
            return self.template(subject["templateDigest"])
        if (seal["fingerprint"] != digest({k: v for k, v in seal.items() if k != "fingerprint"})
                or seal["op"] != op or seal["taskId"] != task_id or seal["provider"] != self.id
                or subject["provider"] != self.id or seal["client"] != subject["client"]["id"]
                or seal["bodyDigest"] != digest(record["body"])):
            fail(OUT_OF_ORDER, "the original material review snapshot has changed")
        template = seal["template"]
        if (digest(template) != seal["templateFingerprint"]
                or template_digest(template) != subject["templateDigest"]
                or template["templateDigest"] != subject["templateDigest"]):
            fail(OUT_OF_ORDER, "the sealed original template has changed")
        validate(template)
        keys = Directory()
        for actor, jwks in seal["templateKeys"].items():
            keys.add(actor, {"keys": jwks})
        verify_template(template, keys)
        files = {name: digest_bytes(_unb64(value)) for name, value in record["files"].items()}
        if files != seal["files"]:
            fail(OUT_OF_ORDER, "the sealed original material bytes have changed")
        return template

    def _material_review_context(self, task_id):
        request = self.store.get("pending_requests", task_id)
        counter = self.store.get("pending_countersigns", task_id)
        if bool(request) == bool(counter):
            fail(OUT_OF_ORDER, "one original material review task is required")
        record = request or counter
        if not record.get("material_seal"):
            fail(OUT_OF_ORDER, "the original material snapshot needs explicit verified migration")
        if record["reviewed"]:
            fail(OUT_OF_ORDER, "use the original reviewed-only recovery interface")
        op = "request.submit" if request else "card.countersign"
        if request:
            record, template, task = self._request_context(task_id)
            subject = record["body"]["request"]; subject_digest = request_digest(subject)
        else:
            record, template, task, _, _ = self._countersign_context(task_id)
            subject = record["body"]["card"]; subject_digest = card_digest(subject)
        seal = record["material_seal"]
        names = [n for n in subject["inputs"] if material(template, n).get("reviewBeforeSigning")]
        if set(names) != set(seal["reviewNames"]) or set(record["files"]) != set(names):
            fail(OUT_OF_ORDER, "the complete original review materials are required")
        for name in names:
            if digest_bytes(_unb64(record["files"][name])) != subject["inputs"][name]["digest"]:
                fail(OUT_OF_ORDER, "original material declaration differs from its bytes")
        auth = record["body"].get("informationAuthorization")
        if names:
            if not auth:
                fail(INVALID_OBJECT, "original material authorization is required")
            objects.verify_signed(auth, auth["jwk"])
            bound = "requestDigest" if request else "cardDigest"
            if auth.get(bound) != subject_digest or not set(names) <= set(auth["inputs"]):
                fail(INVALID_OBJECT, "original authorization does not cover the materials")
        signature = subject["signature"] if request else subject["signatures"]["1"]
        if any(k == "skill" for n in names for k in (material(template, n).get("verifiers") or {})):
            if not callable(self.material_observation_context):
                fail(OUT_OF_ORDER, "original material observation context is required")
        scope = MaterialReviewScope(task.id, op, self.id, subject["client"]["id"], subject_digest,
            subject["templateDigest"], seal["templateFingerprint"], seal["bodyDigest"],
            tuple(sorted(seal["files"].items())), digest(auth) if auth else None,
            subject["delegationScopeDigest"], header_of(signature)["a2c-ts"], seal["deadline"],
            tuple((n, k, ref["id"], ref["digest"]) for n in sorted(names)
                  for k, ref in sorted((material(template, n).get("verifiers") or {}).items())),
            subject.get("requestId"), subject.get("version"))
        if self.now() > parse_time(scope.deadline):
            fail(DEADLINE_PASSED, "the original material review deadline has passed")
        return scope, task

    def resume_material_review(self, task_id: str, *, guard: Callable[[MaterialReviewScope], None]) -> Reply:
        """Prepare only the original unreviewed task; the trusted guard performs local qualification."""
        if not callable(guard):
            fail(INVALID_OBJECT, "a trusted local material review guard is required")
        with self._lock:
            scope, task = self._material_review_context(task_id)
            guard(scope)
            def finish():
                current, _ = self._material_review_context(task_id)
                if current != scope:
                    fail(OUT_OF_ORDER, "the prepared original material scope has changed")
                guard(current)
                return (self._finish_request if current.op == "request.submit" else self._finish_countersign)(task_id)
            return Reply({"op": scope.op}, task_id=task.id, state=task.state, then=finish)

    def _material_observation(self, task_id):
        def context(name, ref, ordinal, need):
            request = self.store.get("pending_requests", task_id)
            record = request or self.store.get("pending_countersigns", task_id)
            op = "request.submit" if request else "card.countersign"
            subject = record["body"]["request"] if request else record["body"]["card"]
            self._sealed_template(record, op, task_id)
            value = MaterialObservation(task_id, op, request_digest(subject) if request else card_digest(subject),
                name, ref["id"], ref["digest"], ordinal, digest(need),
                ((name, digest_bytes(_unb64(record["files"][name]))),))
            return self.material_observation_context(value)
        return context

    def _materials(self, template, decl: Mapping[str, Any], files: Mapping[str, bytes], names: list[str], *,
                   auth: Mapping[str, Any] | None, bound: tuple[str, str], card_like: Mapping[str, Any],
                   task_id: str | None = None):
        """Check the materials sent now: authorization, digests, declarations. Returns
        (rejection reason, failing names, input report) or None when they all fit."""
        if not names:
            return None
        if auth is None:
            fail(INVALID_OBJECT, "materials need an information authorization")
        objects.verify_signed(auth, auth["jwk"])
        if auth.get(bound[0]) != bound[1] or not set(names) <= set(auth["inputs"]):
            fail(INVALID_OBJECT, "information authorization does not cover these materials")
        wrong = [n for n in names if n not in files or digest_bytes(files[n]) != decl[n]["digest"]]
        if wrong:
            return "digest-mismatch", wrong, None
        for n in names:
            refs = material(template, n).get("verifiers")
            if not refs:
                continue
            result = run_target(refs, fetch=self.fetch, template=template, card=card_like, target=n,
                                files={n: files[n]}, model=self.model, observation_context=(
                                    self._material_observation(task_id) if task_id and self.material_observation_context else None))
            if result.status != "pass":
                return "declaration-mismatch", [n], report(bound[1], result, input=n)
        return None

    def _sign(self, kind: str, **body) -> dict:
        return objects.signed(objects.new(kind, **body), self.signer, self.now())

    # ── formation ───────────────────────────────────────────────────────

    def op_request_submit(self, body, files, task_id) -> Reply:
        request = body["request"]
        template = self.template(request["templateDigest"])
        validate_request(template, request)
        h = verify_request(request)
        check_fresh(h, self.now())
        key = f"{request['client']['id']} {request['requestId']}"
        last = self.store.get("requests", key)
        if last is not None and request["version"] <= last["version"]:
            fail(OUT_OF_ORDER, "a revised Request Card needs a higher version")
        rd = request_digest(request)
        fee = template["pricing"].get("proposalFee", 0)
        if fee:
            verify_voucher(body.get("voucher") or fail(CARD_INVALID, "a proposal fee voucher is required"), self.keys,
                           payment_provider=request["paymentProvider"], purpose="proposal-fee", amount=fee,
                           currency=template["pricing"]["currency"], request_digest=rd)
        task = self.new_task("request.submit")
        review = [n for n in request["inputs"] if material(template, n).get("reviewBeforeSigning")]
        self.store.put("pending_requests", task.id, {"body": copy.deepcopy(body),
                       "files": {n: _b64(files[n]) for n in review if n in files},
                       "reviewed": False, "draftReturned": False,
                       "material_seal": self._seal_material_review(task.id, "request.submit", template, body, files, review)})
        self.store.put("requests", key, {"version": request["version"], "task": task.id})
        return Reply({"op": "request.submit"}, task_id=task.id, state=task.state,
                     then=lambda: self._finish_request(task.id))

    def _request_context(self, task_id: str, *, reviewed: bool = False):
        record = self.store.get("pending_requests", task_id)
        if not record:
            fail(UNKNOWN_CARD, "the original prepared request is unavailable")
        request = record["body"]["request"]
        key = f"{request['client']['id']} {request['requestId']}"
        latest = self.store.get("requests", key)
        if request["provider"] != self.id or latest != {"version": request["version"], "task": task_id}:
            fail(OUT_OF_ORDER, "the original request has been superseded or belongs to another provider")
        if (record.get("terminal") or task_id in self._active_requests or record["draftReturned"]
                or reviewed and not record["reviewed"]):
            fail(OUT_OF_ORDER, "reconcile the original task; its drafting boundary cannot be replayed")
        template = self._sealed_template(record, "request.submit", task_id)
        validate_request(template, request)
        verify_request(request)
        deadline = min(parse_time(template["validUntil"]),
                       ts_of(request["signature"]) + parse_duration(template["deadlines"]["respondWithin"]))
        if self.now() > deadline:
            fail(DEADLINE_PASSED, "the original drafting deadline has passed")
        task = self.tasks.get(task_id)
        if task is not None and (task.op != "request.submit" or task.state != "working"):
            fail(OUT_OF_ORDER, "the original request task is no longer working")
        if task is None:
            task = self.tasks[task_id] = Task(task_id, "request.submit")
        return record, template, task

    def resume_request(self, task_id: str) -> Reply:
        """Prepare the original deferred draft, only after the host reconciles its business effects.

        This does not run the policy. The binding finishes the returned reply under its original
        execution ownership. It must establish that the old callback has stopped; a restart alone
        is not such proof. Returned decisions and signing are never replayed by this API.
        """
        with self._lock:
            _, _, task = self._request_context(task_id, reviewed=True)
            return Reply({"op": "request.submit"}, task_id=task.id, state=task.state,
                         then=lambda: self._finish_request(task.id))

    def _finish_request(self, task_id: str) -> Reply:
        record, template, task = self._request_context(task_id)
        self._active_requests.add(task_id)
        try:
            body = record["body"]; request = body["request"]; rd = request_digest(request)
            files = {name: _unb64(value) for name, value in record["files"].items()}
            review = [n for n in request["inputs"] if material(template, n).get("reviewBeforeSigning")]
            problem = None
            if not record["reviewed"]:
                problem = self._materials(template, request["inputs"], files, review, auth=body.get("informationAuthorization"),
                                          bound=("requestDigest", rd), card_like=request, task_id=task.id)
                if not problem:
                    record = {**record, "reviewed": True}
                    self.store.put("pending_requests", task.id, record)
            decision = (f"materials do not fit: {problem[0]} {problem[1]}" if problem
                        else self.policy.draft(template, request, {n: files[n] for n in review}))
            self.store.put("pending_requests", task.id, {**record, "draftReturned": True})
            if isinstance(decision, str):
                task.state = "completed"
                task.artifacts.append({"reason": decision})
                return Reply({"op": "request.submit", "reason": decision}, task_id=task.id, state=task.state)
            card = build_proposal_card(template, request, card_id=decision.card_id or f"{request['requestId']}-v{request['version']}",
                                           acceptance=decision.acceptance, accept_within=decision.accept_within,
                                           line_items=decision.line_items)
            validate_card(template, card, request=request)
            card = sign_slot(card, 2, self.signer, self.now())
            card = self.transport.send(self.market, "card.endorse", {"card": card}).body["card"]
            verify_card(template, card, self.keys, request_ts=ts_of(request["signature"]))   # slot 3 in time
            check_fresh(header_of(card["signatures"]["3"]), self.now())
            self._save({"card": card, "request": request, "review": {n: _b64(files[n]) for n in review},
                            "state": "offered", "task": task.id})
            task.state = "completed"
            task.artifacts.append({"card": card})
            return Reply({"op": "request.submit", "card": card}, task_id=task.id, state=task.state)
        finally:
            self._active_requests.discard(task_id)

    def op_card_countersign(self, body, files, task_id) -> Reply:
        card = body["card"]
        template = self.template(card["templateDigest"])
        if template["formation"] != "countersign":
            fail(CARD_INVALID, "countersign is for countersign templates")
        validate_card(template, card)
        if card["signatures"]["1"] is None or card["signatures"]["2"] is not None:
            fail(OUT_OF_ORDER, "countersign needs slot 1 and no slot 2")
        verify_card(template, card, self.keys)
        h1 = header_of(card["signatures"]["1"])
        check_fresh(h1, self.now())
        if self.now() > parse_time(h1["a2c-ts"]) + parse_duration(template["deadlines"]["respondWithin"]):
            fail(DEADLINE_PASSED, "respondWithin passed")
        if self.now() > parse_time(template["validUntil"]):
            fail(TEMPLATE_EXPIRED, "template expired")
        cd = card_digest(card)
        task = self.new_task("card.countersign")
        review = [n for n in card["inputs"] if material(template, n).get("reviewBeforeSigning")]
        self.store.put("pending_countersigns", task.id, {
            "op": "card.countersign", "taskId": task.id,
            "body": copy.deepcopy(body), "bodyDigest": digest(body),
            "cardDigest": cd, "provider": self.id, "client": card["client"]["id"],
            "files": {n: _b64(files[n]) for n in review if n in files},
            "reviewed": False, "decisionReturned": False,
            "material_seal": self._seal_material_review(task.id, "card.countersign", template, body, files, review),
        })
        return Reply({"op": "card.countersign"}, task_id=task.id, state=task.state,
                     then=lambda: self._finish_countersign(task.id))

    def _countersign_context(self, task_id: str, *, reviewed: bool = False):
        record = self.store.get("pending_countersigns", task_id)
        if not record:
            fail(UNKNOWN_CARD, "the original prepared countersign is unavailable")
        body = record["body"]; card = body["card"]; cd = card_digest(card)
        if (record["op"] != "card.countersign" or record["taskId"] != task_id
                or record["provider"] != self.id or card["provider"] != self.id
                or record["client"] != card["client"]["id"] or record["cardDigest"] != cd
                or record["bodyDigest"] != digest(body)):
            fail(OUT_OF_ORDER, "the original countersign identity or card has changed")
        if (record.get("terminal") or task_id in self._active_countersigns or record["decisionReturned"]
                or reviewed and not record["reviewed"]):
            fail(OUT_OF_ORDER, "reconcile the original task; its decision boundary cannot be replayed")
        template = self._sealed_template(record, "card.countersign", task_id)
        if template["formation"] != "countersign":
            fail(CARD_INVALID, "countersign is for countersign templates")
        validate_card(template, card)
        if card["signatures"]["1"] is None or card["signatures"]["2"] is not None:
            fail(OUT_OF_ORDER, "countersign needs original slot 1 and no slot 2")
        # The original slot 1 carries its signed party's public JWK in the card.
        # A fresh directory guarantees that malformed extra signatures cannot fetch keys.
        if card["signatures"]["3"] is not None or any(card["signatures"][str(n)] for n in (4, 5, 6, 7)):
            fail(OUT_OF_ORDER, "only the original slot 1 may precede a countersign decision")
        verify_card(template, card, Directory())
        if self.store.get("cards", cd) is not None:
            fail(OUT_OF_ORDER, "reconcile the original stored card instead of countersigning again")
        if self.now() > ts_of(card["signatures"]["1"]) + parse_duration(template["deadlines"]["respondWithin"]):
            fail(DEADLINE_PASSED, "the original countersign deadline has passed")
        if self.now() > parse_time(template["validUntil"]):
            fail(TEMPLATE_EXPIRED, "template expired")
        files = {name: _unb64(value) for name, value in record["files"].items()}
        review = [n for n in card["inputs"] if material(template, n).get("reviewBeforeSigning")]
        if record["reviewed"] and (set(files) != set(review) or any(
                digest_bytes(files[n]) != card["inputs"][n]["digest"] for n in review)):
            fail(OUT_OF_ORDER, "the originally reviewed materials have changed")
        task = self.tasks.get(task_id)
        if task is not None and (task.op != "card.countersign" or task.state != "working"):
            fail(OUT_OF_ORDER, "the original countersign task is no longer working")
        if task is None:
            task = self.tasks[task_id] = Task(task_id, "card.countersign")
        return record, template, task, files, review

    def resume_countersign(self, task_id: str) -> Reply:
        """Prepare the original reviewed task after the host reconciles its execution effects.

        No policy, model, network operation or signature runs here. The host must prove that
        the old callback has stopped and its effects are known before finishing this reply.
        Returned decisions and uncertain signing must be reconciled, never replayed.
        """
        with self._lock:
            _, _, task, _, _ = self._countersign_context(task_id, reviewed=True)
            return Reply({"op": "card.countersign"}, task_id=task.id, state=task.state,
                         then=lambda: self._finish_countersign(task.id))

    def _finish_countersign(self, task_id: str) -> Reply:
        record, template, task, files, review = self._countersign_context(task_id)
        self._active_countersigns.add(task_id)
        try:
            body = record["body"]; card = body["card"]; cd = record["cardDigest"]
            problem = None
            if not record["reviewed"]:
                problem = self._materials(template, card["inputs"], files, review,
                                          auth=body.get("informationAuthorization"),
                                          bound=("cardDigest", cd), card_like=card, task_id=task.id)
                if not problem:
                    record = {**record, "reviewed": True}
                    self.store.put("pending_countersigns", task.id, record)
            reason = (f"materials do not fit: {problem[0]} {problem[1]}" if problem
                      else self.policy.review(template, card, {n: files[n] for n in review}))
            self.store.put("pending_countersigns", task.id, {**record, "decisionReturned": True})
            if reason is not None:
                task.state = "completed"
                task.artifacts.append({"reason": reason})
                return Reply({"op": "card.countersign", "reason": reason}, task_id=task.id, state=task.state)
            signed = sign_slot(card, 2, self.signer, self.now())
            self._save({"card": signed, "review": {n: _b64(files[n]) for n in review}, "state": "formed", "task": task.id})
            task.state = "completed"
            task.artifacts.append({"card": signed})
            return Reply({"op": "card.countersign", "card": signed}, task_id=task.id, state=task.state)
        finally:
            self._active_countersigns.discard(task_id)

    def _fail_task(self, reply: Reply, error) -> None:
        if reply.task_id in self._active_countersigns or reply.task_id in self._active_requests:
            return
        super()._fail_task(reply, error)
        task = self.tasks.get(reply.task_id or "")
        if task and task.state in ("failed", "rejected"):
            table = "pending_requests" if task.op == "request.submit" else "pending_countersigns"
            record = self.store.get(table, task.id)
            if record:
                self.store.put(table, task.id, {**record, "terminal": task.state})

    def op_work_start(self, body, files, task_id) -> Reply:
        card = body["card"]
        template = self.template(card["templateDigest"])
        validate_card(template, card)
        cd = card_digest(card)
        rec = self.store.get("cards", cd) or {"card": card, "review": {}}
        if rec.get("state") in ("started", "lapsed", "terminated", "closed") or card["fulfillment"].get("receipt"):
            fail(OUT_OF_ORDER, f"card is {rec.get('state', 'started')}")
        if template["formation"] == "proposal" and "request" not in rec:
            fail(UNKNOWN_CARD, "this provider did not draft that card")
        chain = verify_card(template, card, self.keys)
        if chain.effective is None:
            fail(OUT_OF_ORDER, "card is not effective")
        if chain.effective > parse_time(template["validUntil"]):
            fail(TEMPLATE_EXPIRED, "template expired before the card took effect")
        if self.now() > chain.effective + parse_duration(template["deadlines"]["startWithin"]):
            rec.update(card=card, state="lapsed")
            self._save(rec)
            fail(DEADLINE_PASSED, "startWithin passed; the card has lapsed")
        derived = card["derived"]
        if Decimal(str(derived["deposit"])) > 0:
            verify_voucher(body.get("voucher") or fail(CARD_INVALID, "a deposit voucher is required"), self.keys,
                           payment_provider=card["paymentProvider"], purpose="deposit", amount=derived["deposit"],
                           currency=template["pricing"]["currency"], card_digest=cd)
            rec["depositVoucher"] = body["voucher"]
        names = [n for n in card["inputs"] if not material(template, n).get("reviewBeforeSigning")]
        task = self.new_task("work.start")

        def finish() -> Reply:                      # material checks run verifiers: after the task is returned
            problem = self._materials(template, card["inputs"], files, names, auth=body.get("informationAuthorization"),
                                      bound=("cardDigest", cd), card_like=card)
            if problem:
                reason, wrong, rep = problem
                rejection = self._sign("rejection", cardDigest=cd, reason=reason, inputs=wrong, report=rep)
                if reason == "declaration-mismatch":
                    rec.update(card=card, state="lapsed")
                self._save({**rec, "card": card, "rejection": rejection})
                task.state = "rejected"
                task.artifacts.append({"rejection": rejection})
                return Reply({"op": "work.start", "rejection": rejection}, task_id=task.id, state=task.state)
            receipt = self._sign("receipt", cardDigest=cd)
            started = copy.deepcopy(card)
            started["fulfillment"]["receipt"] = receipt
            materials = {**{n: _unb64(v) for n, v in rec.get("review", {}).items()}, **{n: files[n] for n in names}}
            rec.update(card=started, state="started", task=task.id, materials={n: _b64(b) for n, b in materials.items()},
                       deliveries={}, steps=[], quotes={})
            self._save(rec)
            task.artifacts.append({"receipt": receipt})
            self.policy.on_start(template, started, materials)
            return Reply({"op": "work.start", "receipt": receipt}, task_id=task.id, state=task.state)
        return Reply({"op": "work.start"}, task_id=task.id, state=task.state, then=finish)

    # ── delivery ────────────────────────────────────────────────────────

    def card(self, cd: str) -> dict:
        return self._record(cd)["card"]

    def materials(self, cd: str) -> dict[str, bytes]:
        return {n: _unb64(v) for n, v in self._record(cd).get("materials", {}).items()}

    def request_step(self, cd: str, step: str) -> dict:
        """Ask the Client's user to authorize a declared step; pauses the delivery clock."""
        with self._lock:
            return self._request_step(cd, step)

    def _request_step(self, cd: str, step: str) -> dict:
        rec = self._record(cd)
        template = self.template(rec["card"]["templateDigest"])
        if step not in [s["name"] for s in template.get("authorizableSteps", [])]:
            fail(CARD_INVALID, f"{step} is not an authorizable step of the template")
        if rec.get("state") != "started":
            fail(OUT_OF_ORDER, "no work in progress")
        task = self.task(rec["task"])
        if task.state == "auth-required":
            fail(OUT_OF_ORDER, "a step request is already waiting for an answer")
        period = len(rec["card"]["signatures"]["7"]) + 1
        sr = self._sign("step-request", cardDigest=cd, period=period, step=step)
        task.state, task.status = "auth-required", {"stepRequest": sr}
        rec["steps"].append({"request": sr, "answer": None})
        self._save(rec)
        return sr

    def op_step_authorize(self, body, files, task_id) -> Reply:
        task = self.task(task_id or fail(INVALID_OBJECT, "answer on the delivery task"))
        if task.state != "auth-required":
            fail(OUT_OF_ORDER, "no step request is waiting")
        sr = task.status["stepRequest"]
        rec = self._record(sr["cardDigest"])
        sa = body["stepAuthorization"]
        h = objects.verify_signed(sa, sa["jwk"])
        check_fresh(h, self.now())
        if sa["stepRequestDigest"] != objects.object_digest(sr):
            fail(INVALID_OBJECT, "answer is for another step request")
        rec["steps"][-1]["answer"] = sa
        self._save(rec)
        task.state, task.status = "working", None
        return Reply({"op": "step.authorize", "ok": True}, task_id=task.id, state=task.state)

    def deliver(self, cd: str, files: Mapping[str, tuple[bytes, str]]) -> dict:
        """Sign slot 4(i) over this period's manifest and have the Market sign slot 5(i)."""
        with self._lock:
            return self._deliver(cd, files)

    def _deliver(self, cd: str, files: Mapping[str, tuple[bytes, str]]) -> dict:
        rec = self._record(cd)
        if rec.get("state") != "started":
            fail(OUT_OF_ORDER, f"card is {rec.get('state')}")
        card = copy.deepcopy(rec["card"])
        template = self.template(card["templateDigest"])
        period = len(card["signatures"]["4"]) + 1
        if period > len(deliverable_names(template)):
            fail(OUT_OF_ORDER, "all periods delivered")
        manifest = objects.new("manifest", cardDigest=cd, period=period,
                               files=[{"name": n, "digest": digest_bytes(b), "mediaType": t} for n, (b, t) in files.items()])
        periods = card["fulfillment"].setdefault("periods", [])
        periods.append({"manifest": manifest})
        card = sign_slot(card, 4, self.signer, self.now(), period=period)
        card = self.transport.send(self.market, "delivery.anchor", {"card": card}).body["card"]
        verify_card(template, card, self.keys)
        check_fresh(header_of(card["signatures"]["5"][period - 1]), self.now())
        rec["card"] = card
        rec["deliveries"][str(period)] = {n: _b64(b) for n, (b, _t) in files.items()}
        self._save(rec)
        return card

    def op_delivery_fetch(self, body, files, task_id) -> Reply:
        rec = self._record(body["cardDigest"])
        if task_id is None or task_id != rec.get("task"):
            fail(INVALID_OBJECT, "delivery.fetch is sent on the card's Delivery Task")
        period = body["period"]
        if str(period) not in rec.get("deliveries", {}):
            fail(OUT_OF_ORDER, f"period {period} is not anchored")
        return Reply({"op": "delivery.fetch", "card": rec["card"]},
                     files={n: _unb64(v) for n, v in rec["deliveries"][str(period)].items()})

    def op_acceptance_notify(self, body, files, task_id) -> Reply:
        incoming = body["card"]
        cd = card_digest(incoming)
        rec = self._record(cd)
        if rec.get("state") != "started":
            fail(OUT_OF_ORDER, f"card is {rec.get('state')}")
        template = self.template(incoming["templateDigest"])
        validate_card(template, incoming)
        card = self._merge_client_slots(rec["card"], incoming)
        verify_card(template, card, self.keys)
        old = rec["card"]["signatures"]
        for slot in ("6", "7"):
            for i, sig in enumerate(card["signatures"][slot]):
                if i >= len(old[slot]) or sig != old[slot][i]:
                    check_fresh(header_of(sig), self.now())
        for i, p in enumerate(card["fulfillment"].get("periods", []), start=1):
            rep = p.get("report")
            if rep is not None:
                h = objects.verify_signed(rep, card["client"]["jwk"], signer=card["client"]["id"])
                if rep["cardDigest"] != cd or rep.get("period") != i:
                    fail(INVALID_OBJECT, "report is for another card or period")
                known = (rec["card"]["fulfillment"].get("periods") or [{}] * i)
                if len(known) < i or known[i - 1].get("report") != rep:
                    check_fresh(h, self.now())
        rec["card"] = card
        if body.get("provenance"):
            rec.setdefault("provenance", []).append(body["provenance"])
        self._save(rec)
        return Reply({"op": "acceptance.notify", "ok": True})

    @staticmethod
    def _merge_client_slots(mine: Mapping[str, Any], theirs: Mapping[str, Any]) -> dict:
        """Take only what the Client adds (slots 6 and 7, reports, attestations); everything the
        Provider holds must already be on the incoming card, unchanged."""
        card = copy.deepcopy(dict(mine))
        for slot in ("1", "2", "3"):
            if theirs["signatures"][slot] != mine["signatures"][slot]:
                fail(OUT_OF_ORDER, f"slot {slot} differs from the Provider's copy")
        for slot in ("4", "5"):
            if theirs["signatures"][slot] != mine["signatures"][slot]:
                fail(OUT_OF_ORDER, f"slot {slot} differs from the Provider's copy")
        for slot in ("6", "7"):
            have, got = mine["signatures"][slot], theirs["signatures"][slot]
            if got[:len(have)] != have:
                fail(OUT_OF_ORDER, f"slot {slot} drops signatures the Provider holds")
            card["signatures"][slot] = list(got)
        mp, tp = card["fulfillment"].get("periods", []), theirs["fulfillment"].get("periods", [])
        if len(tp) != len(mp):
            fail(OUT_OF_ORDER, "periods differ from the Provider's copy")
        for a, b in zip(mp, tp):
            if b.get("manifest") != a.get("manifest"):
                fail(OUT_OF_ORDER, "a manifest differs from the Provider's copy")
            for k in ("report", "attestations"):
                if k in b:
                    a[k] = b[k]
        return card

    def op_payment_notify(self, body, files, task_id) -> Reply:
        voucher = body["voucher"]
        rec = self._record(voucher.get("cardDigest", ""))
        card = rec["card"]
        template = self.template(card["templateDigest"])
        currency = template["pricing"]["currency"]
        if voucher.get("purpose") == "installment":
            i = voucher.get("period")
            if not isinstance(i, int) or not 1 <= i <= len(card["signatures"]["7"]):
                fail(OUT_OF_ORDER, "installment for a period without slot 7")
            verify_voucher(voucher, self.keys, payment_provider=card["paymentProvider"], purpose="installment",
                           amount=card["derived"]["periods"][i - 1]["amount"], currency=currency,
                           card_digest=card_digest(card), period=i)
        elif voucher.get("purpose") == "termination":
            balance = self.balance(rec)
            if balance <= 0: fail(OUT_OF_ORDER, 'payment.notify cannot represent a provider refund')
            verify_voucher(voucher, self.keys, payment_provider=card["paymentProvider"], purpose="termination",
                           amount=balance, currency=currency, card_digest=card_digest(card))
        else:
            fail(INVALID_OBJECT, "payment.notify carries installment or termination vouchers")
        if voucher not in rec.setdefault('vouchers', []): rec['vouchers'].append(voucher)
        self._save(rec)
        return Reply({"op": "payment.notify", "ok": True})

    # ── termination ─────────────────────────────────────────────────────

    def op_termination_quote(self, body, files, task_id) -> Reply:
        rec = self._record(body["cardDigest"])
        card = rec["card"]
        if rec.get("state") in ("terminated", "closed"):
            fail(OUT_OF_ORDER, f"card is {rec['state']}")
        template = self.template(card["templateDigest"])
        if len(card["signatures"]["7"]) >= len(deliverable_names(template)):
            fail(OUT_OF_ORDER, "every period is accepted; there is nothing to terminate")
        period = len(card["signatures"]["7"]) + 1
        fee = self.policy.termination_fee(template, card, period)
        if Decimal(str(fee)) > Decimal(str(card["derived"]["periods"][period - 1]["amount"])) or Decimal(str(fee)) < 0:
            fail(CARD_INVALID, "a termination fee cannot exceed the current period's amount")
        quote = self._sign("termination-quote", cardDigest=body["cardDigest"], period=period, fee=fee)
        rec.setdefault("quotes", {})[objects.object_digest(quote)] = quote
        self._save(rec)
        return Reply({"op": "termination.quote", "terminationQuote": quote})

    def balance(self, rec: Mapping[str, Any]) -> Any:
        """Termination balance = fee − deposit (positive: the Client pays; negative: the Provider refunds)."""
        card = rec['card']; template = self.template(card['templateDigest'])
        if 'quote' not in card['fulfillment']['termination']:
            fail(OUT_OF_ORDER, 'a unilateral termination has no agreed balance')
        b = termination_balance(template,card,card['fulfillment']['termination'],self.keys,
                                deposit_paid=bool(rec.get('depositVoucher')))
        return int(b) if b == b.to_integral_value() else float(b)

    def refund_termination(self, cd: str) -> dict:
        """Pay an agreed negative balance and retain its original voucher locally."""
        with self._lock:
            rec = self._record(cd); card = rec['card']; template = self.template(card['templateDigest'])
            if rec.get('state') not in ('terminated','closed'):
                fail(OUT_OF_ORDER, 'terminate this original card before refunding')
            amount = -Decimal(str(self.balance(rec)))
            if amount <= 0: fail(OUT_OF_ORDER, 'this card has no agreed provider refund')
            voucher = rec.get('refundVoucher')
            if voucher is None:
                if self.payment is None: fail(INVALID_OBJECT, 'configure the original provider payment port')
                voucher = self.payment.pay(purpose='termination',template=template,card=card,
                    termination=card['fulfillment']['termination'],deposit_paid=True)
            verify_voucher(voucher,self.keys,payment_provider=card['paymentProvider'],purpose='termination',
                amount=amount,currency=template['pricing']['currency'],card_digest=cd)
            rec['refundVoucher'] = voucher; self._save(rec)
            return voucher

    def op_termination_notify(self, body, files, task_id) -> Reply:
        term = body["termination"]
        rec = self._record(term.get("cardDigest", ""))
        card = copy.deepcopy(rec["card"])
        previous = card['fulfillment'].get('termination')
        if previous and previous['termination'] == term:
            return Reply({'op':'termination.notify','ok':True})
        if rec.get("state") in ("terminated", "closed"):
            fail(OUT_OF_ORDER, f"card is {rec['state']}")
        h = objects.verify_signed(term, card["client"]["jwk"], signer=card["client"]["id"])
        check_fresh(h, self.now())
        record: dict[str, Any] = {"termination": term}
        if "terminationQuoteDigest" in term:
            quote = rec.get("quotes", {}).get(term["terminationQuoteDigest"])
            if quote is None:
                fail(OUT_OF_ORDER, "unknown termination quote")
            template = self.template(card["templateDigest"])
            if self.now() > ts_of(quote["signature"]) + parse_duration(template["deadlines"]["respondWithin"]):
                fail(DEADLINE_PASSED, "the quote expired")
            record["quote"] = quote
        card["fulfillment"]["termination"] = record
        template = self.template(card['templateDigest'])
        verify_card(template,card,self.keys)
        rec.update(card=card, state="terminated")
        self._save(rec)
        if rec.get("task") in self.tasks:
            self.tasks[rec["task"]].state = "canceled"
        return Reply({"op": "termination.notify", "ok": True})

    # ── AP2 payee role ──────────────────────────────────────────────────

    def op_ap2_checkout(self, body, files, task_id) -> Reply:
        """Issue the Checkout JWT for an A2C amount this provider is to be paid."""
        from .payment.ap2 import checkout
        if self.ap2_key is None:
            fail(INVALID_OBJECT, "this provider has no AP2 merchant key")
        purpose, period = body.get("purpose"), body.get("period")
        if purpose == "proposal-fee":
            template = self.template(body["templateDigest"])
            ref, amount = {"requestDigest": body["requestDigest"]}, template["pricing"].get("proposalFee", 0)
        elif purpose == "termination":
            rec = self._record(body["cardDigest"])
            template = self.template(rec["card"]["templateDigest"])
            ref, amount = {"cardDigest": body["cardDigest"]}, self.balance(rec)
        elif purpose in ("deposit", "installment"):
            card = body["card"]
            template = self.template(card["templateDigest"])
            validate_card(template, card)
            verify_card(template, card, self.keys)
            ref = {"cardDigest": card_digest(card)}
            amount = card["derived"]["deposit"] if purpose == "deposit" else card["derived"]["periods"][period - 1]["amount"]
        else:
            fail(INVALID_OBJECT, f"unknown purpose {purpose!r}")
        if Decimal(str(amount)) <= 0:
            fail(OUT_OF_ORDER, 'this party has no positive receivable for the checkout')
        jwt = checkout({"id": self.id, "name": self.name}, self.ap2_key, purpose=purpose, digest_ref=ref,
                       period=period if purpose == "installment" else None, amount=amount,
                       currency=template["pricing"]["currency"], now=int(self.now().timestamp()))
        return Reply({"op": "ap2.checkout", "checkoutJwt": jwt})

    # ── closing ─────────────────────────────────────────────────────────

    def close(self, cd: str) -> None:
        with self._lock:
            self._close(cd)

    def _close(self, cd: str) -> None:
        rec = self._record(cd)
        self.transport.send(self.market, "card.close", {"card": rec["card"]})
        rec["state"] = "closed"
        self._save(rec)
        if rec.get("task") in self.tasks:
            self.tasks[rec["task"]].state = "completed"

    def purge_lapsed(self) -> list[str]:
        """Drop offers that lapsed before work started, with their tasks and materials (合同协议 §十二):
        a countersigned card after slot 2 + startWithin, a drafted card after slot 3 + respondWithin + startWithin."""
        with self._lock:
            gone = []
            for cd, rec in self.store.all("cards").items():
                if rec.get("state") not in ("offered", "formed"):
                    continue
                template = self.template(rec["card"]["templateDigest"])
                d, sigs = template["deadlines"], rec["card"]["signatures"]
                last = ts_of(sigs["3"]) + parse_duration(d["respondWithin"]) if rec["state"] == "offered" else ts_of(sigs["2"])
                if self.now() > last + parse_duration(d["startWithin"]):
                    self.store.delete("cards", cd)
                    self.tasks.pop(rec.get("task", ""), None)
                    gone.append(cd)
            return gone

    def endorse_provenance(self, cd: str) -> list[dict]:
        """Have the Market countersign the provenance statements the Client signed."""
        with self._lock:
            return self._endorse_provenance(cd)

    def _endorse_provenance(self, cd: str) -> list[dict]:
        rec = self._record(cd)
        out = [self.transport.send(self.market, "provenance.endorse", {"provenance": p, "card": rec["card"]}).body["provenance"]
               for p in rec.get("provenance", [])]
        rec["provenance"] = out
        self._save(rec)
        return out


__all__ = ["Provider", "ProviderPolicy", "DefaultPolicy", "Proposal", "MaterialReviewScope", "MaterialObservation"]
