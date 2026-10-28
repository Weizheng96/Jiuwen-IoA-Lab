"""Reuse the A2C frame recorder; arrange this repository's readonly scene and real monitor."""

import argparse, hashlib, importlib.util, json, math, re, shutil, subprocess, sys, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from global_story import SHOTS, NAME, DESCRIPTION, REFERENCE


def label_video(ff, source, dest):
    from assemble import ffpath

    font = Path("C:/Windows/Fonts/msyh.ttc")
    filters = f"drawtext=fontfile='{ffpath(font)}':text='样例服务 · 虚拟币':x=w-tw-40:y=h-144:fontsize=17:fontcolor=0xa6becb:box=1:boxcolor=0x0a1424aa:boxborderw=7"
    subprocess.run(
        [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            filters,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        check=True,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--voice-timing", type=Path, required=True)
    p.add_argument("--render-helper", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--fps", type=int, default=25)
    p.add_argument("--preview", action="store_true")
    a = p.parse_args()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    web = out / "web"
    web.mkdir(exist_ok=True)
    for src in (ROOT / "demo/global_web").iterdir():
        if src.is_file():
            shutil.copyfile(src, web / src.name)
    for name in ("panorama.css", "network-map.css", "network-map.js"):
        shutil.copyfile(ROOT / "ioalab/web/static" / name, web / name)
    run = json.loads((a.source / "public-run.json").read_text(encoding="utf-8"))
    assert not run["proof"]["errors"] and run["proof"]["a2xTreeSearches"] > 0
    shutil.copyfile(a.source / "public-run.json", web / "public-run.json")
    spec = importlib.util.spec_from_file_location(
        "a2c_video_record", a.render_helper.resolve()
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    frames = web / "monitor"
    frames.mkdir(exist_ok=True)
    if not list(frames.glob("*.jpg")):
        monitor = a.source / (
            "monitor.mp4" if (a.source / "monitor.mp4").exists() else "monitor.webm"
        )
        subprocess.run(
            [
                helper.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(run.get("monitorTrim", 6)),
                "-i",
                str(monitor),
                "-vf",
                "fps=12,scale=1920:1080",
                "-q:v",
                "3",
                "-start_number",
                "0",
                str(frames / "%04d.jpg"),
            ],
            check=True,
        )
    source = json.loads(a.voice_timing.read_text(encoding="utf-8"))
    byid = {s["id"]: s for s in source}
    timeline = []
    voices = []
    cursor = 0
    for s in SHOTS:
        clip = byid[s["id"]]
        assert clip["voice"] == s["voice"] and clip["text"] == s["text"]
        hold = max(clip["duration"] + 0.4, 5 if s["id"] == "intro" else 0)
        timeline.append({**s, "at": round(cursor, 3), "hold": hold})
        dest = out / "voice" / Path(clip["file"]).name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(a.voice_timing.parent / clip["file"], dest)
        voices.append(
            {**clip, "at": round(cursor, 3), "file": str(dest.relative_to(out))}
        )
        cursor += hold
    monitor_start = next(s["at"] for s in timeline if s["stage"] >= 9)
    timeline.append(
        {
            "id": "monitor-tail",
            "stage": 10,
            "mode": "tail",
            "text": "",
            "voice": "",
            "at": round(cursor, 3),
            "hold": 20,
            "focus": [],
        }
    )
    duration = round(cursor + 20, 3)
    data = {
        "shots": timeline,
        "duration": duration,
        "monitorFrames": len(list(frames.glob("*.jpg"))),
        "monitorFps": 12,
        "monitorRate": 1.6,
    }
    (web / "timeline.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "voice.json").write_text(
        json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "captions.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def copyfile(self, source, outputfile):
            try:
                super().copyfile(source, outputfile)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(web)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/index.html?auto=1"
    try:
        from playwright.sync_api import sync_playwright

        checks = []
        errors = []
        allowed = {
            "join": {"join"},
            "credit": {"credit"},
            "provider": {"provider"},
            "needs": {"needs"},
            "authorization": {"authorization"},
            "discovery": {"discovery"},
            "methods": set(),
            "compare": set(),
            "contract": set(),
            "delivery": {"delivery"},
            "acceptance": {"acceptance"},
            "arbitration": set(),
            "payment": {"payment"},
            "reputation": {"reputation"},
            "messages": {"messages"},
            "meeting": {"messages"},
        }
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.clock.install(time=0)
            page.clock.pause_at(1)
            page.goto(url)
            page.wait_for_function("window.ready")
            page.clock.run_for(50)
            last = 0
            assert page.locator(".pn-asst").count() == len(run["actors"]) >= 30
            assert page.locator(".pn-svc").count() == len(run["services"]) >= 50
            assert page.locator("#pnEvents").count() == 0
            assert page.locator("#pnBounty").inner_text().startswith("悬赏中心")
            widths = page.locator("#pn .pn-mid > .pn-infra").evaluate_all(
                "(nodes)=>nodes.map(n=>n.getBoundingClientRect().width)"
            )
            assert max(widths) - min(widths) < 1, widths
            print(
                "Shared network layout:",
                len(run["actors"]),
                "assistants,",
                len(run["services"]),
                "services; equal component widths",
                flush=True,
            )
            for shot in timeline:
                point = shot["at"] + min(2, shot["hold"] / 2)
                page.clock.run_for(max(1, round((point - last) * 1000)))
                last = point
                info = page.evaluate("window.frameInfo")
                assert info["mode"] == shot["mode"], info
                if shot["stage"] <= 8:
                    assert set(info["families"]) <= allowed.get(
                        shot["mode"], set()
                    ), info
                assert (
                    page.locator("#caption").evaluate(
                        "(e)=>parseInt(getComputedStyle(e).fontSize)"
                    )
                    >= 52
                )
                plan = page.evaluate("window.eventPlan")
                assert len({e["id"] for e in plan}) == len(plan)
                assert all(0.23 <= e["duration"] <= 0.5 for e in plan)
                if len(plan) > 10:
                    assert len({round(e["at"], 2) for e in plan}) > len(plan) // 3
                    assert any(
                        plan[i + 1]["at"] < e["at"] + e["duration"]
                        for i, e in enumerate(plan[:-1])
                    )
                if shot["stage"] >= 9:
                    assert (
                        page.locator("#network").is_hidden()
                        and page.locator("#realMonitor").is_visible()
                    )
                page.screenshot(path=str(out / ("shot-" + shot["id"] + ".png")))
                checks.append({**info, "finiteUniqueEventPlan": True})
            page.evaluate(
                """()=>{const layer=document.createElementNS('http://www.w3.org/2000/svg','svg');layer.style.cssText='position:absolute;width:1000px;height:800px';document.body.append(layer);const f=NetworkMap.flow(layer,document.querySelector('#pnIdentity'),document.querySelector('#pnPay'),'test');f.paint(.5);if(!layer.children.length)throw Error('missing active flow');f.paint(1);if(layer.children.length)throw Error('flow did not expire');layer.remove();}"""
            )
            browser.close()
        assert not errors, errors
        (out / "scene-checks.json").write_text(
            json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if a.preview:
            print("Preview frames ready; full rendering not requested")
            return
        count = helper.render_frames(
            url, round(duration * 1000), a.fps, out / "silent.mp4"
        )
        helper.mux_audio(
            out / "silent.mp4",
            [(round(v["at"] * 1000), out / v["file"]) for v in voices],
            out / "mixed.mp4",
            count * round(1000 / a.fps),
        )
        label_video(helper.ffmpeg(), out / "mixed.mp4", out / "demo.mp4")
        r = subprocess.run(
            [
                helper.ffmpeg(),
                "-hide_banner",
                "-i",
                str(out / "demo.mp4"),
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
        mean = re.search(r"mean_volume: ([-\d.]+)", r.stderr)
        assert mean and float(mean[1]) > -45
        assert "1920x1080" in r.stderr and "Audio:" in r.stderr
        for i, v in enumerate(voices):
            assert v["at"] + v["duration"] <= (
                voices[i + 1]["at"] if i + 1 < len(voices) else duration
            )
        summary = {
            "case": "global",
            "displayName": NAME,
            "description": DESCRIPTION,
            "source": "chapter-isolated conceptual map with actual run data and real admin capture",
            "narrationReference": REFERENCE,
            "experiment": run["proof"],
            "qa": {
                "result": "passed",
                "dimensions": "1920x1080",
                "duration": duration,
                "meanVolumeDb": float(mean[1]),
                "narrationClips": len(voices),
                "phaseIsolation": True,
                "reviewFrames": ["shot-" + x["id"] + ".png" for x in SHOTS],
            },
            "subtitles": {"pixels": 52, "highContrastBand": True},
            "narration": {
                "speechRate": voices[0]["speechRate"],
                "tempo": voices[0]["tempo"],
                "a2cPronunciation": "A to C",
                "measuredAudioTimeline": True,
            },
            "mechanismOnly": [
                "payment authorization illustration",
                "ANN alternative",
                "conditional arbitration",
                "online/offline illustration",
            ],
            "monitor": {
                "actualCapture": True,
                "playbackRate": 1.6,
                "fullScreenOnly": True,
                "tailSeconds": 20,
                "sharedLayout": True,
            },
            "video": "demo/out/01-global.mp4",
            "sha256": hashlib.sha256((out / "demo.mp4").read_bytes()).hexdigest(),
        }
        summary["persistentLabels"] = [
            "概念动画（讲解段）",
            "真实管理员监控（片尾）",
            "样例服务",
            "虚拟币",
        ]
        summary["qa"].update(
            {
                "finiteRandomEvents": True,
                "fastFlowSeconds": [0.23, 0.5],
                "equalComponentWidths": True,
                "zeroIdleWireResidue": run["proof"]["ui"]["residualWiresAfterIdle"]
                == 0,
                "singleFullScreenMonitor": True,
            }
        )
        (out / "verification.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary["qa"], ensure_ascii=False))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
