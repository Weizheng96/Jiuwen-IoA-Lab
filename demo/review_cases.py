"""Inspect every encoded shot and its matching narration window."""

import argparse
import re
import subprocess
from pathlib import Path
from artifacts import media_digest, read_json, validate_recording, write_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--recording", type=Path, required=True)
    a = p.parse_args()
    folder = a.recording.resolve()
    import imageio_ffmpeg
    from PIL import Image, ImageDraw

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proof = read_json(folder / "evidence.json")
    validate_recording(proof)
    edl = read_json(folder / "edit-list.json")
    voice = read_json(folder / "voice.json")
    review = folder / "shot-review"
    review.mkdir(exist_ok=True)
    report = []
    for shot, clip in zip(edl, voice, strict=True):
        assert shot["id"] == clip["id"] and shot["finalStart"] == clip["at"]
        image = review / (shot["id"] + ".png")
        subprocess.run(
            [
                ff,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(shot["finalStart"] + min(shot["duration"] / 2, 4)),
                "-i",
                str(folder / "demo.mp4"),
                "-frames:v",
                "1",
                str(image),
            ],
            check=True,
        )
        result = subprocess.run(
            [
                ff,
                "-hide_banner",
                "-ss",
                str(clip["at"] + 0.1),
                "-t",
                str(max(0.5, clip["duration"] - 0.2)),
                "-i",
                str(folder / "demo.mp4"),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        volume = re.search(r"mean_volume: ([-\d.]+)", result.stderr)
        assert volume and float(volume[1]) > -38, shot["id"]
        report.append(
            {
                "id": shot["id"],
                "meanVolumeDb": float(volume[1]),
                "frame": str(image.relative_to(folder)),
                "narrationWithinShot": clip["duration"] <= shot["duration"],
            }
        )
    canvas = Image.new("RGB", (1440, ((len(report) + 2) // 3) * 294), "#071321")
    draw = ImageDraw.Draw(canvas)
    for i, row in enumerate(report):
        im = Image.open(folder / row["frame"]).convert("RGB")
        im.thumbnail((480, 270))
        x = (i % 3) * 480
        y = (i // 3) * 294
        canvas.paste(im, (x, y))
        draw.text(
            (x + 8, y + 272), row["id"] + "  " + str(row["meanVolumeDb"]) + " dB", fill="white"
        )
    canvas.save(folder / "shot-review.jpg")
    write_json(
        folder / "shot-review.json",
        {
            "shots": report,
            "result": "passed",
            "preview": proof.get("preview", False),
            "acceptancePresentation":proof.get('acceptancePresentation','recorded'),
            "videoSha256": media_digest(folder / "demo.mp4"),
        },
    )
    print("Every encoded shot and narration window passed:", len(report))


if __name__ == "__main__":
    main()
