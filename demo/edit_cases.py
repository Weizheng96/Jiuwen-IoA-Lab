"""Cut actual raw intervals to measured narration; retain a traceable edit decision list."""

import argparse
import copy
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from .case_story import STORIES
    from .assemble import ffpath
    from .artifacts import (inside, media_digest, read_json, require, validate_real_case,
                            waiting_was_cut, acceptance_presentation, write_json,
                            validate_native_demo, validate_native_files)
    from .approved_voice import voice_plan
else:
    from case_story import STORIES
    from assemble import ffpath
    from artifacts import (inside, media_digest, read_json, require, validate_real_case,
                           waiting_was_cut, acceptance_presentation, write_json,
                           validate_native_demo, validate_native_files)
    from approved_voice import voice_plan


def _number(value, *, positive=False):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and (value > 0 if positive else value >= 0), "Invalid measured duration or trim")
    return value


def build_native_plan(folder, scene, voice_timing, *, review_candidate=False):
    """Read and hash approved inputs; planning never decodes media or changes files."""
    folder, voice_timing = Path(folder).resolve(), Path(voice_timing).resolve()
    proof = read_json(folder / "evidence.json")
    require(proof.get("scene") == scene, "Evidence scene does not match the requested scene")
    pending = validate_native_demo(proof, require_complete=False)
    validate_native_files(folder, proof)
    approved = voice_plan(scene)
    measured = read_json(voice_timing)
    require(isinstance(measured, list) and len(measured) == len(approved["clips"]),
            "Measured audio must contain every approved shot exactly once")
    segments = read_json(folder / "segments.json")
    require(isinstance(segments, list), "Shot mapping must be a list")
    ids = [v["id"] for v in approved["clips"]]
    require(all(isinstance(s, dict) and s.get("id") in ids for s in segments) and
            len({s["id"] for s in segments}) == len(segments), "Unknown or duplicate shot mapping")
    raw = {s["id"]: s for s in segments}
    shots, voices, captions = [], [], []
    at = 0
    previous_panels = {}
    for spec, voice in zip(approved["clips"], measured, strict=True):
        key = spec["id"]
        require(all(voice.get(k) == spec[k] for k in ("id", "text", "voice")),
                "Narration differs from approved shot: " + key)
        duration = round(_number(voice.get("duration"), positive=True) + .55, 3)
        audio = inside(voice_timing.parent, voice["file"])
        require(audio.is_file() and media_digest(audio) == voice.get("audioSha256"),
                "Approved audio SHA256 mismatch: " + key)
        entry = raw.get(key)
        reasons, panels = [], []
        layout = entry.get("layout", "single") if entry else "single"
        require(layout in ("single", "dual", "quad"), "Unknown panel layout: " + key)
        expected = {"single": 1, "dual": 2, "quad": 4}[layout]
        if not entry:
            reasons.append("镜头尚未映射")
        else:
            if entry.get("pendingReason"):
                require(isinstance(entry["pendingReason"], str), "Pending reason must be text")
                reasons.append(entry["pendingReason"])
            entries = entry.get("panels", [])
            require(isinstance(entries, list) and len(entries) <= expected, "Too many panels: " + key)
            require(len({p.get("role") for p in entries}) == len(entries), "Duplicate panel role: " + key)
            if layout != "single":
                roles = {"native", "platform"} if layout == "dual" else {"wang-platform", "zhao-platform", "wang-native", "zhao-native"}
                require(all(p.get("role") in roles for p in entries), "Panel role does not match the declared layout")
            if len(entries) != expected:
                reasons.append("面板素材尚未齐全")
            for panel in entries:
                require(isinstance(panel, dict) and isinstance(panel.get("role"), str) and panel["role"] and
                        isinstance(panel.get("label"), str), "Panel role and label are required")
                start = _number(panel.get("start"))
                available = _number(panel.get("duration"), positive=True)
                source = inside(folder, panel["source"])
                require(re.fullmatch(r"[0-9a-f]{64}", str(panel.get("sha256", ""))) is not None,
                        "Panel source SHA256 is required")
                item = {**panel, "source": str(source.relative_to(folder)), "start": start, "duration": available}
                if not source.is_file():
                    reasons.append(panel["role"] + "：源文件待补")
                    item["missing"] = True
                else:
                    require(media_digest(source) == panel["sha256"], "Source SHA256 mismatch: " + key)
                    if duration > available:
                        require(panel.get("holdLastFrame") is True and isinstance(panel.get("holdReason"), str)
                                and bool(panel["holdReason"].strip()),
                                "Raw interval is too short; an explicit labelled frame hold is required: " + key)
                panels.append(item)
        waiting_cuts = []
        for panel in panels:
            prior = previous_panels.get(panel["role"])
            if prior and waiting_was_cut(prior, panel):
                waiting_cuts.append(panel["role"])
            previous_panels[panel["role"]] = {**panel, "duration": min(panel["duration"], duration)}
        if reasons:
            pending.extend(key + ": " + reason for reason in reasons)
        shots.append({"id": key, "duration": duration, "finalStart": round(at, 3), "layout": layout,
                      "panels": panels, "waitingCuts": waiting_cuts,
                      "pending": bool(reasons), "pendingReasons": reasons})
        voices.append({**voice, "at": round(at, 3), "file": str(audio)})
        captions.append({"id": key, "at": round(at, 3), "text": spec["text"], "voice": spec["voice"]})
        at += duration
    if not review_candidate:
        require(not pending, "Native edit requires complete evidence and footage: " + "; ".join(pending))
        validate_native_demo(proof)
        roles = {"wang-platform", "zhao-platform", "wang-native", "zhao-native"} if scene == "messaging" else {"native", "platform"}
        require(any(s["layout"] == ("quad" if scene == "messaging" else "dual") and
                    {p["role"] for p in s["panels"]} == roles for s in shots),
                "Approved multi-panel platform and native footage is missing")
    return {"scene": scene, "shots": shots, "voices": voices, "captions": captions,
            "proof": copy.deepcopy(proof), "pending": pending, "duration": round(at, 3),
            "reviewCandidate": bool(review_candidate), "voiceTimingSha256": media_digest(voice_timing),
            "segmentsSha256": media_digest(folder / "segments.json")}


def _media_seconds(ff, source):
    result = subprocess.run([ff, "-hide_banner", "-i", str(source)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    require(match is not None, "Cannot establish actual source media duration")
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])


def render_native_shot(ff, folder, shot, destination, *, review_candidate, preparation_label=""):
    """Precompose undistorted panels; missing footage remains visibly missing."""
    folder, destination = Path(folder), Path(destination)
    layout, length = shot["layout"], shot["duration"]
    count = {"single": 1, "dual": 2, "quad": 4}[layout]
    width, height = (1920, 1080) if count == 1 else ((960, 1080) if count == 2 else (960, 540))
    inputs, filters, notes = [], [], []
    font = ffpath(Path("C:/Windows/Fonts/msyh.ttc"))
    # textfile avoids treating labels or private source paths as filter syntax.
    def label_filter(text, name, *, x=18, y=16, size=26, color="white"):
        path = destination.with_name(destination.stem + "-" + name + ".txt")
        path.write_text(text, encoding="utf-8")
        return (f"drawtext=fontfile='{font}':textfile='{ffpath(path)}':expansion=none:"
                f"x={x}:y={y}:fontsize={size}:fontcolor={color}:box=1:boxcolor=0x08111ee6:boxborderw=8")

    roles = ([None] if count == 1 else (["native", "platform"] if count == 2 else
             ["wang-platform", "zhao-platform", "wang-native", "zhao-native"]))
    for index in range(count):
        panel = (shot["panels"][0] if shot["panels"] else None) if count == 1 else next(
            (p for p in shot["panels"] if p["role"] == roles[index]), None)
        if panel and not panel.get("missing"):
            source = inside(folder, panel["source"])
            require(media_digest(source) == panel["sha256"], "Source SHA256 changed before encoding")
            require(panel["start"] + panel["duration"] <= _media_seconds(ff, source) + .06,
                    "Declared trim extends beyond actual source duration")
            inputs += ["-ss", str(panel["start"]), "-t", str(min(panel["duration"], length)), "-i", str(source)]
            chain = f"[{index}:v]setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25"
            if panel["duration"] < length:
                chain += f",tpad=stop_mode=clone:stop_duration={length-panel['duration']:.3f}"
                notes.append("定格讲解：" + panel["holdReason"])
            label = panel["label"]
            if panel.get("captureKind") == "later-review":
                label += " · 事后回看原记录"
        else:
            inputs += ["-f", "lavfi", "-i", f"color=c=0x132536:s={width}x{height}:r=25:d={length}"]
            chain = f"[{index}:v]setsar=1"
            label = (panel["label"] if panel else (roles[index] or "本镜头")) + " · 待补录 · 无该面板画面"
        filters.append(chain + "," + label_filter(label, "panel" + str(index)) + f"[p{index}]")
    if count == 1:
        filters.append("[p0]null[screen]")
    elif count == 2:
        filters.append("[p0][p1]hstack=inputs=2[screen]")
    else:
        filters.append("[p0][p1][p2][p3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[screen]")
    banners = ["仿真演示 · 真实原片与讲解叠层"]
    if review_candidate:
        banners.append("剪辑审阅候选 · 待补录/待核验 · 非完成影片")
    if preparation_label:
        banners.append(preparation_label)
    if shot["pendingReasons"]:
        banners.append("本镜头待补：" + "；".join(shot["pendingReasons"]))
    if shot.get("waitingCuts"):
        banners.append("同源等待片段已剪短")
    banners.extend(notes)
    filters.append("[screen]" + label_filter("\n".join(banners), "banner", y=76, size=26,
                                           color="0xffdc91" if review_candidate else "white") + "[v]")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *inputs,
                    "-filter_complex_threads", "2", "-filter_complex", ";".join(filters),
                    "-map", "[v]", "-t", str(length), "-an", "-c:v", "libx264",
                    "-threads", "2", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
                    str(destination)], check=True)


def edit_native(folder, scene, voice_timing, *, review_candidate=False):
    folder = Path(folder).resolve()
    plan = build_native_plan(folder, scene, voice_timing, review_candidate=review_candidate)
    require(not any((folder / name).exists() for name in ("demo.mp4", "edited.mp4", "clips", "qa.json")),
            "Use a new edit directory; existing movie or review must be preserved")
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    clips = folder / "clips"
    clips.mkdir()
    proof = plan["proof"]
    for index, shot in enumerate(plan["shots"]):
        render_native_shot(ff, folder, shot, clips / f"{index:02}-{shot['id']}.mp4",
                           review_candidate=review_candidate,
                           preparation_label=proof.get("preparation", {}).get("label", ""))
        print("Edited native", shot["id"], flush=True)
    concat = folder / "concat.txt"
    concat.write_text("\n".join("file '" + (clips / f"{i:02}-{s['id']}.mp4").as_posix() + "'"
                                for i, s in enumerate(plan["shots"])) + "\n", encoding="utf-8")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-c", "copy", str(folder / "edited.mp4")], check=True)
    voices = []
    for voice in plan["voices"]:
        source = Path(voice["file"])
        target = folder / "voice" / (voice["id"] + "-" + voice["audioSha256"][:12] + source.suffix)
        target.parent.mkdir(exist_ok=True)
        require(media_digest(source) == voice["audioSha256"], "Audio SHA256 changed before assembly")
        shutil.copyfile(source, target)
        voices.append({**voice, "file": str(target.relative_to(folder))})
    proof.update(layout="single", input="edited.mp4", framed=True, subtitleSize=52,
                 duration=plan["duration"], captureLead=0, editedFromActualRaw=True,
                 reviewCandidate=review_candidate, pending=plan["pending"], preview=False,
                 voiceTimingSha256=plan["voiceTimingSha256"], segmentsSha256=plan["segmentsSha256"])
    for name, value in (("voice.json", voices), ("captions.json", plan["captions"]), ("edit-list.json", plan["shots"])):
        write_json(folder / name, value)
    proof.update(voiceIndexSha256=media_digest(folder / "voice.json"),
                 editListSha256=media_digest(folder / "edit-list.json"))
    write_json(folder / "evidence.json", proof)
    subprocess.run([sys.executable, str(ROOT / "demo/assemble.py"), "--recording", str(folder)], check=True)
    if review_candidate:
        decoded = subprocess.run([ff, "-hide_banner", "-xerror", "-threads", "2", "-i", str(folder / "demo.mp4"),
                                  "-af", "volumedetect", "-f", "null", "-"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", check=True)
        require("1920x1080" in decoded.stderr and "Audio:" in decoded.stderr, "Candidate dimensions/audio failed")
        write_json(folder / "candidate-review.json", {"result": "review-candidate-not-publishable",
                   "videoSha256": media_digest(folder / "demo.mp4"), "fullDecode": True,
                   "dimensions": "1920x1080", "pending": plan["pending"],
                   "manualListening": "pending", "finalQa": "not_run"})
    else:
        subprocess.run([sys.executable, str(ROOT / "demo/check_recording.py"), "--recording", str(folder)], check=True)
        if __package__:
            from .artifacts import object_digest
        else:
            from artifacts import object_digest
        qa = read_json(folder / "qa.json")
        qa.update(proofDigest=object_digest(proof), approvalSha256=proof["approvalSha256"], manualListening="pending")
        write_json(folder / "qa.json", qa)
    return plan


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--recording", type=Path, required=True)
    p.add_argument("--voice-timing", type=Path, required=True)
    p.add_argument("--scene", choices=("delegation-provider", "delegation-assistant", "messaging"),
                   help="Edit the newly approved native screenplay with explicit panel mappings")
    p.add_argument("--review-candidate", action="store_true", help="Watermarked incomplete review; publishing is forbidden")
    p.add_argument(
        "--preview",
        action="store_true",
        help="Explicitly omit an unavailable Outlook shot; never publish as a complete formal film",
    )
    p.add_argument('--acceptance-presentation',choices=('recorded','narrated'),default='recorded',help='Narrated platform scope explains Outlook acceptance without claiming it was recorded')
    a = p.parse_args()
    if a.scene:
        require(not a.preview, "Use --review-candidate for the approved native scenes")
        edit_native(a.recording, a.scene, a.voice_timing, review_candidate=a.review_candidate)
        return
    require(not a.review_candidate, "Review candidates require --scene")
    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    out = a.recording.resolve()
    proof = read_json(out / "evidence.json")
    segments = read_json(out / "segments.json")
    prepared = {v["id"]: v for v in read_json(a.voice_timing)}
    raw = {s["id"]: s for s in segments}
    if a.acceptance_presentation=='narrated':
        require(proof['case']=='messaging' and not a.preview,'Narrated acceptance is an explicit messaging film scope')
        proof.update(acceptancePresentation='narrated',presentationScope='platform-message-flow',outlookFootageIncluded=False)
    require(acceptance_presentation(proof)==a.acceptance_presentation,'Select the declared acceptance presentation explicitly')
    validate_real_case(proof, preview=a.preview)
    if proof["case"] == "messaging" and not a.preview and a.acceptance_presentation=='recorded':
        require("outlook" in raw, "Actual Outlook acceptance footage is required")
    proof["preview"] = a.preview
    story = [s for s in STORIES[proof["case"]] if not ((a.preview or a.acceptance_presentation=="narrated") and s[0] == "outlook")]
    clips = out / "clips"
    clips.mkdir(exist_ok=True)
    voices = []
    captions = []
    edl = []
    at = 0
    for i, (key, text, spoken) in enumerate(story):
        entry = raw[key]
        voice = prepared[key]
        assert voice["text"] == text and voice["voice"] == spoken
        duration = round(voice["duration"] + 0.55, 3)
        if duration > entry["duration"]:
            raise ValueError("Raw shot too short for narration: " + key)
        source = inside(out, entry.get("source", "screen.webm"))
        dest = clips / f"{i:02}-{key}.mp4"
        start = entry["start"]
        filters = []
        if proof["case"] == "delegation" and key in ("quotes", "choice"):
            # Explanation overlay is derived from the two actual signed proposals, never a UI receipt.
            from PIL import Image, ImageDraw, ImageFont

            canvas = Image.new("RGBA", (1240 if len(proof["quotes"])==3 else 820, 320), (10, 24, 41, 247))
            draw = ImageDraw.Draw(canvas)
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 36)
            small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 23)
            big = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 58)
            draw.text((30, 20), "实测签名提案 · 讲解叠层", font=small, fill="#a2bdcc")
            rows = sorted(proof["quotes"], key=lambda r: float(r["total"]))
            assert [float(r["total"]) for r in rows] == ([239,259,299] if len(rows)==3 else [259,299])
            for n, row in enumerate(rows):
                x = 28 + n * 395
                chosen = key == "choice" and float(row["total"]) == 299
                color = "#80f2cc" if chosen else "#d8e9f1"
                draw.rounded_rectangle((x, 72, x + 368, 287), radius=14, outline=color, width=3)
                draw.text(
                    (x + 22, 86),
                    row["provider"] if proof.get("flow")=="nail-original" else ("可靠版" if float(row["total"]) == 299 else "基础版"),
                    font=font,
                    fill=color,
                )
                draw.text(
                    (x + 22, 137), str(int(float(row["total"]))) + " 虚拟币", font=big, fill=color
                )
                draw.text(
                    (x + 22, 225),
                    (
                        ("10项行为判据 · 已推荐" if chosen else "10项行为判据")
                        if float(row["total"]) == 299
                        else ("skill观察 · 单仲裁模型" if float(row["total"])==239 else "5项行为判据")
                    ),
                    font=small,
                    fill=color,
                )
            overlay = clips / (key + "-proposals.png")
            canvas.save(overlay)
            # movie source is fixed and escaped; filter output is still real recorded footage plus labelled explanation.
            filters.append("movie='" + ffpath(overlay) + "'[q];[0:v][q]overlay=30:380")
        if proof['case']=='messaging' and a.acceptance_presentation=='narrated' and key=='invitation':
            from message_acceptance_overlay import render
            overlay=clips/'acceptance-explained.png';render(overlay)
            filters.append("movie='"+ffpath(overlay)+"'[ex];[0:v][ex]overlay=160:330:enable='gte(t,1.5)'")
        previous = raw[story[i - 1][0]] if i else None
        if waiting_was_cut(previous, entry):
            filters.append(
                "drawtext=fontfile='"
                + ffpath(Path("C:/Windows/Fonts/msyh.ttc"))
                + "':text='等待片段已剪短':x=28:y=52:fontsize=18:fontcolor=0xd3dfed:box=1:boxcolor=0x08111ee6:boxborderw=8"
            )
        cmd = [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(duration),
            "-an",
            "-r",
            "25",
        ]
        if filters:
            cmd += ["-vf", ",".join(filters)]
        subprocess.run(
            [
                *cmd,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(dest),
            ],
            check=True,
        )
        audio = (a.voice_timing.parent / voice["file"]).resolve()
        target = out / "voice" / audio.name
        target.parent.mkdir(exist_ok=True)
        if audio!=target.resolve():shutil.copyfile(audio, target)
        voices.append({**voice, "at": round(at, 3), "file": str(target.relative_to(out))})
        captions.append({"id": key, "at": round(at, 3), "text": text, "voice": spoken})
        edl.append(
            {
                "id": key,
                "rawStart": start,
                "rawSource": str(source.relative_to(out)),
                "finalStart": round(at, 3),
                "duration": duration,
            }
        )
        at += duration
        print("Edited", key, flush=True)
    concat = out / "concat.txt"
    concat.write_text(
        "\n".join(
            "file '" + (clips / f"{i:02}-{s[0]}.mp4").as_posix() + "'" for i, s in enumerate(story)
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c",
            "copy",
            str(out / "edited.mp4"),
        ],
        check=True,
    )
    proof.update(
        layout="single",
        input="edited.mp4",
        framed=True,
        subtitleSize=52,
        duration=round(at, 3),
        captureLead=0,
        editedFromActualRaw=True,
    )
    for name, value in [
        ("evidence.json", proof),
        ("voice.json", voices),
        ("captions.json", captions),
        ("edit-list.json", edl),
    ]:
        write_json(out / name, value)
    subprocess.run(
        [sys.executable, str(ROOT / "demo/assemble.py"), "--recording", str(out)], check=True
    )
    subprocess.run(
        [sys.executable, str(ROOT / "demo/check_recording.py"), "--recording", str(out)], check=True
    )


if __name__ == "__main__":
    main()
