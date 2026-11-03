"""Publish only checked movies and an allowlisted evidence summary; raw runs stay private."""

import argparse
import json
import math
import shutil
import time
from pathlib import Path
if __package__:
    from .artifacts import (inside, read_json as read, require, validate_real_case,
                            validate_reviews, write_json as write, media_digest,
                            validate_native_demo, validate_native_files)
else:
    from artifacts import (inside, read_json as read, require, validate_real_case,
                           validate_reviews, write_json as write, media_digest,
                           validate_native_demo, validate_native_files)

ROOT = Path(__file__).resolve().parents[1]


def _only(value, keys):
    return {key: value[key] for key in keys if key in value}


def public_native_business(proof):
    """Project facts explicitly; never publish private audit files, messages or emails."""
    business = proof["business"]
    confirmation_keys = ("reference", "operationId", "bindingDigest", "decision", "historicallyApproved", "source")
    if proof["scene"] == "messaging":
        return {
            "rules": [{**_only(rule, ("name", "revision", "contentDigest", "allowedSenders")),
                       "confirmation": _only(rule["confirmation"], confirmation_keys)} for rule in business["rules"]],
            "directory": {name: {**_only(value, ("accountId", "kind", "status")),
                                   "interfaces": [_only(item, ("name", "revision")) for item in value.get("interfaces", [])]}
                          for name, value in business["directory"].items() if name in ("allowed", "other")},
            "forward": _only(business["forward"], ("notificationRef", "nativeSessionId", "visible", "read")),
            "interactionRef": business["interactionRef"],
            "receiverSessionIds": list(business["receiverSessionIds"]),
            "messages": [{**_only(message, ("messageRef", "interactionRef", "outcome", "resultDigest")),
                          "output": _only(message["output"], ("status", "reason", "inviteSubmitted", "suggestedStart",
                                    "suggestedEnd", "timeZone", "calendar", "reservationId"))}
                         for message in business["messages"]],
            "reschedule": _only(business["reschedule"], ("generatedBy", "basedOnMessageRef", "sentMessageRef",
                                                        "scriptedSecondInput", "operatorProvidedSecondTime")),
            "invitation": _only(business["invitation"], ("transport", "reservationId", "requestId", "providerEventId",
                    "providerClientId", "providerOperationId", "uid", "organizerRole", "recipientRole",
                    "organizerMatchedConfigured", "recipientMatchedConfigured", "recipientStatus", "externalEventMatched",
                    "delivery", "auditSha256")),
            "closed": _only(business["closed"], ("interactionRef", "closed", "firstResultPreserved")),
        }
    return {
        "quotes": [_only(q, ("cardDigest", "provider", "total", "signed")) for q in business["quotes"]],
        "focusedCardDigest": business["focusedCardDigest"],
        "contract": _only(business["contract"], ("cardDigest", "state", "paid", "signatures")),
        "payment": _only(business["payment"], ("cardDigest", "voucherVerified", "amount", "closed")),
        "confirmations": {name: _only(gate, confirmation_keys) for name, gate in business["confirmations"].items()},
        "onboarding": _only(business["onboarding"], ("registrationRecorded", "configurationBoundaryPassed")),
    }


def publish_native(folder, scene, approved_video_sha256=None):
    """Bind the current operator's reviewed movie bytes; this is no new user authorization."""
    folder = Path(folder).resolve()
    proof = read(folder / "evidence.json")
    require(not proof.get("reviewCandidate"), "Review candidate is not publishable")
    require(proof.get("scene") == scene, "Native publication scene mismatch")
    validate_native_demo(proof)
    validate_native_files(folder, proof)
    for name, field in (("segments.json", "segmentsSha256"), ("voice.json", "voiceIndexSha256"),
                        ("edit-list.json", "editListSha256")):
        require(media_digest(folder / name) == proof.get(field), "Native edit inputs changed: " + name)
    voices = read(folder / "voice.json")
    for voice in voices:
        require(media_digest(inside(folder, voice["file"])) == voice["audioSha256"], "Narration changed after QA")
    for shot in read(folder / "segments.json"):
        for panel in shot.get("panels", []):
            require(media_digest(inside(folder, panel["source"])) == panel["sha256"], "Source changed after QA")
    qa, shots = read(folder / "qa.json"), read(folder / "shot-review.json")
    digest = validate_reviews(proof, qa, shots, folder / "demo.mp4")
    require(approved_video_sha256 == digest, "Binding the operator-reviewed final video SHA256 is required")
    require(qa.get("manualListening") == "passed", "Manual narration listening QA is pending")
    public_shots = []
    for shot in shots["shots"]:
        require(isinstance(shot, dict) and isinstance(shot.get("id"), str) and
                shot["id"] in {voice["id"] for voice in voices}, "Unexpected encoded shot identity")
        if "meanVolumeDb" in shot:
            require(isinstance(shot["meanVolumeDb"], (int, float)) and
                    not isinstance(shot["meanVolumeDb"], bool) and math.isfinite(shot["meanVolumeDb"]),
                    "Encoded shot volume must be a finite number")
        if "narrationWithinShot" in shot:
            require(isinstance(shot["narrationWithinShot"], bool), "Encoded shot narration flag must be boolean")
        public_shots.append(_only(shot, ("id", "meanVolumeDb", "narrationWithinShot")))
    public_business = public_native_business(proof)
    prefix = {"delegation-provider": "02", "delegation-assistant": "03", "messaging": "04"}[scene]
    movie = prefix + "-" + scene + "-" + digest[:12] + ".mp4"
    target = ROOT / "demo/out" / movie
    require(not target.exists() or media_digest(target) == digest, "Preserve the existing published film")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_stem(target.stem + "-publishing")
    shutil.copyfile(folder / "demo.mp4", staged)
    require(media_digest(staged) == digest, "Staged native movie digest mismatch")
    if target.is_file() and media_digest(target) == digest:
        staged.unlink()
    else:
        # Keep the same bounded atomic replacement for CLI and package callers.
        for attempt in range(12):
            try:
                staged.replace(target)
                break
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(.25)
    evidence = {
        "format": proof["format"], "scene": scene, "approvalSha256": proof["approvalSha256"],
        "movie": "out/" + movie, "sha256": digest, "bytes": target.stat().st_size,
        "acceptancePresentation": proof.get("acceptancePresentation", "recorded"),
        "presentationScope": proof.get("presentationScope"), "outlookFootageIncluded": proof.get("outlookFootageIncluded", False),
        "presentationOverride": _only(proof.get("presentationOverride", {}), ("layout", "source")),
        "preparation": _only(proof.get("preparation", {}), ("label", "guided", "firstAttemptAutonomous")),
        "evidenceSha256": {key: value["sha256"] for key, value in proof["evidence"].items()},
        "business": public_business,
        "qa": _only(qa, ("result", "dimensions", "meanVolumeDb", "narrationClips", "duration", "videoSha256", "manualListening")),
        "allShots": public_shots,
    }
    # Do not copy invite.json or other local evidence: those may include real addresses.
    dest = ROOT / "demo/assets/native-cases" / (scene + "-" + digest[:12])
    dest.mkdir(parents=True, exist_ok=True)
    write(dest / "evidence.json", evidence)
    # The existing reviewer does not hash-bind its contact sheet. Keep that local
    # image private; only the reviewed final MP4 and allowlisted metadata publish.
    return evidence


def publish(folder, case):
    folder = folder.resolve()
    proof = read(folder / "evidence.json")
    qa = read(folder / "qa.json")
    shots = read(folder / "shot-review.json")
    require(
        proof["case"] == case, "Recording case does not match its publication target"
    )
    validate_real_case(proof)
    preview = proof.get("preview", False)
    digest = validate_reviews(proof, qa, shots, folder / "demo.mp4")
    if case == "delegation":
        movie = "02-delegation.mp4"
        keys = (
            "case",
            "providerExecution",
            "fixtures",
            "browserErrors",
            "quotes",
            "focusedCardDigest",
            "cardDigest",
            "delegation",
        )
        evidence = {k: proof[k] for k in keys}
        for key in ('providerRetake','production','verification','nativeSkillPreload','flow','userTurns','confirmationGates','authorization','registration','onboarding','executionPath','nativeConfirmations','skillAudit','assistantModel','recommendation','materialClarification','continuation','businessSettingsAnswer'):
            if key in proof: evidence[key]=proof[key]
        evidence["bookingDemo"] = {
            "booked": True,
            "cancelled": True,
            "configuration": "actual accepted delivery",
        }
    else:
        movie = "03-messaging-preview.mp4" if preview else "03-messaging.mp4"
        evidence = {
            k: proof[k]
            for k in (
                "case",
                "fixtures",
                "browserErrors",
                "accessControl",
                "busy",
                "outlook",
                "appointment",
            )
        }
        evidence["preview"] = preview
        for key in ('acceptancePresentation','presentationScope','outlookFootageIncluded'):
            if key in proof:evidence[key]=proof[key]
        if proof.get("interfaceRetake"):
            evidence["interfaceRetake"] = {
                k: proof["interfaceRetake"][k]
                for k in ("source", "reason", "shots", "browserErrors")
            }
        evidence["forward"] = {
            k: proof["forward"][k] for k in ("status", "transport", "read")
        }
        evidence["meeting"] = {
            k: proof["meeting"][k]
            for k in ("status", "calendar", "recipient", "transport", "delivery")
        }
    target = ROOT / "demo/out" / movie
    from publish_global import replace_media
    from artifacts import media_digest
    staged=target.with_stem(target.stem+'-publishing')
    shutil.copyfile(folder / 'demo.mp4', staged)
    require(media_digest(staged)==digest,'Staged movie digest mismatch')
    replace_media(staged,target)
    evidence.update(
        movie="out/" + movie,
        sha256=digest,
        bytes=target.stat().st_size,
        qa=qa,
        allShots=[
            {k: v for k, v in shot.items() if k != "frame"} for shot in shots["shots"]
        ],
    )
    dest = ROOT / "demo/assets/cases" / case
    dest.mkdir(parents=True, exist_ok=True)
    write(dest / "evidence.json", evidence)
    write(dest / "edit-list.json", read(folder / "edit-list.json"))
    shutil.copyfile(folder / "shot-review.jpg", dest / "encoded-review.jpg")
    for name in qa["frames"]:
        shutil.copyfile(inside(folder, name), inside(dest, name))
    voices = ROOT / "demo/assets/voice" / case
    voices.mkdir(parents=True, exist_ok=True)
    index = read(folder / "voice.json")
    for clip in index:
        source = inside(folder, clip["file"])
        target = voices / source.name
        shutil.copyfile(source, target)
        clip["file"] = source.name
    write(voices / "voice.json", index)
    return evidence


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--delegation", type=Path)
    p.add_argument("--messaging", type=Path)
    p.add_argument("--date", required=True)
    p.add_argument("--native", type=Path)
    p.add_argument("--scene", choices=("delegation-provider", "delegation-assistant", "messaging"))
    p.add_argument("--approve-video-sha256")
    a = p.parse_args()
    if a.native:
        require(a.scene and not a.delegation and not a.messaging, "Select exactly one native scene")
        evidence = publish_native(a.native, a.scene, a.approve_video_sha256)
        print(json.dumps(evidence, ensure_ascii=False))
        return
    dest = ROOT / "demo/verification/cases.json"
    summary = read(dest) if dest.exists() else {"cases": {}}
    for case, folder in [("delegation", a.delegation), ("messaging", a.messaging)]:
        if folder:
            summary["cases"][case] = publish(folder, case)
    summary["checkedAt"] = a.date
    write(dest, summary)
    print(
        json.dumps(
            {
                k: {"movie": v["movie"], "sha256": v["sha256"], "qa": v["qa"]["result"]}
                for k, v in summary["cases"].items()
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
