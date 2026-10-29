"""Publish only checked movies and an allowlisted evidence summary; raw runs stay private."""

import argparse
import json
import shutil
from pathlib import Path
from artifacts import (
    inside,
    read_json as read,
    require,
    validate_real_case,
    validate_reviews,
    write_json as write,
)

ROOT = Path(__file__).resolve().parents[1]


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
        for key in ('providerRetake','production','verification','nativeSkillPreload','flow','userTurns','confirmationGates','authorization','registration','onboarding','executionPath','nativeConfirmations'):
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
    a = p.parse_args()
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
