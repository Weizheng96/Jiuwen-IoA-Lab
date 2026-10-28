"""Publish a checked global film and its sanitised, independently captured sources."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from artifacts import read_json, require, write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--recording", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    a = p.parse_args()
    recording, source = a.recording.resolve(), a.source.resolve()
    proof = read_json(recording / "verification.json")
    run = read_json(source / "public-run.json")
    movie = recording / "demo.mp4"
    require(
        proof["qa"]["result"] == "passed"
        and proof["sha256"] == hashlib.sha256(movie.read_bytes()).hexdigest(),
        "Unchecked global movie",
    )
    require(
        not run["proof"]["errors"] and not run["proof"]["browserErrors"],
        "Real capture failed",
    )
    require(
        run["proof"]["ui"]["residualWiresAfterIdle"] == 0
        and run["proof"]["ui"]["componentClickAndEscape"],
        "UI verification failed",
    )
    require(
        proof["monitor"]["fullScreenOnly"] and proof["qa"]["finiteRandomEvents"],
        "Global choreography checks missing",
    )
    import imageio_ffmpeg

    require(
        run["proof"].get("mixedActivity", {}).get("passed"),
        "Real capture must mix delegation and P2P in every complete time window",
    )

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    dest = ROOT / "demo/assets/run/global"
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(run["monitorTrim"]),
            "-i",
            str(source / "monitor.webm"),
            "-t",
            str(run["proof"]["monitorSeconds"]),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dest / "monitor.mp4"),
        ],
        check=True,
    )
    run["sourceMonitorTrim"] = run["monitorTrim"]
    run["monitorTrim"] = 0
    write_json(dest / "public-run.json", run)
    shutil.copyfile(movie, ROOT / proof["video"])
    voice = ROOT / "demo/assets/voice/global"
    voice.mkdir(parents=True, exist_ok=True)
    cues = read_json(recording / "voice.json")
    for cue in cues:
        src = recording / cue["file"]
        dst = voice / src.name
        shutil.copyfile(src, dst)
        cue["file"] = dst.name
    write_json(voice / "voice.json", cues)
    frames = ROOT / "demo/assets/run/global/review"
    frames.mkdir(exist_ok=True)
    # Review encoded frames, not just screenshots from before encoding.
    from PIL import Image, ImageDraw

    tiles = []
    for cue in cues:
        dst = frames / (cue["id"] + ".jpg")
        subprocess.run(
            [
                ff,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(cue["at"] + min(2, cue["duration"] / 2)),
                "-i",
                str(movie),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(dst),
            ],
            check=True,
        )
        with Image.open(dst) as img:
            thumb = img.resize((480, 270))
            tiles.append(thumb.copy())
    contact = Image.new("RGB", (1920, 5 * 292), "#0a1424")
    draw = ImageDraw.Draw(contact)
    for i, (cue, tile) in enumerate(zip(cues, tiles)):
        x, y = (i % 4) * 480, (i // 4) * 292
        contact.paste(tile, (x, y))
        draw.text((x + 8, y + 274), cue["id"], fill="white")
    contact.save(frames / "contact.jpg", quality=90)
    proof["qa"]["reviewFrames"] = [
        str((frames / (c["id"] + ".jpg")).relative_to(ROOT)).replace("\\", "/")
        for c in cues
    ]
    value = next(c for c in cues if c["id"] == "value")
    transition_files = []
    transition_strip = Image.new("RGB", (1920, 270), "#0a1424")
    for i, (name, offset) in enumerate(
        (("before", -0.15), ("users-fade", 0.65), ("blend", 1.25), ("after", 1.8))
    ):
        image = frames / ("transition-" + name + ".jpg")
        subprocess.run(
            [
                ff,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(value["at"] + offset),
                "-i",
                str(movie),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(image),
            ],
            check=True,
        )
        transition_files.append(str(image.relative_to(ROOT)).replace("\\", "/"))
        with Image.open(image) as img:
            transition_strip.paste(img.resize((480, 270)), (i * 480, 0))
    transition_strip.save(frames / "transition.jpg", quality=90)
    proof["qa"]["reviewFrames"].extend(transition_files)
    proof["qa"]["transitionReview"] = str(
        (frames / "transition.jpg").relative_to(ROOT)
    ).replace("\\", "/")
    proof["qa"]["reviewedFromFinalEncoding"] = True
    proof["monitor"]["sourceClipSha256"] = hashlib.sha256(
        (dest / "monitor.mp4").read_bytes()
    ).hexdigest()
    write_json(ROOT / "demo/verification/global.json", proof)
    shutil.copyfile(
        recording / "scene-checks.json",
        ROOT / "demo/verification/global-scene-checks.json",
    )
    write_json(
        ROOT / "demo/verification/global-frames.json",
        {
            "sha256": proof["sha256"],
            "frames": [
                str((frames / (c["id"] + ".jpg")).relative_to(ROOT)) for c in cues
            ],
            "contact": str((frames / "contact.jpg").relative_to(ROOT)),
            "realCaptureLoop": "demo/全局视角/播放.html",
            "transitionFrames": transition_files,
            "transitionContact": proof["qa"]["transitionReview"],
        },
    )
    print(
        json.dumps(
            {
                "sha256": proof["sha256"],
                "duration": proof["qa"]["duration"],
                "experiment": run["proof"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
