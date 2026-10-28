"""Independent SDK participants with jittered activity; no hosted personal assistants."""

import json
import random
import secrets
import sys
import threading
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "sdk"),
    str(ROOT / "official-services"),
    str(ROOT / "experiments"),
]
from ioalab_sdk import LabClient
from ioalab_sdk.messaging import MailboxClient
from catalog import definitions
from traffic import run_order

KINDS = {
    "meeting.request": "预约会议",
    "safety.checkin": "报平安",
    "repair.request": "预约维修",
    "meal.invitation": "吃饭邀请",
    "travel.update": "行程同步",
    "document.share": "资料交流",
}


class NetworkTraffic:
    def __init__(self, config, folder, count=36, services=None):
        self.config, self.folder = config, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stop, self.ready = threading.Event(), threading.Event()
        self.records, self.threads, self.accounts, self.errors = [], [], [], []
        self.services = services or [
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
        self.service_locks = {name: threading.Lock() for name in self.services}
        run = secrets.token_hex(3)
        for i in range(count):
            account = {
                "username": f"lab_{run}_{i+1:02}",
                "display_name": f"个人助手 {i+1:02}",
                "password": secrets.token_urlsafe(24),
            }
            response = httpx.post(
                config["base"] + "/api/auth/register",
                json=account,
                trust_env=False,
                timeout=30,
            )
            response.raise_for_status()
            self.accounts.append(account)
        (self.folder / "accounts.json").write_text(
            json.dumps(self.accounts), encoding="utf-8"
        )

    def start(self):
        for i, account in enumerate(self.accounts):
            client = LabClient(
                self.config["base"],
                account["username"],
                account["password"],
                self.folder / account["username"],
                source="experiment",
            )
            box = MailboxClient(
                self.config["base"],
                account["username"],
                account["password"],
                client.home,
            )
            for name, label in KINDS.items():
                box.publish(
                    name,
                    {
                        "description": label + "（独立实验，无外部通知）",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "maxLength": 200}
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    },
                )
            # Message I/O must continue while a separate worker waits on A2X or a contract.
            jobs = [(self.run_messages, (i, box))]
            jobs.append((self.run_orders, (i, client)))
            for target, args in jobs:
                thread = threading.Thread(target=target, args=args, daemon=True)
                self.threads.append(thread)
                thread.start()
        self.ready.set()

    def run_messages(self, i, box):
        self.ready.wait()
        rng = random.Random(secrets.randbits(64))
        # Spread initial traffic over the whole recurrence interval, not one opening burst.
        next_send = time.monotonic() + rng.uniform(0.5, 12)
        next_receive = time.monotonic() + rng.uniform(0.1, 3)
        try:
            while not self.stop.wait(0.18):
                now = time.monotonic()
                if now >= next_send:
                    target = rng.choice(
                        [a for j, a in enumerate(self.accounts) if j != i]
                    )["username"]
                    kind = rng.choice(list(KINDS))
                    message = box.send(
                        target,
                        kind,
                        {"text": KINDS[kind] + "：本地实验请求，不触发邮件或真实预约"},
                    )
                    self.records.append(
                        {
                            "kind": "message",
                            "id": message["id"],
                            "actor": i,
                            "interface": kind,
                            "at": time.time(),
                        }
                    )
                    next_send = time.monotonic() + rng.uniform(14, 20)
                if now >= next_receive:
                    box.sync()
                    box.process(lambda m: {"status": "received", "experiment": True})
                    next_receive = time.monotonic() + rng.uniform(0.5, 3.5)
        except Exception as exc:
            self.errors.append(
                {"actor": i, "error": type(exc).__name__, "detail": str(exc)[:300]}
            )
        finally:
            box.close()

    def run_orders(self, i, client):
        self.ready.wait()
        rng = random.Random(secrets.randbits(64))
        definition = next(
            d for d in definitions() if d["id"] == self.services[i % len(self.services)]
        )
        url = self.config["provider"] + "/a2a/providers/svc-" + definition["id"]
        try:
            if self.stop.wait(rng.uniform(0.5, 8)):
                return
            rounds = 0
            while not self.stop.is_set():
                with self.service_locks[definition["id"]]:
                    if self.stop.is_set():
                        break
                    started = time.time()
                    # Repeat commissions may use a previously discovered service.
                    if rounds % 2 == 0:
                        client.search(definition["name"], 8)
                    result = run_order(client, definition, url)
                    # Acceptance/payment precedes the provider's asynchronous capacity release.
                    until = time.monotonic() + 30
                    while (
                        result["status"] == "accepted"
                        and client.api("GET", "/orders/" + result["cardDigest"])[
                            "state"
                        ]
                        != "completed"
                    ):
                        if time.monotonic() > until:
                            raise TimeoutError(
                                "Provider did not complete the accepted contract"
                            )
                        time.sleep(0.5)
                if result["status"] != "accepted":
                    raise RuntimeError("Experiment contract did not pass acceptance")
                self.records.append(
                    {
                        "kind": "order",
                        "actor": i,
                        "started": started,
                        "at": time.time(),
                        "service": definition["id"],
                        "formation": definition.get("formation", "preapproval"),
                        **result,
                    }
                )
                rounds += 1
                if self.stop.wait(rng.uniform(12, 20)):
                    break
        except Exception as exc:
            self.errors.append(
                {
                    "actor": i,
                    "worker": "order",
                    "error": type(exc).__name__,
                    "detail": str(exc)[:300],
                }
            )
        finally:
            client.close()

    def close(self):
        self.stop.set()
        self.ready.set()
        for thread in self.threads:
            thread.join(30)
        if any(t.is_alive() for t in self.threads):
            raise RuntimeError("Experiment participant did not exit")
        proof = {
            "source": "experiment",
            "participants": len(self.accounts),
            "records": self.records,
            "errors": self.errors,
        }
        (self.folder / "traffic.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return proof
