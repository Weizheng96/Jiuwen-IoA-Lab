"""Owned real participants for both formal scenarios; no fixture substitutions."""

import argparse, json, os, secrets, socket, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "deploy"), str(ROOT / "sdk")]
from configure_assistant import read_env, install_skill
from ioalab_sdk.schedule import LocalSchedule
from ioalab_sdk.messaging import MailboxClient


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--folder", type=Path, required=True)
    p.add_argument("--model-env", type=Path, required=True)
    p.add_argument("--notification-env", type=Path, required=True)
    p.add_argument("--service-model", required=True, help="Process SDK model.list selection_key")
    a = p.parse_args()
    folder = a.folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    model = read_env(a.model_env)
    notify = read_env(a.notification_env)
    cp, pp = port(), port()
    base = f"http://127.0.0.1:{cp}"
    provider = f"http://127.0.0.1:{pp}"
    accounts = {
        "a": {"username": "ISC_001", "password": secrets.token_urlsafe(24)},
        "b": {"username": "demo_b@huawei.com", "password": secrets.token_urlsafe(24)},
        "outside": {"username": "outside_demo", "password": secrets.token_urlsafe(24)},
    }
    admin = secrets.token_urlsafe(24)
    env = {
        **os.environ,
        **model,
        "PYTHONUTF8": "1",
        "JIUWENSWARM_DATA_DIR": str(ROOT / ".swarm-runtime"),
        "IOALAB_MODE": "dev",
        "IOALAB_STUB": "0",
        "IOALAB_ADMIN_PASSWORD": admin,
        "IOALAB_ASSISTANT_PASSWORD": accounts["a"]["password"],
        "IOALAB_PORT": str(cp),
        "IOALAB_PUBLIC_URL": base,
        "IOALAB_BASE_URL": base,
        "IOALAB_DATA": str(folder / "core"),
        "IOALAB_PROVIDER_PORT": str(pp),
        "IOALAB_PROVIDER_PUBLIC_URL": provider,
        "IOALAB_PROVIDER_DATA": str(folder / "providers"),
        "IOALAB_SWARM_COMMAND": json.dumps(
            [str(ROOT / ".swarm-venv/Scripts/jiuwenswarm-process.exe")]
        ),
        "IOALAB_SERVICE_MODEL": a.service_model,
        "IOALAB_LLM_API_KEY": model.get("IOALAB_LLM_API_KEY") or model["API_KEY"],
        "IOALAB_LLM_BASE_URL": model.get("IOALAB_LLM_BASE_URL") or model["API_BASE"],
        "IOALAB_A2X_MODEL": "qwen-plus",
    }
    config = {
        "folder": str(folder),
        "base": base,
        "provider": provider,
        "accounts": accounts,
        "fixtures": [],
        "swarm": "ws://127.0.0.1:19000/ws?user_id=ioa-admin",
        "swarmWeb": "http://127.0.0.1:5173",
        "swarmModelId": "mdl_ioa_qwen_plus",
        "swarmModelAlias": "IoA演示模型",
        "providerAccount": {"username": "ISP_008", "password": "008"},
    }
    processes = []
    logs = []

    def launch(name, command, values):
        log = (folder / (name + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            env=values,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        processes.append(proc)
        return proc

    def ready(url, proc):
        until = time.monotonic() + 150
        while time.monotonic() < until:
            if proc.poll() is not None:
                raise RuntimeError("Owned process exited; inspect " + str(folder))
            try:
                r = httpx.get(url, timeout=2, trust_env=False)
                if r.is_success and r.json().get("ready", True):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise TimeoutError(url)

    try:
        core = launch("core", [sys.executable, "-m", "ioalab", "serve"], env)
        ready(base + "/api/config", core)
        for key in ("b", "outside"):
            httpx.post(
                base + "/api/auth/register", json=accounts[key], trust_env=False
            ).raise_for_status()
        # Explicit normal admin operation prepares test-only virtual credits; not real money.
        with httpx.Client(base_url=base, trust_env=False) as http:
            http.post("/api/admin/login", json={"password": admin}).raise_for_status()
            users = http.get("/api/admin/users").raise_for_status().json()
            uid = next(u["id"] for u in users if u["username"] == accounts["b"]["username"])
            http.post(
                "/api/admin/wallet",
                json={"user_id": uid, "delta": "1000", "note": "两段正式演示的虚拟体验额度"},
            ).raise_for_status()
        services = launch(
            "providers",
            [
                sys.executable,
                str(ROOT / "official-services/serve.py"),
                "--only",
                "nail-booking-basic,nail-booking-reliable",
            ],
            env,
        )
        ready(provider + "/health", services)
        health = httpx.get(provider + "/health", trust_env=False).raise_for_status().json()
        assert health["execution"] == "swarm"
        schedule = LocalSchedule(folder / "assistant-a/schedule.json")
        state = schedule.init()
        doc = state["document"]
        start = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=2)
        while start.weekday() > 4:
            start += timedelta(days=1)
        start = start.replace(hour=11, minute=0, second=0, microsecond=0)
        free = start.replace(hour=14)
        doc["events"] = [
            {
                "id": "existing-team-meeting",
                "title": "已有团队例会",
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=30)).isoformat(),
                "status": "busy",
            }
        ]
        schedule.replace(doc, state["revision"])
        config["appointments"] = {
            "busy": {
                "title": "IoA协作交流",
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=30)).isoformat(),
                "timeZone": "Asia/Shanghai",
            },
            "free": {
                "title": "九问IoA协作演示 " + time.strftime("%H%M%S"),
                "start": free.isoformat(),
                "end": (free + timedelta(minutes=30)).isoformat(),
                "timeZone": "Asia/Shanghai",
            },
        }
        receiver = {
            **{k: v for k, v in notify.items() if k.startswith("IOALAB_")},
            "IOALAB_BASE_URL": base,
            "IOALAB_USERNAME": accounts["a"]["username"],
            "IOALAB_PASSWORD": accounts["a"]["password"],
            "IOALAB_HOME": str(folder / "assistant-a"),
            "IOALAB_SWARM_WS": config["swarm"],
            "IOALAB_CALENDAR_BACKEND": "local",
            "IOALAB_SCHEDULE_FILE": str(schedule.path),
            "IOALAB_ALLOWED_SENDERS": json.dumps([accounts["b"]["username"]]),
        }
        receiver["IOALAB_SWARM_BRIDGE_SECRET"] = model.get(
            "IOALAB_SWARM_BRIDGE_SECRET"
        ) or notify.get("IOALAB_SWARM_BRIDGE_SECRET", "")
        receiver_file = folder / "assistant-a.env"
        receiver_file.write_text(
            "\n".join(k + "=" + json.dumps(v) for k, v in receiver.items()) + "\n", encoding="utf-8"
        )
        receiver_proc = launch(
            "receiver",
            [
                sys.executable,
                str(ROOT / "personal-assistant/assistant.py"),
                "run",
                "--env",
                str(receiver_file),
            ],
            env,
        )
        values = {
            "IOALAB_BASE_URL": base,
            "IOALAB_USERNAME": accounts["b"]["username"],
            "IOALAB_PASSWORD": accounts["b"]["password"],
            "IOALAB_HOME": str(folder / "client-b"),
        }
        install_skill(ROOT, ROOT / ".swarm-runtime", values)
        skill = ROOT / ".swarm-runtime/agent/workspace/skills/ioalab-delegate"
        settings = skill / "settings.local.json"
        if settings.exists():
            shutil = __import__("shutil")
            shutil.copyfile(settings, folder / "previous-skill-settings.json")
        settings.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        config["skillDirectory"] = str(skill)
        (folder / "run.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        b = MailboxClient(
            base,
            accounts["b"]["username"],
            accounts["b"]["password"],
            folder / "client-b",
            connect_identity=True,
        )
        try:
            until = time.monotonic() + 30
            while not b.interfaces(accounts["a"]["username"]):
                if time.monotonic() > until:
                    raise TimeoutError("Receiver did not publish interfaces")
                time.sleep(0.3)
        finally:
            b.close()
        print(
            "FORMAL CASE STACK READY; real Swarm providers, local editable schedule and configured AgentMail notification. Config:",
            folder / "run.json",
            flush=True,
        )
        while True:
            if any(proc.poll() is not None for proc in processes):
                raise RuntimeError("Owned participant exited")
            time.sleep(1)
    finally:
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
