"""Resume the original countersign without replaying requests, effects or signatures."""
import base64
import copy

import pytest

from a2c.common import Directory, Store, Task
from a2c.codec import digest
from a2c.card import card_digest
from a2c.errors import A2CError
from a2c.provider import DefaultPolicy, Provider
from .support import MARKET, PROVIDER, World


class PausedPolicy(DefaultPolicy):
    def __init__(self):
        self.calls = 0
        self.paused = True
        self.reason = None

    def review(self, *args):
        self.calls += 1
        if self.paused:
            raise RuntimeError("original policy stopped before returning its decision")
        return self.reason


def prepared(tmp_path, monkeypatch=None):
    policy = PausedPolicy(); world = World(policy=policy)
    world.provider.store = Store(tmp_path / "provider.json")
    template = world.publish("countersign")
    [template] = world.client.get_templates(PROVIDER, template["templateDigest"])
    draft = world.client.draft_card(template, card_id="resume-original", spec=world.spec(),
        materials=world.materials(), signers={"slot1": "user", "slot7": "client"},
        delegation_scope_digest="sha256:" + "d" * 64)
    card = world.client.sign(draft.card)
    if monkeypatch is not None:
        monkeypatch.setattr(world.provider, "_materials", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("material verification stopped")))
    with pytest.raises(RuntimeError):
        world.client.countersign(card)
    pending = world.provider.store.all("pending_countersigns")
    assert len(pending) == 1
    task_id = next(iter(pending))
    return world, policy, task_id


def restarted(world, tmp_path, policy):
    return Provider(world.provider_signer, market=MARKET, keys=world.keys, transport=world.net,
        fetch=world.fetch, clock=world.clock, store=Store(tmp_path / "provider.json"),
        policy=policy, model=world.model)


def forbidden(*args, **kwargs):
    pytest.fail("resume performed a policy, model, verifier, network or signing effect")


def test_restart_prepares_original_deferred_without_effects_then_signs_once(tmp_path, monkeypatch):
    world, policy, tid = prepared(tmp_path)
    fresh = restarted(world, tmp_path, policy)
    original = copy.deepcopy(fresh.store.get("pending_countersigns", tid))
    policy.paused = False
    monkeypatch.setattr(fresh, "_materials", forbidden)
    monkeypatch.setattr(fresh, "model", forbidden)
    monkeypatch.setattr(fresh, "fetch", forbidden)
    monkeypatch.setattr(world.net, "send", forbidden)
    monkeypatch.setattr(world.keys, "_fetch", forbidden)
    import a2c.provider as core
    sign = core.sign_slot; signatures = []
    def counted(*args, **kwargs):
        signatures.append(args[1]); return sign(*args, **kwargs)
    monkeypatch.setattr(core, "sign_slot", counted)
    deferred = fresh.resume_countersign(tid)
    duplicate = fresh.resume_countersign(tid)
    assert deferred.task_id == tid and deferred.state == "working"
    assert deferred.body == {"op": "card.countersign"} and policy.calls == 1 and signatures == []
    result = fresh.finish(deferred)
    assert result.task_id == tid and result.state == "completed" and signatures == [2]
    assert policy.calls == 2 and len(fresh.tasks) == 1 and len(world.psp.ledger) == 0
    assert result.body["card"]["signatures"]["1"] == original["body"]["card"]["signatures"]["1"]
    assert result.body["card"]["signatures"]["2"]
    record = fresh.store.get("pending_countersigns", tid)
    assert record["body"] == original["body"] and record["files"] == original["files"]
    terminal = copy.deepcopy(fresh.task(tid))
    with pytest.raises(A2CError): fresh.finish(duplicate)
    assert fresh.task(tid) == terminal
    with pytest.raises(A2CError): fresh.resume_countersign(tid)
    assert policy.calls == 2 and signatures == [2]


def test_unreviewed_materials_cannot_resume_or_start_policy(tmp_path, monkeypatch):
    world, policy, tid = prepared(tmp_path, monkeypatch)
    fresh = restarted(world, tmp_path, policy)
    with pytest.raises(A2CError): fresh.resume_countersign(tid)
    assert policy.calls == 0


@pytest.mark.parametrize("fault", ["op", "task", "provider", "client", "digest", "body", "material", "decision", "completed", "wrong-task-op", "formed"])
def test_changed_or_final_original_countersign_is_not_replayed(tmp_path, fault):
    world, policy, tid = prepared(tmp_path)
    fresh = restarted(world, tmp_path, policy)
    record = fresh.store.get("pending_countersigns", tid)
    if fault == "op": record["op"] = "request.submit"
    elif fault == "task": record["taskId"] = "another-task"
    elif fault == "provider": record["provider"] = "another-provider"
    elif fault == "client": record["client"] = "another-client"
    elif fault == "digest": record["cardDigest"] = "sha256:" + "0" * 64
    elif fault == "body": record["body"]["card"]["cardId"] = "replacement"
    elif fault == "material": record["files"][next(iter(record["files"]))] = base64.b64encode(b"changed").decode()
    elif fault == "decision": record["decisionReturned"] = True
    elif fault == "completed": fresh.tasks[tid] = Task(tid, "card.countersign", state="completed")
    elif fault == "wrong-task-op": fresh.tasks[tid] = Task(tid, "request.submit")
    elif fault == "formed": fresh.store.put("cards", record["cardDigest"], {"state": "formed"})
    fresh.store.put("pending_countersigns", tid, record)
    with pytest.raises(A2CError): fresh.resume_countersign(tid)
    assert policy.calls == 1


def test_resume_does_not_refresh_original_deadline(tmp_path):
    world, policy, tid = prepared(tmp_path); world.clock.advance(days=2)
    with pytest.raises(A2CError) as exc: restarted(world, tmp_path, policy).resume_countersign(tid)
    assert exc.value.code == "deadline-passed" and policy.calls == 1


@pytest.mark.parametrize("after_save", [False, True])
def test_unknown_after_signing_cannot_sign_or_review_again(tmp_path, monkeypatch, after_save):
    world, policy, tid = prepared(tmp_path); policy.paused = False
    fresh = restarted(world, tmp_path, policy); save = fresh._save; calls = []
    def unknown(record):
        calls.append(copy.deepcopy(record))
        if after_save: save(record)
        raise RuntimeError("signed card persistence result is unknown")
    monkeypatch.setattr(fresh, "_save", unknown)
    with pytest.raises(RuntimeError): fresh.finish(fresh.resume_countersign(tid))
    assert len(calls) == 1 and calls[0]["card"]["signatures"]["2"] and policy.calls == 2
    fresh = restarted(world, tmp_path, policy)
    with pytest.raises(A2CError): fresh.resume_countersign(tid)
    assert len(calls) == 1 and policy.calls == 2


def test_returned_decline_is_final_without_signature(tmp_path):
    world, policy, tid = prepared(tmp_path); policy.paused = False; policy.reason = "declined"
    fresh = restarted(world, tmp_path, policy)
    deferred = fresh.resume_countersign(tid)
    duplicate = fresh.resume_countersign(tid)
    result = fresh.finish(deferred)
    terminal = copy.deepcopy(fresh.task(tid))
    with pytest.raises(A2CError): fresh.finish(duplicate)
    assert fresh.task(tid) == terminal
    assert result.body["reason"] == "declined" and result.state == "completed"
    assert not fresh.store.all("cards") and policy.calls == 2
    with pytest.raises(A2CError): restarted(world, tmp_path, policy).resume_countersign(tid)


def test_original_embedded_signer_key_survives_restart_without_key_cache_or_fetch(tmp_path):
    world, policy, tid = prepared(tmp_path)
    fresh = restarted(world, tmp_path, policy); fresh.keys = Directory(fetch=forbidden)
    deferred = fresh.resume_countersign(tid)
    assert deferred.task_id == tid and deferred.then is not None
    assert policy.calls == 1


@pytest.mark.parametrize("change", ["extra-slot3", "extra-slot4", "signer-jwk"])
def test_original_signature_is_verified_without_truncating_changed_card(tmp_path, change):
    world, policy, tid = prepared(tmp_path)
    fresh = restarted(world, tmp_path, policy)
    fresh.keys = Directory(fetch=forbidden)
    record = fresh.store.get("pending_countersigns", tid)
    card = record["body"]["card"]
    if change == "extra-slot3": card["signatures"]["3"] = card["signatures"]["1"]
    elif change == "extra-slot4": card["signatures"]["4"] = [card["signatures"]["1"]]
    else: card[card["signers"]["slot1"]]["jwk"] = world.provider_signer.jwk
    # Even an internally recanonicalized record must pass the original signature algorithm.
    record["bodyDigest"] = digest(record["body"])
    record["cardDigest"] = card_digest(card)
    fresh.store.put("pending_countersigns", tid, record)
    with pytest.raises(A2CError): fresh.resume_countersign(tid)
    assert policy.calls == 1


def test_second_finish_from_active_policy_cannot_reenter_the_original_decision(tmp_path):
    world, policy, tid = prepared(tmp_path); policy.paused = False
    fresh = restarted(world, tmp_path, policy)
    duplicate = fresh.resume_countersign(tid)
    def decision(*args):
        policy.calls += 1
        with pytest.raises(A2CError): fresh.finish(duplicate)
        return None
    policy.review = decision
    result = fresh.finish(fresh.resume_countersign(tid))
    assert result.body["card"]["signatures"]["2"] and policy.calls == 2
    assert fresh.task(tid).artifacts == [{"card": result.body["card"]}]


def test_working_task_protocol_error_still_fails_with_original_error(tmp_path):
    world, policy, tid = prepared(tmp_path)
    fresh = restarted(world, tmp_path, policy)
    deferred = fresh.resume_countersign(tid)
    record = fresh.store.get("pending_countersigns", tid)
    record["bodyDigest"] = "changed"
    fresh.store.put("pending_countersigns", tid, record)
    with pytest.raises(A2CError): fresh.finish(deferred)
    assert fresh.task(tid).state == "failed"
    assert fresh.task(tid).artifacts[-1]["error"]["code"] == "out-of-order"


@pytest.mark.parametrize("malformed", [False, True])
def test_real_terminal_failure_survives_process_restart(tmp_path, malformed):
    world, policy, tid = prepared(tmp_path)
    fresh = restarted(world, tmp_path, policy)
    from a2c.errors import CARD_INVALID
    def rejected(*args):
        if malformed: raise ValueError("malformed original decision")
        raise A2CError(CARD_INVALID, "original protocol rejected")
    policy.review = rejected
    with pytest.raises(A2CError): fresh.finish(fresh.resume_countersign(tid))
    assert fresh.task(tid).state == "failed"
    terminal = copy.deepcopy(fresh.task(tid))
    assert fresh.store.get("pending_countersigns", tid)["terminal"] == "failed"
    with pytest.raises(A2CError): restarted(world, tmp_path, policy).resume_countersign(tid)
    assert fresh.task(tid) == terminal


def test_failed_transition_excludes_concurrent_resume_until_terminal_is_persisted(tmp_path, monkeypatch):
    import threading
    world, policy, tid = prepared(tmp_path)
    fresh = restarted(world, tmp_path, policy)
    from a2c.errors import CARD_INVALID
    def rejected(*args): raise A2CError(CARD_INVALID, "original protocol rejected")
    policy.review = rejected
    deferred = fresh.resume_countersign(tid)
    # Hold the exception-to-terminal boundary open to exercise the public API race.
    entered = threading.Event(); release = threading.Event(); resumed = threading.Event()
    finalize = fresh._fail_task; failures = []; continuation = []
    def paused_finalization(reply, error):
        entered.set()
        assert release.wait(5)
        finalize(reply, error)
    monkeypatch.setattr(fresh, "_fail_task", paused_finalization)
    def finish():
        try: fresh.finish(deferred)
        except A2CError as error: failures.append(error)
    def resume():
        try: continuation.append(fresh.resume_countersign(tid))
        except A2CError as error: continuation.append(error)
        finally: resumed.set()
    first = threading.Thread(target=finish); first.start(); assert entered.wait(5)
    second = threading.Thread(target=resume); second.start()
    try:
        assert not resumed.wait(0.1), "resume crossed an unfinished terminal transition"
    finally:
        release.set(); first.join(5); second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert len(failures) == 1 and len(continuation) == 1 and isinstance(continuation[0], A2CError)
    assert fresh.task(tid).state == "failed"
    assert fresh.store.get("pending_countersigns", tid)["terminal"] == "failed"


def test_counter_pre_review_resume_keeps_original_slot_one_and_task(tmp_path, monkeypatch):
    world, policy, tid = prepared(tmp_path, monkeypatch); fresh = restarted(world, tmp_path, policy)
    original = fresh.store.get('pending_countersigns', tid); policy.paused = False
    scopes = []
    deferred = fresh.resume_material_review(tid, guard=scopes.append)
    assert policy.calls == 0 and not original['reviewed'] and deferred.task_id == tid
    result = fresh.finish(deferred)
    assert result.body['card']['signatures']['1'] == original['body']['card']['signatures']['1']
    assert result.body['card']['signatures']['2'] and result.task_id == tid and policy.calls == 1
    assert len(scopes) == 2 and scopes[0] == scopes[1] and len(fresh.tasks) == 1


def test_counter_material_guard_reentry_does_not_complete_or_sign(tmp_path, monkeypatch):
    world, policy, tid = prepared(tmp_path, monkeypatch); fresh = restarted(world, tmp_path, policy)
    deferred = fresh.resume_material_review(tid, guard=lambda scope: None)
    original = fresh.store.get('pending_countersigns', tid)
    world.clock.advance(days=2)
    with pytest.raises(A2CError): fresh.finish(deferred)
    assert not fresh.store.get('pending_countersigns', tid)['reviewed'] and not fresh.store.all('cards')
    assert original['body']['card']['signatures']['2'] is None and policy.calls == 0
