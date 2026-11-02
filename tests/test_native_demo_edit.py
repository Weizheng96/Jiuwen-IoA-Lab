"""Native-film candidates bind approved narration and actual input bytes.

The planning fixtures contain inert bytes, not recordings or business evidence.
They deliberately remain review candidates until independent business facts exist.
"""

import copy
import hashlib
import json

import pytest

from demo import artifacts, edit_cases
from demo.approved_voice import voice_plan
from demo.review_story import APPROVAL


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def native_inputs(tmp_path):
    """All approved shot/audio identities, with no fabricated business result."""
    folder = tmp_path / "recording"
    folder.mkdir()
    source = folder / "sources" / "browser.mp4"
    source.parent.mkdir()
    source.write_bytes(b"inert source bytes: no decoder or business execution")
    timing = folder / "voice" / "voice-paced.json"
    timing.parent.mkdir()
    voices = []
    segments = []
    for index, clip in enumerate(voice_plan("messaging")["clips"]):
        audio = timing.parent / (clip["id"] + ".mp3")
        audio.write_bytes(("inert audio: " + clip["id"]).encode())
        voices.append({
            **clip,
            "at": 999,
            "duration": 1.25 + (index % 3) / 10,
            "file": audio.name,
            "audioSha256": _sha(audio.read_bytes()),
        })
        segments.append({
            "id": clip["id"],
            "layout": "single",
            "panels": [{
                "role": "native",
                "label": "isolated media fixture",
                "source": "sources/browser.mp4",
                "sha256": _sha(source.read_bytes()),
                "start": index * 5,
                "duration": 4,
            }],
        })
    proof = {
        "format": "ioalab.native-demo/1",
        "scene": "messaging",
        "case": "messaging",
        "approvalSha256": APPROVAL["contentSha256"],
        "screens": "real browser pages",
        "fixtures": [],
        "browserErrors": [],
        "acceptancePresentation": "narrated",
        "presentationScope": "platform-message-flow",
        "outlookFootageIncluded": False,
        "evidence": {},
        "shotEvidence": {},
        "business": {},
    }
    _save(folder / "evidence.json", proof)
    _save(folder / "segments.json", segments)
    _save(timing, voices)
    return {
        "folder": folder,
        "source": source,
        "timing": timing,
        "voices": voices,
        "segments": segments,
        "proof": proof,
    }


def _plan(inputs, *, review=True):
    return edit_cases.build_native_plan(
        inputs["folder"], "messaging", inputs["timing"], review_candidate=review
    )


def test_native_plan_uses_measured_audio_and_does_not_rewrite_inputs(native_inputs):
    inputs = native_inputs
    before_entries = set(inputs["folder"].rglob("*"))
    before = {path: path.read_bytes() for path in inputs["folder"].rglob("*") if path.is_file()}
    plan = _plan(inputs)
    assert plan["scene"] == "messaging"
    assert [shot["id"] for shot in plan["shots"]] == [v["id"] for v in inputs["voices"]]
    at = 0
    for shot, voice, measured in zip(plan["shots"], plan["voices"], inputs["voices"]):
        assert shot["duration"] == pytest.approx(measured["duration"] + .55)
        assert shot["finalStart"] == pytest.approx(at)
        assert voice["at"] == pytest.approx(at)
        assert voice["voice"] == measured["voice"]
        assert voice["audioSha256"] == measured["audioSha256"]
        at += shot["duration"]
    assert plan["pending"], "Inert fixtures must not claim complete business evidence"
    assert all(path.read_bytes() == content for path, content in before.items())
    assert set(inputs["folder"].rglob("*")) == before_entries


def test_missing_shot_is_explicit_review_placeholder(native_inputs):
    inputs = native_inputs
    removed = inputs["segments"].pop()
    _save(inputs["folder"] / "segments.json", inputs["segments"])
    plan = _plan(inputs)
    placeholder = next(shot for shot in plan["shots"] if shot["id"] == removed["id"])
    assert placeholder["pending"] is True
    assert placeholder["pendingReasons"]
    assert plan["pending"]


def test_missing_source_is_explicit_review_placeholder(native_inputs):
    inputs = native_inputs
    inputs["source"].unlink()
    plan = _plan(inputs)
    assert all(shot["pending"] and shot["pendingReasons"] for shot in plan["shots"])
    assert plan["pending"]


def test_changed_source_hash_cannot_be_downgraded_to_review(native_inputs):
    native_inputs["source"].write_bytes(b"changed after source hash was bound")
    with pytest.raises(ValueError, match="(?i)(hash|digest|sha)"):
        _plan(native_inputs)


@pytest.mark.parametrize("field,value", [
    ("start", -1), ("start", float("nan")), ("start", float("inf")),
    ("duration", 0), ("duration", -1), ("duration", float("nan")),
    ("duration", float("inf")),
])
def test_illegal_trim_is_rejected_even_for_review(native_inputs, field, value):
    inputs = native_inputs
    inputs["segments"][0]["panels"][0][field] = value
    _save(inputs["folder"] / "segments.json", inputs["segments"])
    with pytest.raises(ValueError):
        _plan(inputs)


def test_source_cannot_escape_the_recording_directory(native_inputs):
    inputs = native_inputs
    outside = inputs["folder"].parent / "outside.mp4"
    outside.write_bytes(b"outside the declared recording")
    panel = inputs["segments"][0]["panels"][0]
    panel.update(source="../outside.mp4", sha256=_sha(outside.read_bytes()))
    _save(inputs["folder"] / "segments.json", inputs["segments"])
    with pytest.raises(ValueError, match="(?i)(escape|outside)"):
        _plan(inputs)


def test_changed_audio_hash_cannot_be_downgraded_to_review(native_inputs):
    inputs = native_inputs
    audio = inputs["timing"].parent / inputs["voices"][0]["file"]
    audio.write_bytes(b"changed after narration was hashed")
    with pytest.raises(ValueError, match="(?i)(audio|hash|digest|sha)"):
        _plan(inputs)


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_measured_audio_duration_is_not_a_review_candidate(native_inputs, duration):
    inputs = native_inputs
    inputs["voices"][0]["duration"] = duration
    _save(inputs["timing"], inputs["voices"])
    with pytest.raises(ValueError):
        _plan(inputs)


@pytest.mark.parametrize("field,value", [
    ("id", "unapproved-shot"), ("text", "未经批准的字幕"), ("voice", "未经批准的旁白"),
])
def test_unapproved_narration_identity_is_rejected_in_review(native_inputs, field, value):
    inputs = native_inputs
    inputs["voices"][0][field] = value
    _save(inputs["timing"], inputs["voices"])
    with pytest.raises(ValueError):
        _plan(inputs)


def test_wrong_approval_hash_is_never_a_review_candidate(native_inputs):
    inputs = native_inputs
    inputs["proof"]["approvalSha256"] = "0" * 64
    _save(inputs["folder"] / "evidence.json", inputs["proof"])
    with pytest.raises(ValueError, match="(?i)(approv|screenplay)"):
        _plan(inputs)


def test_proof_scene_must_match_the_requested_approved_scene(native_inputs):
    inputs = native_inputs
    inputs["proof"]["scene"] = "delegation-assistant"
    _save(inputs["folder"] / "evidence.json", inputs["proof"])
    with pytest.raises(ValueError, match="(?i)scene"):
        _plan(inputs)


def test_business_validation_is_readonly_and_missing_facts_are_not_complete(native_inputs):
    proof = native_inputs["proof"]
    before = copy.deepcopy(proof)
    assert artifacts.validate_native_demo(proof, require_complete=False)
    assert proof == before
    with pytest.raises(ValueError):
        artifacts.validate_native_demo(proof)
    assert proof == before


def test_review_candidate_is_refused_before_reading_publication_artifacts(native_inputs):
    from demo import publish_cases

    inputs = native_inputs
    inputs["proof"]["reviewCandidate"] = True
    _save(inputs["folder"] / "evidence.json", inputs["proof"])
    # No qa.json, shot-review.json or movie exists. Refusal must be the candidate
    # boundary, not an unrelated missing-file error or publication side effect.
    with pytest.raises(ValueError, match="(?i)(candidate|review)"):
        publish_cases.publish_native(inputs["folder"], "messaging")
    assert not (inputs["folder"] / "demo.mp4").exists()


def _object_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return "sha256:" + _sha(encoded)


def _confirmation(name):
    return {
        "reference": "confirmation-" + name,
        "operationId": "operation-" + name,
        "bindingDigest": _object_digest({"object": name}),
        "decision": "approved",
        "source": "actual-isolated-ui",
    }


@pytest.fixture
def complete_messaging(native_inputs):
    """Synthetic complete facts exercise gates; they never stand for a real run."""
    inputs = native_inputs
    proof = inputs["proof"]
    audit = inputs["folder"] / "evidence" / "audit.json"
    _save(audit, {"kind": "inert-unit-test-audit"})
    proof["evidence"] = {"audit": {"file": "evidence/audit.json", "sha256": _sha(audit.read_bytes())}}
    proof["shotEvidence"] = {shot["id"]: ["audit"] for shot in voice_plan("messaging")["clips"]}
    first = {
        "messageRef": "message-conflict",
        "interactionRef": "one-interaction",
        "outcome": "completed",
        "output": {
            "status": "conflict", "inviteSubmitted": False, "reason": "busy",
            "suggestedStart": "2026-11-04T11:30:00+08:00",
            "suggestedEnd": "2026-11-04T12:00:00+08:00", "timeZone": "Asia/Shanghai",
        },
        "evidenceRef": "audit",
    }
    second = {
        "messageRef": "message-rescheduled",
        "interactionRef": "one-interaction",
        "outcome": "completed",
        "output": {
            "status": "invite_submitted", "inviteSubmitted": True,
            "calendar": "awaiting_acceptance", "reservationId": "one-reservation",
        },
        "evidenceRef": "audit",
    }
    for message in (first, second):
        message["resultDigest"] = _object_digest({"outcome": message["outcome"], "output": message["output"]})
    interfaces = [{"name": "meeting_rule", "revision": 2}, {"name": "forward_rule", "revision": 1}]
    proof["business"] = {
        "rules": [{
            "name": item["name"], "revision": item["revision"],
            "contentDigest": _object_digest(item), "allowedSenders": ["allowed-account"],
            "confirmation": _confirmation(item["name"]), "evidenceRef": "audit",
        } for item in interfaces],
        "directory": {
            "allowed": {"accountId": "allowed-account", "status": "completed", "interfaces": interfaces,
                        "evidenceRef": "audit"},
            "other": {"accountId": "other-account", "kind": "empty_directory", "status": "completed",
                      "interfaces": [], "evidenceRef": "audit"},
        },
        "forward": {"notificationRef": "notification-1", "nativeSessionId": "web-native-notification",
                    "visible": True, "read": "unknown", "evidenceRef": "audit"},
        "interactionRef": "one-interaction",
        "messages": [first, second],
        "receiverSessionIds": ["web-native-receiver", "web-native-receiver"],
        "reschedule": {"generatedBy": "sender-model", "basedOnMessageRef": first["messageRef"],
                       "sentMessageRef": second["messageRef"], "scriptedSecondInput": False,
                       "operatorProvidedSecondTime": False, "evidenceRef": "audit"},
        "invitation": {
            "transport": "agentmail-calendar", "reservationId": "one-reservation",
            "requestId": "one-request", "providerEventId": "one-provider-event", "uid": "one-uid",
            "organizerRole": "configured-organizer", "recipientRole": "configured-recipient",
            "organizerMatchedConfigured": True, "recipientMatchedConfigured": True,
            "recipientStatus": "needs_action", "externalEventMatched": True,
            "delivery": "submitted", "auditSha256": _sha(audit.read_bytes()), "evidenceRef": "audit",
        },
        "closed": {"interactionRef": "one-interaction", "closed": True,
                   "firstResultPreserved": True, "evidenceRef": "audit"},
    }
    first_shot = inputs["segments"][0]
    first_shot["layout"] = "quad"
    first_shot["panels"] = [{**first_shot["panels"][0], "role": role} for role in
                            ("wang-platform", "zhao-platform", "wang-native", "zhao-native")]
    _save(inputs["folder"] / "evidence.json", proof)
    _save(inputs["folder"] / "segments.json", inputs["segments"])
    return inputs


def test_complete_message_evidence_and_quad_mapping_build_a_formal_plan(complete_messaging):
    inputs = complete_messaging
    before = copy.deepcopy(inputs["proof"])
    assert artifacts.validate_native_demo(inputs["proof"]) == []
    plan = _plan(inputs, review=False)
    assert plan["pending"] == [] and plan["reviewCandidate"] is False
    assert plan["shots"][0]["layout"] == "quad"
    assert plan["proof"] == before == inputs["proof"]


@pytest.mark.parametrize("mutation", [
    "same-message", "different-interaction", "overwritten-conflict", "stale-result-digest",
    "accepted-recipient", "unmatched-external-event", "hidden-notification", "scripted-reschedule",
    "forgotten-first-result",
])
def test_message_completion_preserves_exact_results_and_external_pending_state(complete_messaging, mutation):
    proof = complete_messaging["proof"]
    business = proof["business"]
    first, second = business["messages"]
    if mutation == "same-message":
        second["messageRef"] = first["messageRef"]
    elif mutation == "different-interaction":
        second["interactionRef"] = "another-interaction"
    elif mutation == "overwritten-conflict":
        first["output"] = copy.deepcopy(second["output"])
        first["resultDigest"] = _object_digest({"outcome": first["outcome"], "output": first["output"]})
    elif mutation == "stale-result-digest":
        first["output"]["reason"] = "changed after recording"
    elif mutation == "accepted-recipient":
        business["invitation"]["recipientStatus"] = "accepted"
    elif mutation == "unmatched-external-event":
        business["invitation"]["externalEventMatched"] = False
    elif mutation == "hidden-notification":
        business["forward"]["visible"] = False
    elif mutation == "scripted-reschedule":
        business["reschedule"]["generatedBy"] = "script"
    elif mutation == "forgotten-first-result":
        business["closed"]["firstResultPreserved"] = False
    issues = artifacts.validate_native_demo(proof, require_complete=False)
    assert issues
    with pytest.raises(ValueError):
        artifacts.validate_native_demo(proof)


def test_actual_empty_directory_and_actual_rejection_remain_distinct(complete_messaging):
    proof = complete_messaging["proof"]
    assert artifacts.validate_native_demo(proof) == []
    other = proof["business"]["directory"]["other"]
    other.update(kind="rejected", status=403)
    assert artifacts.validate_native_demo(proof) == []
    other["kind"] = "empty_directory"
    with pytest.raises(ValueError, match="(?i)(directory|rejected)"):
        artifacts.validate_native_demo(proof)


@pytest.mark.parametrize("sessions", [
    pytest.param(None, id="missing"),
    pytest.param([], id="empty-list"),
    pytest.param(["web-native-receiver"], id="only-one"),
    pytest.param(["web-native-receiver"] * 3, id="three"),
    pytest.param(["", ""], id="empty-values"),
    pytest.param(["first-receiver", "second-receiver"], id="different-sessions"),
])
def test_both_message_rounds_require_the_same_nonempty_receiver_session(complete_messaging, sessions):
    proof = complete_messaging["proof"]
    assert artifacts.validate_native_demo(proof) == []
    if sessions is None:
        proof["business"].pop("receiverSessionIds")
    else:
        proof["business"]["receiverSessionIds"] = sessions
    assert artifacts.validate_native_demo(proof, require_complete=False)
    with pytest.raises(ValueError):
        artifacts.validate_native_demo(proof)


@pytest.mark.parametrize("field", ["scriptedSecondInput", "operatorProvidedSecondTime"])
@pytest.mark.parametrize("value", [pytest.param("missing", id="missing"), True, None])
def test_sender_model_reschedule_requires_explicit_unscripted_input(complete_messaging, field, value):
    proof = complete_messaging["proof"]
    assert artifacts.validate_native_demo(proof) == []
    # A top-level denial cannot substitute for the missing per-reschedule fact.
    proof[field] = False
    if value == "missing":
        proof["business"]["reschedule"].pop(field)
    else:
        proof["business"]["reschedule"][field] = value
    assert artifacts.validate_native_demo(proof, require_complete=False)
    with pytest.raises(ValueError):
        artifacts.validate_native_demo(proof)


@pytest.mark.parametrize("field", ["scriptedSecondInput", "operatorProvidedSecondTime"])
def test_top_level_scripted_input_cannot_contradict_reschedule_denial(complete_messaging, field):
    proof = complete_messaging["proof"]
    proof[field] = True
    assert proof["business"]["reschedule"][field] is False
    assert artifacts.validate_native_demo(proof, require_complete=False)
    with pytest.raises(ValueError):
        artifacts.validate_native_demo(proof)


def test_allowed_sender_cannot_supply_the_other_account_directory_control(complete_messaging):
    proof = complete_messaging["proof"]
    directory = proof["business"]["directory"]
    directory["other"]["accountId"] = directory["allowed"]["accountId"]
    assert artifacts.validate_native_demo(proof, require_complete=False)
    with pytest.raises(ValueError):
        artifacts.validate_native_demo(proof)


def test_missing_business_and_missing_shot_block_complete_edit(complete_messaging):
    inputs = complete_messaging
    inputs["proof"]["business"]["messages"].pop()
    _save(inputs["folder"] / "evidence.json", inputs["proof"])
    with pytest.raises(ValueError, match="(?i)(complete|message)"):
        _plan(inputs, review=False)
    assert _plan(inputs)["pending"]


def test_native_public_projection_omits_private_audit_and_unapproved_nested_fields(complete_messaging):
    from demo.publish_cases import public_native_business

    proof = complete_messaging["proof"]
    business = proof["business"]
    marker = "PRIVATE-UNIT-TEST-MARKER"
    business["invitation"].update(organizer=marker + "@organizer.invalid", recipient=marker + "@recipient.invalid",
                                  email=marker + "@mail.invalid", privateBody=marker)
    business["messages"][0]["output"]["privateBody"] = marker
    business["directory"]["allowed"]["interfaces"][0]["privateBody"] = marker
    original = copy.deepcopy(proof)
    public = public_native_business(proof)
    encoded = json.dumps(public)
    assert marker not in encoded
    assert "evidenceRef" not in encoded
    assert "organizer" not in public["invitation"] and "recipient" not in public["invitation"]
    assert public["invitation"]["recipientStatus"] == "needs_action"
    assert public["messages"][0]["output"]["status"] == "conflict"
    assert proof == original


@pytest.fixture
def complete_delegation(complete_messaging):
    proof = copy.deepcopy(complete_messaging["proof"])
    proof.update(scene="delegation-assistant", case="delegation", acceptancePresentation="recorded")
    proof.pop("presentationScope", None)
    proof["shotEvidence"] = {shot["id"]: ["audit"] for shot in voice_plan(proof["scene"])["clips"]}
    quotes = [{"cardDigest": "card-" + str(index), "signed": True, "total": amount, "evidenceRef": "audit"}
              for index, amount in enumerate(("239.00", "259.00", "299.00"))]
    proof["business"] = {
        "quotes": quotes,
        "focusedCardDigest": quotes[2]["cardDigest"],
        "contract": {"cardDigest": quotes[2]["cardDigest"], "state": "completed", "paid": "299.00",
                     "signatures": {"1": True, "2": True, "3": True,
                                    **{slot: [True] for slot in ("4", "5", "6", "7")}}, "evidenceRef": "audit"},
        "payment": {"cardDigest": quotes[2]["cardDigest"], "voucherVerified": True,
                    "amount": "299.00", "closed": True, "evidenceRef": "audit"},
        "confirmations": {name: {**_confirmation(name), "evidenceRef": "audit"} for name in
                          ("authorization", "contract", "registration", "acceptance")},
        "onboarding": {"registrationRecorded": True, "configurationBoundaryPassed": True, "evidenceRef": "audit"},
    }
    return proof


@pytest.mark.parametrize("scene", ["delegation-provider", "delegation-assistant"])
def test_both_delegation_views_bind_same_selected_payment_and_four_confirmations(complete_delegation, scene):
    proof = complete_delegation
    proof["scene"] = scene
    proof["shotEvidence"] = {shot["id"]: ["audit"] for shot in voice_plan(scene)["clips"]}
    assert artifacts.validate_native_demo(proof) == []
    gate = proof["business"]["confirmations"]["acceptance"]
    gate.update(decision="expired", historicallyApproved=True)
    assert artifacts.validate_native_demo(proof) == []
    gate["historicallyApproved"] = False
    with pytest.raises(ValueError, match="(?i)approval"):
        artifacts.validate_native_demo(proof)


@pytest.mark.parametrize("mutation", [
    "two-proposals", "missing-signature", "other-card-payment", "missing-confirmation",
    "one-confirmation-reused", "missing-amounts", "configuration-boundary-failed",
])
def test_incomplete_delegation_is_never_promoted(complete_delegation, mutation):
    proof = complete_delegation
    business = proof["business"]
    if mutation == "two-proposals":
        business["quotes"].pop(0)
    elif mutation == "missing-signature":
        business["contract"]["signatures"].pop("7")
    elif mutation == "other-card-payment":
        business["payment"]["cardDigest"] = "another-card"
    elif mutation == "missing-confirmation":
        business["confirmations"].pop("registration")
    elif mutation == "one-confirmation-reused":
        gate = business["confirmations"]["authorization"]
        business["confirmations"] = {name: copy.deepcopy(gate) for name in business["confirmations"]}
    elif mutation == "missing-amounts":
        business["quotes"][2].pop("total")
        business["contract"].pop("paid")
        business["payment"].pop("amount")
    elif mutation == "configuration-boundary-failed":
        business["onboarding"]["configurationBoundaryPassed"] = False
    assert artifacts.validate_native_demo(proof, require_complete=False)
    with pytest.raises(ValueError):
        artifacts.validate_native_demo(proof)


@pytest.fixture
def publishable_native_files(complete_messaging, monkeypatch):
    """Exercise publication gates without pretending to decode the inert MP4."""
    from demo import publish_cases

    inputs = complete_messaging
    folder, proof = inputs["folder"], inputs["proof"]
    private_marker = "PRIVATE-AUDIT-FILE-MUST-STAY-LOCAL"
    audit = folder / "evidence" / "audit.json"
    _save(audit, {"unitTestPrivatePayload": private_marker})
    proof["evidence"]["audit"]["sha256"] = _sha(audit.read_bytes())
    proof["business"]["invitation"]["auditSha256"] = _sha(audit.read_bytes())
    voices = [{**voice, "file": "voice/" + voice["file"]} for voice in inputs["voices"]]
    _save(folder / "voice.json", voices)
    _save(folder / "edit-list.json", [{"id": shot["id"]} for shot in inputs["segments"]])
    for name, field in (("segments.json", "segmentsSha256"), ("voice.json", "voiceIndexSha256"),
                        ("edit-list.json", "editListSha256")):
        proof[field] = _sha((folder / name).read_bytes())
    _save(folder / "evidence.json", proof)
    movie = folder / "demo.mp4"
    movie.write_bytes(b"inert encoded video fixture: not a real QA result")
    movie_digest = _sha(movie.read_bytes())
    qa = {
        "result": "passed", "preview": False, "acceptancePresentation": "narrated",
        "videoSha256": movie_digest, "manualListening": "passed",
        "proofDigest": _object_digest(proof), "approvalSha256": proof["approvalSha256"],
    }
    review = {
        "result": "passed", "preview": False, "acceptancePresentation": "narrated",
        "videoSha256": movie_digest, "shots": [{"id": "cover", "frame": "private-local-frame.png"}],
    }
    _save(folder / "qa.json", qa)
    _save(folder / "shot-review.json", review)
    (folder / "shot-review.jpg").write_bytes(b"inert unit-test contact sheet")
    public_root = folder.parent / "isolated-publication-root"
    monkeypatch.setattr(publish_cases, "ROOT", public_root)
    return {**inputs, "publicRoot": public_root, "movieDigest": movie_digest,
            "privateMarker": private_marker, "qa": qa}


def test_native_publication_requires_current_hashes_and_never_copies_private_evidence(publishable_native_files):
    from demo.publish_cases import publish_native

    inputs = publishable_native_files
    summary = publish_native(inputs["folder"], "messaging", approved_video_sha256=inputs["movieDigest"])
    target = inputs["publicRoot"] / "demo" / summary["movie"]
    assert target.read_bytes() == (inputs["folder"] / "demo.mp4").read_bytes()
    assert summary["sha256"] == inputs["movieDigest"]
    public_files = list(inputs["publicRoot"].rglob("*"))
    assert not any(path.name == "audit.json" for path in public_files)
    assert not any(path.name == "private-local-frame.png" for path in public_files)
    assert all(inputs["privateMarker"].encode() not in path.read_bytes()
               for path in public_files if path.is_file())
    assert (inputs["folder"] / "evidence" / "audit.json").is_file()


def test_public_shot_summary_uses_only_safe_measured_fields(publishable_native_files):
    from demo.publish_cases import publish_native

    inputs = publishable_native_files
    review_file = inputs["folder"] / "shot-review.json"
    review = json.loads(review_file.read_text(encoding="utf-8"))
    marker = "PRIVATE-OPERATOR-AUDIT-NEVER-PUBLISH"
    review["shots"][0].update(meanVolumeDb=-18.25, narrationWithinShot=True,
                                operatorPrivateAudit={"address": marker, "nested": {"body": marker}})
    _save(review_file, review)
    summary = publish_native(inputs["folder"], "messaging", approved_video_sha256=inputs["movieDigest"])
    assert summary["allShots"] == [{"id": "cover", "meanVolumeDb": -18.25, "narrationWithinShot": True}]
    assert summary["business"]["receiverSessionIds"] == ["web-native-receiver", "web-native-receiver"]
    assert summary["business"]["reschedule"]["scriptedSecondInput"] is False
    assert summary["business"]["reschedule"]["operatorProvidedSecondTime"] is False
    assert marker not in json.dumps(summary)
    assert all(marker.encode() not in path.read_bytes()
               for path in inputs["publicRoot"].rglob("*") if path.is_file())


def test_unbound_contact_sheet_is_not_copied_into_publication(publishable_native_files):
    from demo.publish_cases import publish_native

    inputs = publishable_native_files
    contact_sheet = inputs["folder"] / "shot-review.jpg"
    marker = b"REPLACED-PRIVATE-CONTACT-SHEET-MUST-NOT-BE-PUBLISHED"
    contact_sheet.write_bytes(marker)
    summary = publish_native(inputs["folder"], "messaging", approved_video_sha256=inputs["movieDigest"])
    assert summary["business"]["receiverSessionIds"] == ["web-native-receiver", "web-native-receiver"]
    assert summary["business"]["reschedule"]["scriptedSecondInput"] is False
    assert summary["business"]["reschedule"]["operatorProvidedSecondTime"] is False
    public_files = list(inputs["publicRoot"].rglob("*"))
    assert not any(path.name in ("shot-review.jpg", "encoded-review.jpg") for path in public_files)
    assert all(marker not in path.read_bytes() for path in public_files if path.is_file())
    assert contact_sheet.read_bytes() == marker


@pytest.mark.parametrize("mutation", [
    "listening-pending", "unapproved-video", "changed-proof", "changed-segments", "changed-voice-index",
    "changed-edit-list", "changed-audio", "changed-source", "changed-movie",
])
def test_native_publication_rejects_stale_qa_or_missing_final_approval(publishable_native_files, mutation):
    from demo.publish_cases import publish_native

    inputs = publishable_native_files
    folder, approved = inputs["folder"], inputs["movieDigest"]
    if mutation == "listening-pending":
        inputs["qa"]["manualListening"] = "pending"
        _save(folder / "qa.json", inputs["qa"])
    elif mutation == "unapproved-video":
        approved = None
    elif mutation == "changed-proof":
        inputs["proof"]["preparation"] = {"label": "changed after encoded review"}
        _save(folder / "evidence.json", inputs["proof"])
    elif mutation == "changed-segments":
        with (folder / "segments.json").open("ab") as stream:
            stream.write(b"\n")
    elif mutation == "changed-voice-index":
        with (folder / "voice.json").open("ab") as stream:
            stream.write(b"\n")
    elif mutation == "changed-edit-list":
        with (folder / "edit-list.json").open("ab") as stream:
            stream.write(b"\n")
    elif mutation == "changed-audio":
        (inputs["timing"].parent / inputs["voices"][0]["file"]).write_bytes(b"changed narration")
    elif mutation == "changed-source":
        inputs["source"].write_bytes(b"changed native recording")
    elif mutation == "changed-movie":
        (folder / "demo.mp4").write_bytes(b"changed encoded movie")
    with pytest.raises(ValueError):
        publish_native(folder, "messaging", approved_video_sha256=approved)
    assert not inputs["publicRoot"].exists()
