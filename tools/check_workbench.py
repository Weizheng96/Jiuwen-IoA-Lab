"""Offline layout/privacy regression of the real workbench DOM; never demo footage."""

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
    sample = {
        "account": {"id": "own", "username": "sample", "display_name": "王经理"},
        "roles": [],
        "wallet": "100",
        "identities": [],
        "orders": [],
        "services": [],
        "events": [],
        "categories": [],
        "messages": [],
        "interfaces": [
            {
                "name": "meeting.request",
                "revision": 1,
                "enabled": True,
                "allowedSenders": ["allowed"],
                "inputSchema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            }
        ],
    }
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, height in ((960, 928), (1920, 1080)):
                page = browser.new_page(viewport={"width": width, "height": height})
                errors = []
                paths = []
                page.on("pageerror", lambda e: errors.append(str(e)))

                def api(route):
                    path = route.request.url.split("/api/")[1]
                    paths.append(path)
                    value = (
                        {"id": "own"}
                        if path == "session"
                        else sample if path == "workbench" else []
                    )
                    assert path in ("session", "workbench", "catalog"), path
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(value),
                    )

                page.route("**/api/**", api)
                page.clock.install(time=0)
                page.clock.pause_at(1)
                page.goto(f"http://127.0.0.1:{server.server_port}/static/index.html")
                page.locator("#workspace").wait_for(state="visible")
                assert (
                    page.locator("#assistant-title").inner_text() == "王经理的个人助手"
                )
                assert (
                    "我定义的交互接口" in page.locator("#assistant-view").inner_text()
                )
                assert "免费接口" not in page.locator("#assistant-view").inner_text()
                assert page.locator("[data-map-component]").count() == 6
                assert page.locator(".ambient-assistant").count() == 16
                assert page.locator(".ambient-service").count() == 12
                bounds = page.locator(".ioa-network").bounding_box()
                assert (
                    bounds["y"] >= 0 and bounds["y"] + bounds["height"] < height - 120
                ), bounds
                page.locator("#interfaces").scroll_into_view_if_needed()
                assert page.locator(".ioa-network").bounding_box() == bounds
                page.locator(".interface-definition summary").click()
                page.evaluate("render()")
                assert (
                    page.locator(".interface-definition").get_attribute("open")
                    is not None
                )
                scope = page.locator(".interface-scope").bounding_box()
                schema = page.locator(".interface-definition pre").bounding_box()
                assert scope["y"] + scope["height"] <= schema["y"]
                assert page.locator("#network-map").evaluate(
                    "e=>e.scrollWidth<=e.clientWidth+1"
                )
                page.locator("[data-graph-node=message]").click()
                assert "消息中心" in page.locator("#component-detail").inner_text()
                page.evaluate("show('回执详情',{status:'no_time'})")
                assert (
                    page.locator("#detail").bounding_box()["y"]
                    >= bounds["y"] + bounds["height"]
                )
                page.keyboard.press("Escape")
                assert not page.locator("#detail").is_visible()
                page.clock.run_for(900)
                assert (
                    page.locator("#accountWires [data-activity=background]").count() > 0
                )
                assert page.locator("#accountWires [data-activity=own]").count() == 0
                page.evaluate(
                    "IoaNetwork.render({...data,events:[{seq:1,op:'discovery.search'}]})"
                )
                assert page.locator("#accountWires [data-activity=own]").count() == 1
                page.clock.run_for(1400)
                assert page.locator("#accountWires [data-activity=own]").count() == 0
                # A refresh of an unchanged event must not replay it.
                page.evaluate(
                    "IoaNetwork.render({...data,events:[{seq:1,op:'discovery.search'}]})"
                )
                assert page.locator("#accountWires [data-activity=own]").count() == 0
                assert not errors, errors
                if width == 960:
                    target = ROOT / "data/workbench-layout-review.png"
                    target.parent.mkdir(exist_ok=True)
                    page.screenshot(path=str(target))
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(
        "Workbench: fixed network, independent scroll/details, anonymous background, finite own flows and scoped requests passed"
    )


if __name__ == "__main__":
    main()
