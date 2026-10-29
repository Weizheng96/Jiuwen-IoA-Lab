"""Incomplete or mismatched evidence must not be promoted to a formal movie."""

import copy

import pytest
from demo.artifacts import (
    inside,
    media_digest,
    validate_real_case,
    validate_recording,
    validate_reviews,
    waiting_was_cut,
)


@pytest.fixture
def delegation():
    return {
        "case": "delegation",
        "screens": "real browser pages",
        "providerExecution": "swarm",
        "fixtures": [],
        "browserErrors": [],
        "cardDigest": "selected",
        "focusedCardDigest": "selected",
        "quotes": [{"cardDigest": "basic", "total": 259}, {"cardDigest": "selected", "total": 299}],
        "delegation": {
            "cardDigest": "selected",
            "state": "completed",
            "paid": "299.00",
            "signatures": {
                "1": True,
                "2": True,
                "3": True,
                **{s: [True] for s in ("4", "5", "6", "7")},
            },
        },
        "bookingDemo": [{"cancelled": 1}],
    }


def test_formal_contract_rejects_missing_evidence(delegation):
    validate_real_case(delegation)
    bad = copy.deepcopy(delegation)
    bad["focusedCardDigest"] = "basic"
    with pytest.raises(ValueError, match="same contract"):
        validate_real_case(bad)
    for slot in ("1", "4", "5", "6", "7"):
        bad = copy.deepcopy(delegation)
        bad["delegation"]["signatures"].pop(slot)
        with pytest.raises(ValueError, match="signatures"):
            validate_real_case(bad)
    bad = copy.deepcopy(delegation)
    bad["delegation"]["paid"] = "0"
    with pytest.raises(ValueError, match="payment"):
        validate_real_case(bad)
    bad = copy.deepcopy(delegation)
    bad["fixtures"] = ["sample-provider"]
    with pytest.raises(ValueError, match="fixtures"):
        validate_real_case(bad)


def test_recorded_acceptance_mode_rejects_submission_only():
    proof = {
        "case": "messaging",
        "screens": "real browser pages",
        "fixtures": [],
        "browserErrors": [],
        "preview": True,
        "accessControl": {"directSendStatus": 403},
        "forward": {"status": "delivered", "transport": "jiuwen-web"},
        "busy": {"status": "no_time", "reason": "calendar_conflict"},
        "meeting": {"status": "invite_submitted", "transport": "agentmail-calendar"},
        "outlook": {"status": "awaiting_actual_acceptance"},
    }
    validate_real_case(proof)
    with pytest.raises(ValueError, match="Outlook"):
        validate_real_case(proof, preview=False)
    proof["preview"] = False
    proof["screens"] = "real browser pages"
    with pytest.raises(ValueError, match="Outlook"):
        validate_recording(proof)


def test_capture_time_origins_and_artifact_scope(tmp_path):
    first = {"start": 100, "duration": 10}
    assert waiting_was_cut(first, {"start": 120, "duration": 10})
    assert not waiting_was_cut(first, {"start": 120, "duration": 10, "source": "tail.webm"})
    assert inside(tmp_path, "voice/clip.mp3").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        inside(tmp_path, "../private.env")


def test_changed_movie_cannot_reuse_old_reviews(tmp_path):
    movie = tmp_path / "demo.mp4"
    movie.write_bytes(b"reviewed-encoded-content")
    review = {"result": "passed", "preview": False, "videoSha256": media_digest(movie)}
    validate_reviews({}, review, review, movie)
    movie.write_bytes(b"different-content-after-review")
    with pytest.raises(ValueError, match="changed after review"):
        validate_reviews({}, review, review, movie)


def narrated_message():
    return {'case':'messaging','screens':'real browser pages','fixtures':[],'browserErrors':[],
        'preview':False,'acceptancePresentation':'narrated','presentationScope':'platform-message-flow','outlookFootageIncluded':False,
        'accessControl':{'directSendStatus':403},'forward':{'status':'delivered','transport':'jiuwen-web'},
        'busy':{'status':'no_time','reason':'calendar_conflict'},
        'meeting':{'status':'invite_submitted','transport':'agentmail-calendar','delivery':'pending_provider_send'},
        'outlook':{'status':'awaiting_actual_acceptance'}}


def test_narrated_platform_scope_keeps_actual_pending_states():
    proof=narrated_message();before=copy.deepcopy(proof)
    validate_real_case(proof);validate_recording(proof)
    assert proof==before and proof['meeting']['delivery']=='pending_provider_send'
    proof.pop('acceptancePresentation')
    with pytest.raises(ValueError,match='Outlook'):validate_real_case(proof)


def test_narrated_scope_cannot_claim_acceptance_or_silently_change_scope():
    for field,value in [('presentationScope','full-email-acceptance'),('outlookFootageIncluded',True),('acceptancePresentation','unknown')]:
        proof=narrated_message();proof[field]=value
        with pytest.raises(ValueError):validate_real_case(proof)
    proof=narrated_message();proof['outlook']['status']='accepted_and_visible'
    with pytest.raises(ValueError,match='cannot claim'):validate_real_case(proof)


def test_encoded_review_is_bound_to_acceptance_presentation(tmp_path):
    movie=tmp_path/'demo.mp4';movie.write_bytes(b'checked-movie')
    proof=narrated_message();review={'result':'passed','preview':False,'videoSha256':media_digest(movie),'acceptancePresentation':'narrated'}
    validate_reviews(proof,review,review,movie)
    changed={**review,'acceptancePresentation':'recorded'}
    with pytest.raises(ValueError,match='presentation changed'):validate_reviews(proof,changed,review,movie)
