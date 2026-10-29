"""Record real pages in side-by-side iframes; editing is driven by saved raw timestamps."""

import argparse, json, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
from ioalab_sdk.messaging import MailboxClient




def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--case", choices=["delegation", "messaging"], required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    if cfg.get("scenario", a.case) != a.case:
        raise ValueError("Use the independent participant stack for this scenario")
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if cfg.get("fixtures"):
        raise ValueError("Formal cases reject fixture participants")
    from urllib.parse import urlsplit

    if any(urlsplit(cfg[k]).hostname != "127.0.0.1" for k in ("base", "provider")):
        raise ValueError(
            "This recording harness is limited to local simulation participants"
        )
    b = cfg["accounts"]["b"]
    box = MailboxClient(
        cfg["base"], b["username"], b["password"], Path(cfg["folder"]) / "client-b"
    )
    health = box.http.get(cfg["provider"] + "/health").raise_for_status().json()
    assert health["execution"] == "swarm"
    proof = {
        "case": a.case,
        "fixtures": [],
        "screens": "real browser pages",
        "providerExecution": "swarm",
        "browserErrors": [],
    }
    segments = []
    html = """<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#080f1b;color:#e7f5ff;font:18px "Microsoft YaHei"}header{height:42px;display:flex;justify-content:space-between;padding:10px 28px;background:#101f31}#panels{display:grid;grid-template-columns:1fr 1fr;height:928px;gap:3px}iframe{width:100%;height:100%;border:0;background:#0b1421}#cover{position:fixed;inset:42px 0 110px;z-index:20;background:radial-gradient(ellipse at center,#17374b,#0b1424 70%);display:grid;place-content:center;text-align:center}h1{font-size:68px;margin:0 0 26px;color:#c9fff4}h2{font-size:34px;color:#9fced9;font-weight:400}#bottom{height:110px;border-top:2px solid #79daca}body.full #panels{grid-template-columns:0 1fr}body.full #left{visibility:hidden}#tag{position:fixed;bottom:118px;right:20px;font-size:14px;color:#94aabd;background:#09121cdd;padding:6px 10px}</style><header><b id="leftlabel">九问 Swarm</b><span>九问IoA实验平台 · 实际界面录屏</span><b id="rightlabel">我的个人助手</b></header><div id="panels"><iframe id="left"></iframe><iframe id="right"></iframe></div><div id="bottom"></div><div id="tag">真实九问与协议 · 虚拟币 · 长等待将在剪辑中缩短</div><div id="cover"><h1>CASE_TITLE</h1><h2>九问IoA实验平台 · Jiuwen IoA Lab</h2></div><script>left.src=NINE_URL;right.src=LAB_URL;</script>"""
    html = (
        html.replace(
            "CASE_TITLE",
            (
                "美甲预约 · 合同委托"
                if a.case == "delegation"
                else "个人助手 · 留言与预约"
            ),
        )
        .replace("NINE_URL", json.dumps(cfg["swarmWeb"] + "/?user_id=ioa-demo-b"))
        .replace("LAB_URL", json.dumps(cfg["base"]))
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from playwright.sync_api import (
        sync_playwright,
        TimeoutError as PlaywrightTimeoutError,
    )

    def save():
        (out / "segments.json").write_text(
            json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "evidence.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=out / "raw",
                record_video_size={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            capture = time.monotonic()
            page.on("pageerror", lambda e: proof["browserErrors"].append(str(e)))
            page.goto(f"http://127.0.0.1:{srv.server_port}")
            page.locator("#cover").evaluate('(e)=>e.style.pointerEvents="none"')
            page.add_style_tag(
                content="#panels{grid-template-columns:minmax(0,1fr) minmax(0,1fr);overflow:hidden}#left{width:calc(100% + 312px);min-width:0;position:relative;left:-312px}"
            )
            nine = page.frame_locator("#left")
            lab = page.frame_locator("#right")
            lab.locator("#name").fill(b["username"])
            lab.locator("#password").fill(b["password"])
            lab.locator("#auth button[type=submit]").click()
            lab.locator("#workspace").wait_for(state="visible")
            page.locator('#left').evaluate('(e)=>{e.style.width="100%";e.style.left="0"}')
            nine.get_by_role('button',name='新建任务',exact=True).click()
            page.locator('#left').evaluate('(e)=>{e.style.width="calc(100% + 312px)";e.style.left="-312px"}')
            nine.locator("[contenteditable=true]").last.wait_for(timeout=20000)
            try:
                nine.get_by_text("qwen-flash", exact=True).last.wait_for(timeout=8000)
            except PlaywrightTimeoutError:
                pass  # The previously selected model may already be the configured alias.
            desired=cfg['swarmModelAlias']
            for current in ('qwen-flash','IoA演示模型'):
                selector=nine.get_by_text(current,exact=True)
                if current!=desired and selector.count() and selector.last.is_visible():
                    selector.last.click()
                    nine.get_by_text(desired,exact=True).last.click()
                    break

            def shot(key, node=None, seconds=18, action=None):
                if node:
                    lab.locator("[data-graph-node=" + node + "]").click()
                    lab.locator(".ioa-network").evaluate(
                        '(e)=>e.scrollIntoView({block:"start"})'
                    )
                start = time.monotonic() - capture
                if action: action()
                page.screenshot(path=str(out / (key + ".png")))
                page.wait_for_timeout(seconds * 1000)
                segments.append(
                    {"id": key, "start": round(start, 3), "duration": seconds}
                )
                save()
                print("SHOT", key, flush=True)

            def send(text):
                editor = nine.locator("[contenteditable=true]").last
                editor.fill(text)
                editor.press("Enter")
                print("Submitted visible Jiuwen turn", flush=True)

            seen = set()

            def operator():
                command = out / "operator.json"
                if not command.exists():
                    return
                import re

                item = json.loads(command.read_text(encoding="utf-8"))
                ident = item["id"]
                if not re.fullmatch("[A-Za-z0-9_-]{1,80}", ident):
                    raise ValueError("Invalid operator marker")
                if ident in seen:
                    return
                if item["kind"] == "message":
                    if (
                        not isinstance(item.get("text"), str)
                        or not 1 <= len(item["text"]) <= 4000
                    ):
                        raise ValueError("Invalid operator message")
                    send(item["text"])
                elif item["kind"] != "snapshot":
                    raise ValueError("Unknown operator action")
                seen.add(ident)
                page.screenshot(path=str(out / ("operator-" + ident + ".png")))
                (out / ("operator-" + ident + ".txt")).write_text(
                    nine.locator("body").inner_text(), encoding="utf-8"
                )
                proof.setdefault("operatorActions", []).append(
                    {
                        "id": ident,
                        "kind": item["kind"],
                        "at": round(time.monotonic() - capture, 3),
                    }
                )
                save()

            def view():
                return (
                    box.http.get(
                        cfg["base"] + "/api/workbench",
                        headers={"Authorization": "Bearer " + box.token},
                    )
                    .raise_for_status()
                    .json()
                )

            def wait(predicate, label, seconds=900):
                until = time.monotonic() + seconds
                while time.monotonic() < until:
                    operator()
                    value = view()
                    result = predicate(value)
                    if result:
                        return result
                    page.wait_for_timeout(1000)
                page.screenshot(path=str(out / ("timeout-" + label + ".png")))
                (out / ("nine-" + label + ".txt")).write_text(
                    nine.locator("body").inner_text(), encoding="utf-8"
                )
                raise TimeoutError(label)

            shot("cover")
            page.locator("#cover").evaluate("(e)=>e.remove()")
            if a.case == "delegation":
                from delegation_case import record
                record(cfg,out,page,nine,lab,box,proof,shot,send,wait,save)
            else:
                from message_case import record

                record(cfg, out, page, nine, lab, box, proof, shot, send, wait, save)
            page.screenshot(path=str(out / "final.png"))
            video = page.video
            context.close()
            video.save_as(out / "screen.webm")
            browser.close()
            proof["rawDuration"] = round(time.monotonic() - capture, 3)
            save()
    finally:
        box.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
