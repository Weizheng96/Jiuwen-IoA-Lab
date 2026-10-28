"""Offline browser regression: real admin DOM/renderer, no model, accounts or server API."""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    from playwright.sync_api import sync_playwright

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "ioalab/web"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for script in ("admin.js", "admin_messages.js"):
                page.route(
                    "**/static/" + script,
                    lambda route: route.fulfill(status=200, body=""),
                )
            page.clock.install(time=0)
            page.clock.pause_at(1)
            page.goto(f"http://127.0.0.1:{server.server_port}/static/admin.html")
            page.add_script_tag(
                content="""
                const USERS=[{id:'u-a',username:'A',kind:'external',balance:100,agents:[{id:'assistant-a',role:'assistant'}]},{id:'u-b',username:'B',kind:'external',balance:100,agents:[{id:'assistant-b',role:'assistant'}]}];
                const REGISTRY=[{id:'service',name:'Service',category:'test',owner_id:'u-b',price:10,status:'ready'}];
                const cases=new Cases();Names.seedUsers(USERS);Names.seedServices(REGISTRY);
                document.getElementById('mainView').classList.remove('hide');document.body.classList.add('trial');
                Panorama.init(USERS,REGISTRY,[{id:'test',name:'Test'}],{});
            """
            )
            widths = page.locator("#pn .pn-mid > .pn-infra").evaluate_all(
                "(nodes)=>nodes.map(n=>n.getBoundingClientRect().width)"
            )
            assert len(widths) == 6 and max(widths) - min(widths) < 1, widths
            assert page.locator("#pnEvents").count() == 0
            assert (
                page.locator(".concept-user").count() == 0
            ), "User roles belong only to the explanatory scene"
            assert "用户权限白名单管理" in page.locator("#pnIntent").inner_text()
            assert page.locator("#pnIntent").evaluate(
                "e=>e.classList.contains('construction')"
            )
            page.locator("#pnIdentity").click()
            assert page.locator("#pnIdentity").evaluate(
                '(el)=>el.classList.contains("component-focus")'
            )
            page.keyboard.press("Escape")
            assert page.locator("#mapInfo").is_hidden()
            page.evaluate(
                "Panorama.restore([{assistant:'assistant-a',provider:'service',cd:'sha256:closed',state:'已结清',sigs:{1:true,2:true,3:true,4:[true],5:[true],6:[true],7:[true]}}])"
            )
            assert (
                page.locator("#pnWires > *").count() == 0
            ), "Closed history must never create a persistent connection"
            page.evaluate(
                "cases.byCard['sha256:second']={assistant:'assistant-b',provider:'service'};Panorama.apply({seq:1,op:'delivery.anchor',sender:'service',receiver:'market',cardDigest:'sha256:second',detail:{}})"
            )
            assert (
                page.evaluate("Panorama.N.asst['assistant-b'].state") == "收到存证通知"
            )
            assert (
                page.evaluate("Panorama.N.asst['assistant-a'].state") == "已结清 ✓"
            ), "Concurrent orders must be routed by card, not the first matching service"
            page.clock.run_for(2000)
            page.evaluate("Panorama.tick(Clock.now())")
            page.evaluate(
                "Panorama.apply({seq:1,kind:'message',op:'message.queued',sender:'assistant-a',receiver:'assistant-b',detail:{messageId:'one',interface:'safety.checkin'}})"
            )
            assert (
                page.locator("#pnWires > *").count() == 1
            ), "Queueing creates only the sender-to-center flow"
            page.clock.run_for(1200)
            page.evaluate("Panorama.tick(Clock.now())")
            assert (
                page.locator("#pnWires > *").count() == 0
            ), "Sending must not schedule a fictitious recipient flow"
            page.evaluate(
                "Panorama.apply({seq:2,kind:'message',op:'message.processing',sender:'assistant-a',receiver:'assistant-b',detail:{messageId:'one',interface:'safety.checkin'}})"
            )
            assert (
                page.locator("#pnWires > *").count() == 1
            ), "Actual recipient processing creates the receive flow"
            page.clock.run_for(1200)
            page.evaluate("Panorama.tick(Clock.now())")
            assert (
                page.locator("#pnWires > *").count() == 0
            ), "Completed transmission must leave no SVG remnants"
            page.evaluate(
                "Panorama.init(USERS,REGISTRY,[{id:'test',name:'Test'}],{baseSeq:100});Panorama.apply({seq:50,op:'discovery.search',sender:'assistant-a',detail:{ids:['service']}})"
            )
            page.clock.run_for(1200)
            page.evaluate("Panorama.tick(Clock.now())")
            assert (
                page.locator("#pnWires > *").count() == 0
            ), "Historical callbacks must not animate after restoration"
            assert not errors, errors
            browser.close()
        print(
            json.dumps(
                {
                    "result": "passed",
                    "checks": [
                        "equal widths",
                        "protocol component focus",
                        "observer hidden",
                        "closed history has no wires",
                        "queue does not fabricate delivery",
                        "processing draws receive",
                        "zero residual paths",
                    ],
                },
                ensure_ascii=False,
            )
        )
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
