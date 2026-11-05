"""Shared file handling and evidence gates for recorded demonstration artifacts."""

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def media_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inside(folder, relative):
    """Resolve an artifact without following a path outside its recording directory."""
    folder = Path(folder).resolve()
    target = (folder / relative).resolve()
    if not target.is_relative_to(folder):
        raise ValueError("Artifact path escapes its recording directory")
    return target


def require(condition, message):
    if not condition:
        raise ValueError(message)


NATIVE_FORMAT = "ioalab.native-demo/1"
NATIVE_SCENES = ("delegation-provider", "delegation-assistant", "messaging")


def object_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _reference(value):
    return isinstance(value, str) and bool(value.strip())


def _positive_amount(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and amount > 0 else None
    except (ValueError, ArithmeticError):
        return None


def native_review_issues(proof):
    """Return missing filming facts, never downgrade changed approval or input hashes."""
    if __package__:
        from .review_story import APPROVAL, approved_content
    else:
        from review_story import APPROVAL, approved_content
    stories = approved_content()
    require(proof.get("format") == NATIVE_FORMAT, "Unknown native film format")
    scene = proof.get("scene")
    require(scene in NATIVE_SCENES and scene in stories, "Unknown approved scene")
    require(proof.get("case") == ("messaging" if scene == "messaging" else "delegation"),
            "Native scene and case do not match")
    require(proof.get("approvalSha256") == APPROVAL["contentSha256"],
            "Native film approval hash differs from the approved screenplay")
    issues = []

    def check(ok, message):
        if not ok:
            issues.append(message)

    refs = proof.get("evidence", {})
    require(isinstance(refs, dict), "Evidence references must be an object")
    # A supplied digest is an assertion, including during a candidate edit.
    for key, entry in refs.items():
        require(isinstance(entry, dict) and _reference(entry.get("file")), "Invalid evidence reference: " + key)
        require(_sha(entry.get("sha256")), "Invalid evidence SHA256: " + key)

    def evidence(value, label):
        check(isinstance(value, dict) and value.get("evidenceRef") in refs,
              label + ": independent evidence reference is missing")

    def confirmation(value, label):
        check(isinstance(value, dict) and all(_reference(value.get(k)) for k in
              ("reference", "operationId", "bindingDigest", "source")) and
              (value.get("decision") == "approved" or
               (value.get("decision") == "expired" and value.get("historicallyApproved") is True)),
              label + ": bound historical owner approval is missing")

    check(proof.get("screens") == "real browser pages" and proof.get("fixtures") == [] and
          proof.get("browserErrors") == [], "Declared real browser footage is missing")
    check(proof.get("kind") != "template_only_not_business_proof", "Template is not business proof")
    preparation = proof.get("preparation", {})
    if preparation.get("guided") or preparation.get("priorFailures"):
        check(preparation.get("firstAttemptAutonomous") is False and _reference(preparation.get("label")),
              "Guided or corrected takes require an explicit preparation label")
    for shot in stories[scene]["shots"]:
        keys = proof.get("shotEvidence", {}).get(shot["id"], [])
        check(isinstance(keys, list) and bool(keys) and all(k in refs for k in keys),
              shot["id"] + ": shot evidence binding is missing")
    business = proof.get("business", {})
    require(isinstance(business, dict), "Business snapshot must be an object")
    if scene == "messaging":
        check(proof.get("acceptancePresentation") == "narrated" and
              proof.get("presentationScope") == "platform-message-flow" and
              proof.get("outlookFootageIncluded") is False,
              "Platform film cannot claim actual Outlook acceptance or calendar footage")
        rules = business.get("rules", [])
        check(isinstance(rules, list) and len(rules) == 2, "Two published receiving rules are required")
        for rule in rules if isinstance(rules, list) else []:
            require(isinstance(rule, dict), "Rule snapshot must be an object")
            evidence(rule, "rule")
            check(_reference(rule.get("name")) and isinstance(rule.get("revision"), int) and
                  not isinstance(rule.get("revision"), bool) and rule["revision"] > 0 and
                  _reference(rule.get("contentDigest")) and bool(rule.get("allowedSenders")),
                  "Published rule identity, revision, content and allowed sender are required")
            confirmation(rule.get("confirmation"), "rule")
        directory = business.get("directory", {})
        allowed, other = directory.get("allowed", {}), directory.get("other", {})
        evidence(allowed, "allowed directory")
        evidence(other, "other directory")
        check(_reference(allowed.get("accountId")) and allowed.get("status") == "completed" and
              len(allowed.get("interfaces", [])) == 2, "Allowed sender must discover both interfaces")
        empty = other.get("kind") == "empty_directory" and other.get("status") == "completed" and other.get("interfaces") == []
        denied = other.get("kind") == "rejected" and isinstance(other.get("status"), int) and other["status"] in (401, 403)
        check(_reference(other.get("accountId")) and other.get("accountId") != allowed.get("accountId") and (empty or denied),
              "Other-account directory result must use a distinct account and preserve its actual empty or rejected result")
        forward = business.get("forward", {})
        evidence(forward, "forward")
        check(_reference(forward.get("notificationRef")) and _reference(forward.get("nativeSessionId")) and
              forward.get("visible") is True and forward.get("read") in ("unknown", "read", "unread"),
              "Actual native notification visibility is required; queued alone is insufficient")
        interaction = business.get("interactionRef")
        messages = business.get("messages", [])
        valid_pair = isinstance(messages, list) and len(messages) == 2 and _reference(interaction)
        check(valid_pair, "Two actual message results are required")
        receivers = business.get("receiverSessionIds")
        check(isinstance(receivers, list) and len(receivers) == 2 and
              all(_reference(session) for session in receivers) and receivers[0] == receivers[1],
              "Both messages must bind the same nonempty native receiver Session")
        if valid_pair:
            first, second = messages
            require(all(isinstance(m, dict) for m in messages), "Message snapshots must be objects")
            check(all(m.get("interactionRef") == interaction and _reference(m.get("messageRef")) for m in messages) and
                  first.get("messageRef") != second.get("messageRef"),
                  "Rescheduling requires the same interaction and distinct message references")
            for message in messages:
                evidence(message, "message")
                check(message.get("outcome") == "completed" and isinstance(message.get("output"), dict) and
                      message.get("resultDigest") == object_digest({"outcome": message.get("outcome"), "output": message.get("output")}),
                      "Message result digest does not bind the actual terminal output")
            conflict, submitted = first.get("output") or {}, second.get("output") or {}
            check(conflict.get("status") == "conflict" and conflict.get("inviteSubmitted") is False and
                  _reference(conflict.get("suggestedStart")) and _reference(conflict.get("suggestedEnd")),
                  "The original conflict and real suggested interval must be retained")
            check(submitted.get("status") == "invite_submitted" and submitted.get("inviteSubmitted") is True and
                  submitted.get("calendar") == "awaiting_acceptance" and _reference(submitted.get("reservationId")),
                  "The second message must contain actual invitation submission, awaiting acceptance")
            reschedule = business.get("reschedule", {})
            evidence(reschedule, "reschedule")
            check(reschedule.get("generatedBy") == "sender-model" and
                  reschedule.get("scriptedSecondInput") is False and
                  reschedule.get("operatorProvidedSecondTime") is False and
                  proof.get("scriptedSecondInput", False) is False and
                  proof.get("operatorProvidedSecondTime", False) is False and
                  reschedule.get("basedOnMessageRef") == first.get("messageRef") and
                  reschedule.get("sentMessageRef") == second.get("messageRef"),
                  "The sender model must generate the second request from the actual first reply without scripted input or operator-provided time")
            invitation = business.get("invitation", {})
            evidence(invitation, "invitation")
            check(invitation.get("transport") == "agentmail-calendar" and
                  invitation.get("reservationId") == submitted.get("reservationId") and
                  all(_reference(invitation.get(k)) for k in ("requestId", "providerEventId", "uid", "organizerRole", "recipientRole")) and
                  invitation.get("organizerMatchedConfigured") is True and invitation.get("recipientMatchedConfigured") is True and
                  invitation.get("externalEventMatched") is True and invitation.get("recipientStatus") == "needs_action" and
                  _sha(invitation.get("auditSha256")),
                  "Exact external event audit must match configured parties and remain needs_action")
            closed = business.get("closed", {})
            evidence(closed, "close")
            check(closed.get("interactionRef") == interaction and closed.get("closed") is True and
                  closed.get("firstResultPreserved") is True, "Closing must preserve the original conflict result")
    else:
        # Both viewpoints bind one real contract, rather than narrating a different paid order.
        quotes, contract = business.get("quotes", []), business.get("contract", {})
        require(isinstance(quotes, list) and all(isinstance(q, dict) for q in quotes) and
                isinstance(contract, dict), "Invalid delegation business snapshots")
        check(isinstance(quotes, list) and len(quotes) == 3 and
              len({q.get("cardDigest") for q in quotes}) == 3 and
              all(_reference(q.get("cardDigest")) and q.get("signed") is True for q in quotes),
              "Three distinct actual signed proposals are required")
        for quote in quotes if isinstance(quotes, list) else []:
            evidence(quote, "quote")
        evidence(contract, "contract")
        digest = contract.get("cardDigest")
        selected = next((q for q in quotes if q.get("cardDigest") == digest), {})
        signatures = contract.get("signatures", {})
        amount = _positive_amount(selected.get("total"))
        check(_reference(digest) and business.get("focusedCardDigest") == digest and selected and
              amount is not None and contract.get("state") == "completed" and _positive_amount(contract.get("paid")) == amount and
              all(signatures.get(slot) is True for slot in ("1", "2", "3")) and
              all(signatures.get(slot) == [True] for slot in ("4", "5", "6", "7")),
              "Focus, signatures, payment and completion must bind the selected contract")
        payment = business.get("payment", {})
        evidence(payment, "payment")
        check(payment.get("cardDigest") == digest and payment.get("voucherVerified") is True and
              amount is not None and _positive_amount(payment.get("amount")) == amount and payment.get("closed") is True,
              "Verified actual payment voucher and closed state are required")
        gates = business.get("confirmations", {})
        check(set(gates) == {"authorization", "contract", "registration", "acceptance"},
              "Four separately bound confirmation classes are required")
        check(all(len({gate.get(key) for gate in gates.values()}) == 4
                  for key in ("reference", "operationId", "bindingDigest")),
              "Four confirmation classes require distinct approval bindings")
        for name, gate in gates.items():
            confirmation(gate, name)
            evidence(gate, name)
        onboarding = business.get("onboarding", {})
        evidence(onboarding, "onboarding")
        check(onboarding.get("registrationRecorded") is True and onboarding.get("configurationBoundaryPassed") is True,
              "This new tutorial requires recorded registration and a passed configuration boundary")
    return issues


def validate_native_demo(proof, *, require_complete=True):
    issues = native_review_issues(proof)
    if require_complete:
        require(not proof.get("reviewCandidate"), "Review candidate cannot be a complete film")
        require(not issues, "Native film evidence is incomplete: " + "; ".join(issues))
    return issues


def validate_native_files(folder, proof):
    """Rehash local evidence without copying its potentially private contents."""
    for key, entry in proof.get("evidence", {}).items():
        path = inside(folder, entry["file"])
        require(path.is_file() and media_digest(path) == entry["sha256"], "Evidence file SHA256 mismatch: " + key)


def waiting_was_cut(previous, current):
    """Only compare timestamps from the same capture file and time origin."""
    return bool(
        previous
        and previous.get("source", "screen.webm") == current.get("source", "screen.webm")
        and current["start"] > previous["start"] + previous["duration"] + 1
    )


def require_offer_window(card, template, *, margin=180, now=None):
    """Filming must fit the existing signed proposal window; never renew it."""
    from datetime import timedelta
    from a2c.client import header_ts
    from a2c.codec import parse_duration, utcnow
    deadline=header_ts(card['signatures']['3'])+parse_duration(template['deadlines']['respondWithin'])
    require(deadline-(now or utcnow())>=timedelta(seconds=margin), 'Insufficient proposal response time; stop, do not automatically renew authorization or pay again')
    return deadline.isoformat()


def validate_delegation_resume(proof, segments, orders, skill_audit):
    """Only resume footage after real quotes, before any contract consent."""
    require(proof.get('case')=='delegation' and proof.get('executionPath')=='generic-primitives', 'Resume requires the generic delegation recording')
    require(proof.get('skillAudit')==skill_audit, 'Installed skill changed since the original capture')
    require([s['id'] for s in segments]==['cover','account','configuration','skill','need','authorization','discovery','quotes'], 'Resume requires the exact unsigned quote checkpoint')
    require([t['kind'] for t in proof.get('userTurns',[])]==['request','authorization'], 'Resume must not repeat contract consent')
    quotes=proof.get('quotes',[])
    require(len(quotes)==3 and {q['cardDigest'] for q in quotes}=={o['card_digest'] for o in orders}, 'Resume contract set differs from recorded proposals')
    require(not any(o['signatures'].get('1') for o in orders), 'Resume cannot replay a signed contract')
    require(not proof.get('browserErrors'), 'Keep failed browser evidence separate')


def acceptance_presentation(proof):
    mode=proof.get('acceptancePresentation','recorded')
    require(mode in ('recorded','narrated'),'Unknown acceptance presentation')
    if mode=='narrated':
        require(proof.get('case')=='messaging' and proof.get('presentationScope')=='platform-message-flow','Narrated acceptance requires an explicit platform-message-flow scope')
    return mode


def native_user_text(event):
    if event.get('role')=='user':return event.get('content')
    if event.get('event_type')=='chat.ask_user_answer':
        answers=event.get('answers',[])
        require(len(answers)==1,'Expected one native answer per confirmation')
        text=answers[0].get('custom_input','')
        return text if isinstance(text,str) and text.strip() else None
    return None


def native_answer_seen(path, offset, text):
    """Require a new server acknowledgement, never an old answer or partial line."""
    with Path(path).open('rb') as stream:
        stream.seek(offset);raw=stream.read().decode('utf-8',errors='ignore')
    for line in raw.splitlines():
        try:event=json.loads(line)
        except json.JSONDecodeError:continue
        if event.get('event_type')=='chat.ask_user_answer' and native_user_text(event)==text:return True
    return False


BUSINESS_SETTINGS_REPLY='非关键设置按合理默认值拟定，列在合同中由我确认。'


def validate_business_operator(proof):
    messages=[a for a in proof.get('operatorActions',[]) if a.get('kind')=='message']
    if not messages:return
    answer=proof.get('businessSettingsAnswer',{})
    require(len(messages)==1 and messages[0]['id']=='business-settings-answer', 'Unapproved operator prompt in formal recording')
    require(answer.get('text')==BUSINESS_SETTINGS_REPLY and '规格参数' in answer.get('question','') and len(answer.get('nativeTraceSha256',''))==64, 'Actual authored business answer and native question evidence are required')


def validate_real_case(proof, *, preview=None):
    """Check business evidence; this does not replace visual inspection of recorded footage."""
    if proof.get("format") == NATIVE_FORMAT:
        validate_native_demo(proof)
        return
    preview = proof.get("preview", False) if preview is None else preview
    require(
        proof.get("fixtures") == [], "Formal cases reject fixtures or missing fixture declarations"
    )
    require(
        proof.get("screens") == "real browser pages" and proof.get("browserErrors") == [],
        "Expected declared error-free real browser footage",
    )
    case = proof.get("case")
    if case == "delegation":
        require(not preview, "Delegation is published only after the full contract loop")
        if proof.get('executionPath')=='generic-primitives':
            onboarding=proof.get('onboarding',{})
            require(onboarding.get('account')=='xiaomei@outlook.com' and onboarding.get('registration')=='actual UI registration',
                    'Actual recorded registration of the declared demo account is required')
            require(onboarding.get('configuration')=='actual skill file saved and reread' and onboarding.get('skillSelection')=='native skill menu' and bool(proof.get('nativeSkillPreload')),
                    'Actual configuration save and native skill selection are required')

        require(proof.get("providerExecution") == "swarm", "Real Swarm production is required")
        order = proof["delegation"]
        digest = proof["cardDigest"]
        require(
            proof.get("focusedCardDigest") == order["cardDigest"] == digest,
            "Focus, completion and payment must describe the same contract",
        )
        require(order["state"] == "completed", "Contract is not completed")
        signatures = order["signatures"]
        require(
            all(signatures.get(slot) is True for slot in ("1", "2", "3")),
            "Formation signatures are incomplete",
        )
        require(
            all(signatures.get(slot) == [True] for slot in ("4", "5", "6", "7")),
            "Delivery, anchoring, receipt and acceptance signatures are incomplete",
        )
        quotes = proof["quotes"]
        original=proof.get('flow')=='nail-original'
        count=3 if original else 2
        require(len(quotes)==count and len({q['cardDigest'] for q in quotes})==count,
                'Distinct signed proposals are required')
        if original:
            validate_business_operator(proof)
            report=proof.get('verification',{})
            require(report.get('status')=='pass' and report.get('checkCount')==10 and report.get('allPassed') is True,'Ten actual local WASM checks are required')
            production=proof.get('production',{})
            require(production.get('submission')=='delivered' and production.get('selftestStatuses',[])[-1:] == ['pass'],'Actual provider SDK submission is required')
            require({q['provider'] for q in quotes}=={'灵犀小程序','快搭建站','码上飞'},'Use the authored service identities')
            require(proof.get('confirmationGates')==['authorization','contract','registration','acceptance'],'Four separate confirmation gates are required')
            turns=proof.get('userTurns',[])
            kinds=[t['kind'] for t in turns]
            require([k for k in kinds if k not in ('settings','clarification')]==['request','authorization','contract','registration','acceptance'],'User turn order is incomplete')
            for optional in ('settings','clarification'):
                require(kinds.count(optional)<=1 and (optional not in kinds or kinds.index('authorization')<kinds.index(optional)<kinds.index('contract')), 'Business answer belongs before contract confirmation')
            if 'settings' in kinds:require(turns[kinds.index('settings')]['text']==BUSINESS_SETTINGS_REPLY and proof.get('businessSettingsAnswer',{}).get('text')==BUSINESS_SETTINGS_REPLY,'Only the authored settings answer is allowed')
            if any(t['kind']=='clarification' for t in turns):
                require(proof.get('materialClarification',{}).get('question') and turns[kinds.index('clarification')]['text']=='本轮不提供架构图，请先比较现有提案的验收标准并推荐，再让我确认具体合同。','Only the authored business-material answer is allowed')
            require(not any(word in turns[0]['text'] for word in ('SDK','SKILL.md','--confirmed','基础版','可靠版')),'Initial user request must be business-only')
            auth=proof.get('authorization',{})
            require(Decimal(str(auth.get('proposalFees',-1)))==3 and Decimal(str(auth.get('budget',0)))==500,'Actual proposal fees and total scope are required')
            require(proof.get('registration',{}).get('beforeDelivery') is True,'Registration authorization must precede delivery')
        require(
            sorted(Decimal(str(q["total"])) for q in quotes) == ([Decimal(239),Decimal(259),Decimal(299)] if original else [Decimal(259),Decimal(299)]),
            "Proposal amounts do not match this demonstration",
        )
        selected = next((q for q in quotes if q["cardDigest"] == digest), None)
        require(
            selected is not None and Decimal(str(selected["total"])) == Decimal(299),
            "The completed contract is not the selected reliable proposal",
        )
        require(Decimal(str(order["paid"])) == Decimal(299), "Actual payment is incomplete")
        bookings = proof["bookingDemo"]
        require(
            len(bookings) == 1 and bookings[0]["cancelled"] == 1,
            "The delivered configuration must actually book and cancel",
        )
    elif case == "messaging":
        require(
            proof["accessControl"]["directSendStatus"] == 403,
            "Unauthorized sender rejection is missing",
        )
        require(
            proof["forward"]["status"] == "delivered"
            and proof["forward"]["transport"] == "jiuwen-web",
            "Real Jiuwen delivery is missing",
        )
        require(
            proof["busy"] == {"status": "no_time", "reason": "calendar_conflict"},
            "Local schedule conflict rejection is missing",
        )
        require(
            proof["meeting"]["status"] == "invite_submitted"
            and proof["meeting"]["transport"] == "agentmail-calendar",
            "Actual AgentMail invitation submission is missing",
        )
        mode=acceptance_presentation(proof)
        if mode=='narrated':
            require(not preview,'Narrated platform film must declare its scope rather than use the incomplete preview mode')
            require(proof.get('outlook',{}).get('status') in ('awaiting_actual_acceptance','not_recorded') and proof.get('outlookFootageIncluded') is False,'Narrated acceptance cannot claim actual Outlook acceptance or footage')
        elif not preview:
            require(
                proof.get("outlook", {}).get("status") == "accepted_and_visible",
                "Actual Outlook acceptance and calendar footage are required",
            )
    else:
        raise ValueError("Unknown real demonstration case")


def validate_reviews(proof, qa, shots, video):
    """Bind both encoded reviews to the exact movie bytes being published."""
    preview = proof.get("preview", False)
    digest = media_digest(video)
    if proof.get("format") == NATIVE_FORMAT:
        validate_native_demo(proof)
        require(qa.get("proofDigest") == object_digest(proof) and
                qa.get("approvalSha256") == proof["approvalSha256"],
                "Native evidence or approved screenplay changed after encoded QA")
    mode=acceptance_presentation(proof)
    require(qa.get('acceptancePresentation','recorded')==shots.get('acceptancePresentation','recorded')==mode,'Acceptance presentation changed after review')
    require(
        qa["result"] == ("preview-passed" if preview else "passed")
        and qa.get("preview", False) == preview
        and shots["result"] == "passed"
        and shots.get("preview", False) == preview,
        "Encoded reviews do not match this artifact",
    )
    require(
        qa.get("videoSha256") == shots.get("videoSha256") == digest,
        "Movie changed after review; run both encoded checks again",
    )
    return digest


def validate_recording(proof):
    require(
        proof.get("screens") == "real browser pages" and not proof.get("browserErrors"),
        "Expected error-free real browser footage",
    )
    if proof.get("case") == "global":
        run = proof["experiment"]
        require(
            run["completedOrders"] >= 4 and run["messages"] >= 4 and not run["errors"],
            "Global recording lacks successful protocol activity",
        )
    elif not proof.get("fixtures"):
        validate_real_case(proof)
    elif proof["case"] == "delegation":
        require(
            proof["delegation"]["state"] == "completed"
            and proof["delegation"]["signatures"]["7"] == [True],
            "Local contract test failed",
        )
    elif proof["case"] == "messaging":
        require(
            proof["forward"]["transport"] == "jiuwen-web"
            and proof["meeting"]["status"] == "invite_submitted",
            "Local message test failed",
        )
    else:
        raise ValueError("Unknown recording case")
