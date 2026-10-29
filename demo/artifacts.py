"""Shared file handling and evidence gates for the two real demonstration cases."""

import hashlib
import json
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


def waiting_was_cut(previous, current):
    """Only compare timestamps from the same capture file and time origin."""
    return bool(
        previous
        and previous.get("source", "screen.webm") == current.get("source", "screen.webm")
        and current["start"] > previous["start"] + previous["duration"] + 1
    )


def acceptance_presentation(proof):
    mode=proof.get('acceptancePresentation','recorded')
    require(mode in ('recorded','narrated'),'Unknown acceptance presentation')
    if mode=='narrated':
        require(proof.get('case')=='messaging' and proof.get('presentationScope')=='platform-message-flow','Narrated acceptance requires an explicit platform-message-flow scope')
    return mode


def validate_real_case(proof, *, preview=None):
    """Check business evidence; this does not replace visual inspection of recorded footage."""
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
            require(not any(a.get('kind')=='message' for a in proof.get('operatorActions',[])),'Do not rescue a formal flow with extra user prompts')
            report=proof.get('verification',{})
            require(report.get('status')=='pass' and report.get('checkCount')==10 and report.get('allPassed') is True,'Ten actual local WASM checks are required')
            production=proof.get('production',{})
            require(production.get('submission')=='delivered' and production.get('selftestStatuses',[])[-1:] == ['pass'],'Actual provider SDK submission is required')
            require({q['provider'] for q in quotes}=={'灵犀小程序','快搭建站','码上飞'},'Use the authored service identities')
            require(proof.get('confirmationGates')==['authorization','contract','registration','acceptance'],'Four separate confirmation gates are required')
            turns=proof.get('userTurns',[])
            require([t['kind'] for t in turns]==['request','authorization','contract','registration','acceptance'],'User turn order is incomplete')
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
