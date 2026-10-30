"""Supplement the local runner and closing shots from the same completed contract."""

import argparse, importlib.util, json, shutil, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/access/sdk"))
from ioalab_sdk.messaging import MailboxClient


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--recording", type=Path, required=True)
    args = p.parse_args()
    out = args.recording.resolve()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    proof = json.loads((out / "evidence.json").read_text(encoding="utf-8"))
    segments = json.loads((out / "segments.json").read_text(encoding="utf-8"))
    assert (
        proof["delegation"]["state"] == "completed"
        and proof["delegation"]["signatures"]["7"] == [True]
        and float(proof["delegation"]["paid"]) == 299
    )
    assert not any(s["id"] in ("tool", "close") for s in segments)
    b = cfg["accounts"]["b"]
    box = MailboxClient(cfg["base"], b["username"], b["password"], Path(cfg["folder"]) / "client-b")
    current = (
        box.http.get(
            cfg["base"] + "/api/workbench", headers={"Authorization": "Bearer " + box.token}
        )
        .raise_for_status()
        .json()
    )
    actual = next(o for o in current["orders"] if o["card_digest"] == proof["cardDigest"])
    assert actual["state"] == "completed" and float(actual["paid"]) == 299
    original = list((out / "raw").glob("*.webm"))
    assert len(original) == 1
    if not (out / "screen.webm").exists():
        shutil.copyfile(original[0], out / "screen.webm")
    spec = importlib.util.spec_from_file_location(
        "trusted_runner", ROOT / "examples/booking_app.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    config = json.loads(Path(proof["deliveryFile"]).read_text(encoding="utf-8"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    tool = f"http://127.0.0.1:{port}"
    threading.Thread(
        target=runner.serve, args=(config, out / "booking-final.sqlite", port), daemon=True
    ).start()
    html = """<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#080f1b;color:#e7f5ff;font:18px Microsoft YaHei}header{height:42px;display:flex;justify-content:space-between;padding:10px 28px;background:#101f31}main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);height:928px;overflow:hidden;gap:3px}iframe{width:100%;height:100%;border:0}#left{width:calc(100% + 312px);position:relative;left:-312px}body.full main{grid-template-columns:0 1fr}body.full #left{visibility:hidden}footer{height:110px;border-top:2px solid #79daca}</style><header><b>九问 Swarm</b><span>九问IoA实验平台 · 实际界面录屏</span><b id="label">真实交付配置 · 本地预约工具</b></header><main><iframe id="left"></iframe><iframe id="right"></iframe></main><footer></footer>"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=out / "tail-raw",
                record_video_size={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            capture = time.monotonic()
            page.on("pageerror", lambda e: proof["browserErrors"].append(str(e)))
            page.goto(f"http://127.0.0.1:{srv.server_port}")
            page.locator("body").evaluate('(e)=>e.classList.add("full")')
            page.locator("#left").evaluate("(e,u)=>e.src=u", proof["swarmUrl"])
            page.locator("#right").evaluate("(e,u)=>e.src=u", tool)
            lab = page.frame_locator("#right")
            lab.locator("#form").wait_for()
            page.wait_for_timeout(1500)
            start = time.monotonic() - capture
            lab.locator("#name").fill("演示客户")
            lab.locator("#start").fill("2026-10-29T11:00")
            lab.locator("#form button").click()
            lab.get_by_text("预约成功", exact=True).wait_for()
            page.wait_for_timeout(3800)
            lab.get_by_role("button", name="取消预约").click()
            lab.get_by_text("已取消", exact=False).wait_for()
            page.wait_for_timeout(13000)
            segments.append(
                {"id": "tool", "start": round(start, 3), "duration": 18, "source": "tail.webm"}
            )
            proof["bookingDemo"] = box.http.get(tool + "/bookings").raise_for_status().json()
            assert len(proof["bookingDemo"]) == 1 and proof["bookingDemo"][0]["cancelled"] == 1
            page.screenshot(path=str(out / "tool.png"))
            page.locator("#right").evaluate("(e,u)=>e.src=u", cfg["base"])
            lab.locator("#name").fill(b["username"])
            lab.locator("#password").fill(b["password"])
            lab.locator("#auth button[type=submit]").click()
            lab.locator("#workspace").wait_for(state="visible")
            page.locator("body").evaluate('(e)=>e.classList.remove("full")')
            page.locator("#label").evaluate('(e)=>e.textContent="我的个人助手"')
            lab.locator("[data-graph-node=assistant]").click()
            lab.locator(".ioa-network").evaluate('(e)=>e.scrollIntoView({block:"start"})')
            page.wait_for_timeout(2000)
            start = time.monotonic() - capture
            page.screenshot(path=str(out / "close.png"))
            page.wait_for_timeout(18000)
            segments.append(
                {"id": "close", "start": round(start, 3), "duration": 18, "source": "tail.webm"}
            )
            video = page.video
            context.close()
            video.save_as(out / "tail.webm")
            browser.close()
        proof["supplement"] = {
            "reason": "Repair browser cancellation request shape in trusted local runner",
            "sameContract": proof["cardDigest"],
            "scope": "runner and closing views only",
            "previousFailedToolFootageExcluded": True,
        }
        proof["rawDuration"] = round(time.monotonic() - capture, 3)
        (out / "evidence.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "segments.json").write_text(
            json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Same-contract tool and closing capture complete", flush=True)
    finally:
        box.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
