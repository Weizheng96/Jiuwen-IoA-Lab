"""Validate dimensions, real evidence, narration timing and exported review frames."""

import argparse, json, re, subprocess
from pathlib import Path
from artifacts import media_digest, read_json, validate_recording, write_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--recording", type=Path, required=True)
    args = p.parse_args()
    folder = args.recording.resolve()
    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proof = read_json(folder / "evidence.json")
    video = folder / ("local-test.mp4" if proof.get("fixtures") else "demo.mp4")
    validate_recording(proof)
    result = subprocess.run(
        [ff, "-hide_banner", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert "1920x1080" in result.stderr and "Audio:" in result.stderr
    mean = re.search(r"mean_volume: ([-\d.]+)", result.stderr)
    assert mean and float(mean[1]) > -45, "Narration is absent or too quiet"
    voice = read_json(folder / "voice.json")
    for i, clip in enumerate(voice):
        end = voice[i + 1]["at"] if i + 1 < len(voice) else proof["duration"]
        assert clip["at"] + clip["duration"] <= end + 0.1, "Narration overlaps or is truncated"
    frames = []
    for i, fraction in enumerate((0.1, 0.5, 0.9)):
        frame = folder / f"review-{i+1}.png"
        subprocess.run(
            [
                ff,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(proof["duration"] * fraction),
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(frame),
            ],
            check=True,
        )
        frames.append(frame.name)
    qa = {
        "result": "preview-passed" if proof.get("preview") else "passed",
        "dimensions": "1920x1080",
        "meanVolumeDb": float(mean[1]),
        "narrationClips": len(voice),
        "duration": proof["duration"],
        "frames": frames,
        "fixtures": proof.get("fixtures", []),
        "outlook": proof.get("outlook"),
        "preview": proof.get("preview", False),
    }
    if proof.get('acceptancePresentation'):
        qa['acceptancePresentation']=proof['acceptancePresentation']
        qa['presentationScope']=proof['presentationScope']
    qa["videoSha256"] = media_digest(video)
    write_json(folder / "qa.json", qa)
    print(json.dumps(qa, ensure_ascii=False))


if __name__ == "__main__":
    main()
