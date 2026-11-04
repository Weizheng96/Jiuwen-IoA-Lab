"""Reject phase-separated movies and keep mailbox I/O alive through blocked discovery."""

import importlib.util
import threading
from pathlib import Path
import pytest
import network

spec = importlib.util.spec_from_file_location(
    "mixed_activity", Path(__file__).resolve().parents[1] / "demo/mixed_activity.py"
)
mixed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mixed)


def test_equal_totals_do_not_hide_separated_phases():
    separated = [
        {"at": 0, "delegation": 0, "p2p": 0},
        {"at": 10, "delegation": 10, "p2p": 0},
        {"at": 20, "delegation": 10, "p2p": 10},
    ]
    proof = mixed.summarize(separated)
    assert proof["delegationShare"] == 0.5 and not proof["passed"]
    assert proof["mixedShare"] == 0
    concurrent = [{"at": i, "delegation": i * 2, "p2p": i * 2} for i in range(21)]
    assert mixed.summarize(concurrent)["passed"]


def test_counter_reset_is_not_accepted():
    with pytest.raises(ValueError, match="reset"):
        mixed.summarize(
            [
                {"at": 0, "delegation": 3, "p2p": 1},
                {"at": 10, "delegation": 0, "p2p": 4},
            ]
        )


def test_mailbox_keeps_working_during_blocked_order_and_orders_continue(
    monkeypatch, tmp_path
):
    traffic = network.NetworkTraffic.__new__(network.NetworkTraffic)
    traffic.ready = threading.Event()
    traffic.ready.set()
    traffic.stop = threading.Event()
    traffic.records = []
    traffic.errors = []
    traffic.services = ["test"]
    traffic.service_locks = {"test": threading.Lock()}
    traffic.config = {"provider": "http://local.invalid"}
    traffic.accounts = [{"username": "a"}, {"username": "b"}]
    entered = threading.Event()
    release = threading.Event()
    four_orders = threading.Event()
    two_messages = threading.Event()

    class Random:
        def uniform(self, *args):
            return 0.01

        def choice(self, values):
            return list(values)[0]

    monkeypatch.setattr(network.random, "Random", lambda seed: Random())
    monkeypatch.setattr(
        network, "definitions", lambda: [{"id": "test", "name": "Test"}]
    )
    completions = []
    messages = []

    def complete(*args):
        completions.append(True)
        if len(completions) >= 4:
            four_orders.set()
        return {"status": "accepted", "cardDigest": str(len(completions))}

    monkeypatch.setattr(network, "run_order", complete)

    class Client:
        def api(self, *args):
            return {"state": "completed"}

        def search(self, *args):
            entered.set()
            assert release.wait(3)

        def close(self):
            pass

    class Box:
        def send(self, *args):
            messages.append(True)
            if len(messages) >= 2:
                two_messages.set()
            return {"id": str(len(messages))}

        def sync(self):
            pass

        def process(self, handler):
            pass

        def close(self):
            pass

    order = threading.Thread(target=traffic.run_orders, args=(0, Client()))
    mailbox = threading.Thread(target=traffic.run_messages, args=(0, Box()))
    order.start()
    mailbox.start()
    try:
        assert entered.wait(1)
        assert two_messages.wait(2), "Messages stalled behind the order worker"
        assert not completions
        release.set()
        assert four_orders.wait(
            2
        ), "Order generation stopped at the old three-order cap"
    finally:
        traffic.stop.set()
        release.set()
        order.join(3)
        mailbox.join(3)
    assert not order.is_alive() and not mailbox.is_alive() and not traffic.errors
