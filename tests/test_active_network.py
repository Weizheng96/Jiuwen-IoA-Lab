"""Dense single-sender activity is insufficient; accepted contracts must release capacity first."""

import importlib.util
import threading
from pathlib import Path
import network

spec = importlib.util.spec_from_file_location(
    "active_metrics", Path(__file__).resolve().parents[1] / "demo/mixed_activity.py"
)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def test_density_needs_many_concurrent_initiators():
    samples = [
        {
            "at": i / 5,
            "delegation": i * 2,
            "p2p": i * 2,
            "activeLines": 5,
            "initiators": [str((i + j) % 36) for j in range(5)],
        }
        for i in range(151)
    ]
    assert metrics.density(samples)["passed"]
    one = [{**s, "initiators": ["only-one"]} for s in samples]
    assert not metrics.density(one)["passed"]
    quiet = [
        {**s, "delegation": i // 20, "p2p": i // 20} for i, s in enumerate(samples)
    ]
    assert not metrics.density(quiet)["passed"]


def test_next_client_waits_for_actual_completion(monkeypatch):
    traffic = network.NetworkTraffic.__new__(network.NetworkTraffic)
    traffic.ready = threading.Event()
    traffic.ready.set()
    traffic.stop = threading.Event()
    traffic.records = []
    traffic.errors = []
    traffic.services = ["test"]
    traffic.service_locks = {"test": threading.Lock()}
    traffic.config = {"provider": "http://local.invalid"}

    class Random:
        def uniform(self, *args):
            return 0.01

    monkeypatch.setattr(network.random, "Random", lambda seed: Random())
    monkeypatch.setattr(
        network, "definitions", lambda: [{"id": "test", "name": "Test"}]
    )
    waiting = threading.Event()
    release = threading.Event()
    second = threading.Event()
    started = []

    class Client:
        def __init__(self, n):
            self.n = n

        def search(self, *args):
            pass

        def api(self, *args):
            if self.n == 0 and not release.is_set():
                waiting.set()
                return {"state": "started"}
            return {"state": "completed"}

        def close(self):
            pass

    def complete(client, *args):
        started.append(client.n)
        if client.n == 1:
            second.set()
        return {"status": "accepted", "cardDigest": str(client.n)}

    monkeypatch.setattr(network, "run_order", complete)
    first = threading.Thread(target=traffic.run_orders, args=(0, Client(0)))
    other = threading.Thread(target=traffic.run_orders, args=(1, Client(1)))
    first.start()
    try:
        assert waiting.wait(1)
        other.start()
        assert not second.wait(0.15)
        release.set()
        assert second.wait(2)
    finally:
        release.set()
        traffic.stop.set()
        first.join(3)
        if other.ident:
            other.join(3)
    assert not traffic.errors and not first.is_alive() and not other.is_alive()
