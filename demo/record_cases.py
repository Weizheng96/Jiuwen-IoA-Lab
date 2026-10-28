"""Record real pages in side-by-side iframes; editing is driven by saved raw timestamps."""

import argparse, json, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
from ioalab_sdk.messaging import MailboxClient

DELEGATION = """请先加载已安装的ioalab-delegate技能，只使用IoA平台实际回执，不要联网找SaaS或估价。我是小满美甲店店主，需要本地预约工具：每天9点至20点营业，同时接待2人，纯色美甲75分钟、延长甲105分钟，支持取消和重复请求幂等。预算上限500虚拟币。请发现服务、读取模板，并分别用request向基础版和可靠版征集真实签名提案卡。各1虚拟币拟卡费、总共2虚拟币，我明确确认支付，请通过--confirmed传递授权。得到两张提案卡后比较报价与验收标准，暂不采纳、不签约、不开工。"""
CONTRACT = """选择299虚拟币的可靠版。请用技能compare记录实际候选和比较理由。我确认这份合同与验收标准，同意采纳、签约并开工，相应命令用--confirmed传递本次授权。收到交付后调用本地SDK取件，在WASM沙箱运行验收器，告诉我结果；暂不接受、暂不付尾款，等我确认。"""
ACCEPT = """我已看过本地SDK的验收通过结果，确认接受交付。请通过SDK签署验收并按合同金额支付虚拟币，用--confirmed传递本次授权，最后核对合同完成及实际已付金额。"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--case", choices=["delegation", "messaging"], required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if cfg.get("fixtures"):
        raise ValueError("Formal cases reject fixture participants")
    from urllib.parse import urlsplit

    if any(urlsplit(cfg[k]).hostname != "127.0.0.1" for k in ("base", "provider")):
        raise ValueError("This recording harness is limited to local simulation participants")
    b = cfg["accounts"]["b"]
    box = MailboxClient(cfg["base"], b["username"], b["password"], Path(cfg["folder"]) / "client-b")
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
            "美甲预约 · 合同委托" if a.case == "delegation" else "个人助手 · 留言与预约",
        )
        .replace("NINE_URL", json.dumps(cfg["swarmWeb"] + "/?user_id=ioa-demo-b"))
        .replace("LAB_URL", json.dumps(cfg["base"]))
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/schedule":
                from html import escape
                from ioalab_sdk.schedule import LocalSchedule

                state = LocalSchedule(Path(cfg["folder"]) / "assistant-a/schedule.json").show()
                doc = state["document"]
                rules = doc["rules"]
                events = "".join(
                    "<tr><td>"
                    + escape(e["title"])
                    + "</td><td>"
                    + escape(e["start"])
                    + "</td><td>"
                    + escape(e["end"])
                    + "</td></tr>"
                    for e in doc["events"]
                )
                raw = (
                    '<!doctype html><meta charset="utf-8"><style>body{background:#0b1424;color:#d9f6f0;padding:48px;font:25px Microsoft YaHei}h1{font-size:42px;color:#9de9d5}table{width:100%;border-collapse:collapse;margin:38px 0}td,th{text-align:left;border-bottom:1px solid #315068;padding:24px 12px}small{color:#98b2c6;font-size:18px;word-break:break-all}section{background:#15283c;border:1px solid #3c6478;border-radius:18px;padding:24px}</style><h1 id="schedule">助手A · 本地时间安排</h1><p>实际读取助手本地文件 · 可通过SDK或编辑器更新</p><section>时区：'
                    + escape(doc["timeZone"])
                    + "<p>可预约日："
                    + escape(
                        "、".join(
                            ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][i]
                            for i in rules["weekdays"]
                        )
                    )
                    + " · "
                    + str(rules["startHour"])
                    + ":00—"
                    + str(rules["endHour"])
                    + ":00</p><p>会议时长 "
                    + str(rules["minMinutes"])
                    + "—"
                    + str(rules["maxMinutes"])
                    + " 分钟 · 至少提前 "
                    + str(rules["leadMinutes"])
                    + " 分钟</p></section><table><tr><th>已有安排</th><th>开始</th><th>结束</th></tr>"
                    + events
                    + "</table><small>来源："
                    + escape(state["path"])
                    + "<br>修订："
                    + escape(state["revision"])
                    + "</small>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(raw)
                return
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from playwright.sync_api import sync_playwright

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
            nine.locator("[contenteditable=true]").last.wait_for(timeout=20000)
            if nine.get_by_text("qwen-flash", exact=True).count():
                nine.get_by_text("qwen-flash", exact=True).last.click()
                nine.get_by_text(cfg["swarmModelAlias"], exact=False).last.click()

            def shot(key, node=None, seconds=18):
                if node:
                    lab.locator("[data-graph-node=" + node + "]").click()
                    lab.locator(".ioa-network").evaluate('(e)=>e.scrollIntoView({block:"start"})')
                start = time.monotonic() - capture
                page.screenshot(path=str(out / (key + ".png")))
                page.wait_for_timeout(seconds * 1000)
                segments.append({"id": key, "start": round(start, 3), "duration": seconds})
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
                    if not isinstance(item.get("text"), str) or not 1 <= len(item["text"]) <= 4000:
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
                    {"id": ident, "kind": item["kind"], "at": round(time.monotonic() - capture, 3)}
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
                before = {o["card_digest"] for o in view()["orders"]}
                if before:
                    raise ValueError(
                        "Use a fresh case_stack folder for each formal delegation take; prior proposals may occupy provider capacity"
                    )
                send(
                    "本机技能已安装在"
                    + cfg["skillDirectory"].replace("\\", "/")
                    + "/SKILL.md，请先用read_file读取，不需要在线搜索或安装。本轮重新征集两份新提案，不复用历史合同。"
                    + DELEGATION
                )
                shot("need", "assistant")
                wait(lambda d: any(e["op"] == "discovery.search" for e in d["events"]), "discovery")
                shot("discovery", "a2x")
                proposed = wait(
                    lambda d: (
                        [o for o in d["orders"] if o["card_digest"] not in before]
                        if len([o for o in d["orders"] if o["card_digest"] not in before]) >= 2
                        else None
                    ),
                    "quotes",
                )
                assert {float(o["total"]) for o in proposed} == {259, 299}, proposed
                proof["quotes"] = [
                    {
                        "cardDigest": o["card_digest"],
                        "total": o["total"],
                        "provider": o["providerName"],
                    }
                    for o in proposed
                ]
                focus = next(o["card_digest"] for o in proposed if float(o["total"]) == 299)
                lab.locator('[data-order="' + focus + '"]').first.wait_for(timeout=30000)
                lab.locator('[data-order="' + focus + '"]').first.click()
                lab.locator("#detail").evaluate("(e)=>e.close()")
                proof["focusedCardDigest"] = focus
                shot("quotes", "service")
                # Wait for the first turn to finish before submitting the user's next instruction.
                page.wait_for_timeout(8000)
                send(CONTRACT)
                shot("choice", "contract")
                chosen = wait(
                    lambda d: next(
                        (
                            o
                            for o in d["orders"]
                            if o["card_digest"] not in before
                            and float(o["total"]) == 299
                            and o["signatures"].get("1")
                        ),
                        None,
                    ),
                    "signed",
                )
                cd = chosen["card_digest"]
                proof["cardDigest"] = cd
                shot("contract", "contract")
                wait(
                    lambda d: next(
                        (
                            o
                            for o in d["orders"]
                            if o["card_digest"] == cd and o["state"] == "started"
                        ),
                        None,
                    ),
                    "started",
                )
                shot("production", "service")
                wait(
                    lambda d: next(
                        (
                            o
                            for o in d["orders"]
                            if o["card_digest"] == cd and any(o["signatures"].get("5", []))
                        ),
                        None,
                    ),
                    "delivery",
                )
                shot("delivery", "contract")
                wait(
                    lambda d: any(
                        e["op"] == "order.verified" and e["card_digest"] == cd for e in d["events"]
                    ),
                    "verification",
                )
                shot("verification", "assistant")
                page.wait_for_timeout(5000)
                send(ACCEPT)
                completed = wait(
                    lambda d: next(
                        (
                            o
                            for o in d["orders"]
                            if o["card_digest"] == cd and o["state"] == "completed"
                        ),
                        None,
                    ),
                    "completion",
                )
                assert (
                    completed["signatures"]["7"] == [True] and float(completed["paid"]) == 299
                ), completed
                proof["delegation"] = {
                    "state": completed["state"],
                    "cardDigest": cd,
                    "signatures": completed["signatures"],
                    "paid": completed["paid"],
                }
                shot("payment", "payment")
                lab.locator("#logout").click()
                lab.locator("#name").fill(cfg["providerAccount"]["username"])
                lab.locator("#password").fill(cfg["providerAccount"]["password"])
                lab.locator("#auth button[type=submit]").click()
                lab.locator("#workspace").wait_for(state="visible")
                lab.locator("[data-view=provider]").click()
                page.locator("body").evaluate('(e)=>e.classList.add("full")')
                page.locator("#rightlabel").evaluate(
                    '(e)=>e.textContent="服务方 · 独立服务收入与历史"'
                )
                lab.locator("#provider-view").scroll_into_view_if_needed()
                shot("provider")
                assert cd[:23] in lab.locator("#services").inner_text()
                # The trusted local runner consumes the real fetched JSON, never executes provider code.
                files = list(
                    (Path(cfg["folder"]) / "client-b/deliveries" / cd.split(":")[-1] / "1").glob(
                        "booking.json"
                    )
                )
                assert len(files) == 1
                proof["deliveryFile"] = str(files[0])
                proof["swarmUrl"] = nine.locator("body").evaluate("(e)=>location.href")
                proof["readyForTool"] = True
                save()
                print("CONTRACT COMPLETE; real booking config:", files[0], flush=True)
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    "trusted_booking_runner", ROOT / "examples/booking_app.py"
                )
                runner = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(runner)
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    tool_port = sock.getsockname()[1]
                tool_base = f"http://127.0.0.1:{tool_port}"
                db = out / "booking-demo.sqlite"
                booking_config = json.loads(files[0].read_text(encoding="utf-8"))
                thread = threading.Thread(
                    target=runner.serve, args=(booking_config, db, tool_port), daemon=True
                )
                thread.start()
                page.locator("#right").evaluate("(e,url)=>e.src=url", tool_base)
                lab.locator("#form").wait_for()
                page.locator("#rightlabel").evaluate(
                    '(e)=>e.textContent="真实交付配置 · 本地预约工具"'
                )
                start = time.monotonic() - capture
                lab.locator("#name").fill("演示客户")
                lab.locator("#start").fill("2026-10-29T11:00")
                lab.locator("#form button").click()
                lab.get_by_text("预约成功", exact=True).wait_for()
                page.wait_for_timeout(4500)
                lab.get_by_role("button", name="取消预约").click()
                page.wait_for_timeout(13500)
                segments.append({"id": "tool", "start": round(start, 3), "duration": 18})
                proof["bookingDemo"] = (
                    box.http.get(tool_base + "/bookings").raise_for_status().json()
                )
                assert len(proof["bookingDemo"]) == 1 and proof["bookingDemo"][0]["cancelled"] == 1
                save()
                print("SHOT tool", flush=True)
                page.locator("#right").evaluate("(e,url)=>e.src=url", cfg["base"])
                lab.locator("#workspace").wait_for(state="visible")
                lab.locator("#logout").click()
                lab.locator("#name").fill(b["username"])
                lab.locator("#password").fill(b["password"])
                lab.locator("#auth button[type=submit]").click()
                lab.locator("#workspace").wait_for(state="visible")
                page.locator("body").evaluate('(e)=>e.classList.remove("full")')
                page.locator("#rightlabel").evaluate('(e)=>e.textContent="我的个人助手"')
                shot("close", "assistant")
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
