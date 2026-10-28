"""Fresh real A2X run + admin recording. Only service outputs are labelled samples."""

import argparse, json, os, secrets, socket, sqlite3, subprocess, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "deploy"), str(ROOT / "experiments")]
from configure_assistant import read_env
from network import NetworkTraffic
from mixed_activity import summarize


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model-env", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=150)
    p.add_argument("--participants", type=int, default=36)
    a = p.parse_args()
    folder = a.out.resolve()
    folder.mkdir(parents=True, exist_ok=False)

    def port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    cp, pp = port(), port()
    base = f"http://127.0.0.1:{cp}"
    provider = f"http://127.0.0.1:{pp}"
    model = read_env(a.model_env)
    key = model.get("IOALAB_LLM_API_KEY") or model.get("API_KEY")
    endpoint = model.get("IOALAB_LLM_BASE_URL") or model.get("API_BASE")
    if not key or not endpoint:
        raise ValueError(
            "Explicit model environment is required for actual A2X tree search"
        )
    account = {"username": "ISC_001", "password": secrets.token_urlsafe(24)}
    config = {
        "base": base,
        "provider": provider,
        "folder": str(folder),
        "accounts": {"a": account},
        "fixtures": ["provider-sample"],
    }
    (folder / "run.json").write_text(json.dumps(config), encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "IOALAB_MODE": "dev",
        "IOALAB_STUB": "0",
        "IOALAB_LLM_API_KEY": key,
        "IOALAB_LLM_BASE_URL": endpoint,
        "IOALAB_A2X_MODEL": "qwen-plus",
        "IOALAB_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "IOALAB_ASSISTANT_PASSWORD": account["password"],
        "IOALAB_PORT": str(cp),
        "IOALAB_PUBLIC_URL": base,
        "IOALAB_BASE_URL": base,
        "IOALAB_DATA": str(folder / "core"),
        "IOALAB_PROVIDER_PORT": str(pp),
        "IOALAB_PROVIDER_PUBLIC_URL": provider,
        "IOALAB_PROVIDER_DATA": str(folder / "providers"),
    }
    processes = []
    logs = []
    traffic = None

    def launch(name, cmd, values):
        log = (folder / (name + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=values,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        processes.append(proc)
        return proc

    def ready(url, proc):
        until = time.monotonic() + 180
        while time.monotonic() < until:
            if proc.poll() is not None:
                raise RuntimeError("Owned process exited; inspect local log")
            try:
                r = httpx.get(url, timeout=3, trust_env=False)
                if r.is_success and r.json().get("ready", True):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise TimeoutError(url)

    try:
        core = launch(
            "core",
            [str(ROOT / ".venv/Scripts/python.exe"), "-m", "ioalab", "serve"],
            env,
        )
        ready(base + "/api/config", core)
        services = [
            "translate-zh-en",
            "wechat-posts",
            "resume-polish",
            "regex-snippet",
            "trip-plan",
            "brand-story",
            "med-reminder",
            "contract-risks",
            "mistake-explain",
            "product-page",
            "translate-en-zh",
            "packing-list",
        ]
        service = launch(
            "providers",
            [
                str(ROOT / ".venv/Scripts/python.exe"),
                str(ROOT / "official-services/serve.py"),
            ],
            {**env, "IOALAB_STUB": "1"},
        )
        ready(provider + "/health", service)
        traffic = NetworkTraffic(
            config, folder / "participants", count=a.participants, services=services
        )
        # Prepare explicit virtual experiment credit through the normal admin API.
        with httpx.Client(base_url=base, trust_env=False) as http:
            http.post("/api/auth/login", json=account).raise_for_status()
            allowed = {a["username"] for a in traffic.accounts}
            for user in http.get("/api/admin/users").raise_for_status().json():
                if user["username"] in allowed:
                    http.post(
                        "/api/admin/wallet",
                        json={
                            "user_id": user["id"],
                            "delta": "1000",
                            "note": "独立实验虚拟体验额",
                        },
                    ).raise_for_status()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=folder / "raw",
                record_video_size={"width": 1920, "height": 1080},
            )
            video_origin = time.monotonic()
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base)
            page.locator("#name").fill(account["username"])
            page.locator("#password").fill(account["password"])
            page.locator("#auth button[type=submit]").click()
            page.locator("#workspace").wait_for(state="visible")
            page.goto(base + "/admin?view=panorama")
            page.locator("#mainView").wait_for(state="visible")
            lead = time.monotonic()
            traffic.start()
            lead = time.monotonic() - lead
            page.locator("#pnIdentity").click()
            assert page.locator("#pnIdentity").evaluate(
                '(e)=>e.classList.contains("component-focus")'
            )
            page.keyboard.press("Escape")
            assert page.locator("#mapInfo").is_hidden()
            widths = page.locator("#pn .pn-mid > .pn-infra").evaluate_all(
                "(nodes)=>nodes.map(n=>n.getBoundingClientRect().width)"
            )
            assert max(widths) - min(widths) < 1, widths
            assert page.locator("#pnEvents").count() == 0
            print(
                "Recording current admin UI with independent clients and actual A2X model search",
                flush=True,
            )
            # Warm up genuine discovery/contract and P2P together, without replaying history.
            warmup = time.monotonic()
            while True:
                counts = page.evaluate("({...Panorama.flowStats})")
                if (
                    counts["delegation"] > 0
                    and counts["p2p"] > 0
                    and sum(r["kind"] == "order" for r in traffic.records) >= 4
                ):
                    break
                if time.monotonic() - warmup > 90:
                    raise TimeoutError("Both traffic families did not start")
                page.wait_for_timeout(200)
            start = time.monotonic()
            monitor_trim = start - video_origin
            activity = [{"at": 0, **counts}]
            next_log = 0
            while time.monotonic() - start < a.seconds:
                page.wait_for_timeout(200)
                activity.append(
                    {
                        "at": round(time.monotonic() - start, 3),
                        **page.evaluate(
                            "({...Panorama.flowStats,...Panorama.activitySnapshot()})"
                        ),
                    }
                )
                if activity[-1]["at"] >= next_log:
                    print("Monitor elapsed", round(activity[-1]["at"]), "s", flush=True)
                    next_log += 20
            mixed = summarize(activity)
            from mixed_activity import density

            dense = density(activity)
            (folder / "mixed-activity.json").write_text(
                json.dumps(
                    {"samples": activity, "summary": mixed, "density": dense},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = traffic.close()
            traffic = None
            page.wait_for_timeout(5000)
            page.evaluate("Panorama.restore(cases.list)")
            residual = page.locator("#pnWires > *").count()
            assert residual == 0, ("Completed traffic left SVG remnants", residual)
            flow_stats = page.evaluate("Panorama.flowStats")
            node_layout = page.locator("#pnAsstList .pn-asst").evaluate_all(
                "nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {id:n.dataset.id,x:r.x,y:r.y,width:r.width,height:r.height};})"
            )
            panels = page.locator("#pn>.pn-mid,#pn>.pn-right").evaluate_all(
                "nodes=>Object.fromEntries(nodes.map(n=>{const r=n.getBoundingClientRect();return [n.classList.contains('pn-mid')?'components':'services',{x:r.x,y:r.y,width:r.width,height:r.height}]}))"
            )
            assert (
                abs(
                    panels["components"]["y"]
                    + panels["components"]["height"]
                    - panels["services"]["y"]
                    - panels["services"]["height"]
                )
                < 2
            )
            log_count = page.locator("#pnLogRows .log-row").count()
            assert 0 < log_count <= 200
            ui_proof = {
                "sharedLayout": True,
                "equalComponentWidths": widths,
                "componentClickAndEscape": True,
                "residualWiresAfterIdle": residual,
                "hiddenObserver": True,
                "logRows": log_count,
                "equalColumnHeights": True,
            }

            users = page.request.get(base + "/api/admin/users").json()
            registry = page.request.get(base + "/api/admin/registry").json()
            stats = page.request.get(base + "/api/admin/stats").json()
            page.screenshot(path=str(folder / "monitor-final.png"))
            video = page.video
            context.close()
            video.save_as(folder / "monitor.webm")
            browser.close()
        db = sqlite3.connect(
            "file:" + str(folder / "core/ioalab.sqlite") + "?mode=ro", uri=True
        )
        db.row_factory = sqlite3.Row
        events = []
        for row in db.execute(
            "SELECT seq,at,op,sender,receiver,card_digest,period,amount,outcome FROM events ORDER BY seq"
        ):
            events.append(dict(row))
        db.close()
        tree = [
            e
            for e in events
            if e["op"] == "discovery.search" and e["outcome"] == "a2x-tree"
        ]
        if not tree:
            raise RuntimeError(
                "No actual a2x-tree event; do not label fallback vector search as A2X navigation"
            )
        accounts = json.loads((folder / "participants/accounts.json").read_text())
        actors = [
            {
                "id": u["id"],
                "name": "实验助手" + str(i + 1).zfill(2),
                "agents": [{"id": x["id"], "role": x["role"]} for x in u["agents"]],
            }
            for i, x in enumerate(accounts)
            for u in users
            if u["username"] == x["username"]
        ]
        safe = {
            "monitorTrim": round(monitor_trim, 3),
            "actors": actors,
            "layout": {"assistants": node_layout, "panels": panels},
            "services": [
                {
                    k: s.get(k)
                    for k in ("id", "name", "category", "formation", "price", "url")
                }
                for s in registry["services"]
            ],
            "categories": registry.get("categories", []),
            "events": events,
            "stats": stats,
            "records": result["records"],
            "proof": {
                "participants": a.participants,
                "a2xTreeSearches": len(tree),
                "orders": sum(r["kind"] == "order" for r in result["records"]),
                "messages": sum(r["kind"] == "message" for r in result["records"]),
                "errors": result["errors"],
                "browserErrors": errors,
                "ui": ui_proof,
                "renderedTransmissions": flow_stats,
                "mixedActivity": mixed,
                "density": dense,
                "messageKinds": sorted(
                    set(
                        r["interface"]
                        for r in result["records"]
                        if r["kind"] == "message"
                    )
                ),
                "serviceExecution": "labelled sample outputs",
                "monitorSeconds": a.seconds,
            },
        }
        (folder / "public-run.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert (
            not errors
            and not result["errors"]
            and safe["proof"]["orders"] >= 6
            and mixed["passed"]
            and dense["passed"]
        ), safe["proof"]
        print(json.dumps(safe["proof"], ensure_ascii=False), flush=True)
    finally:
        if traffic:
            traffic.close()
        for proc in reversed(processes):
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    proc.terminate()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
