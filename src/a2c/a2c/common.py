"""What the three SDKs share: key directory, clock, store, endpoints, tasks and the in-process network."""
from __future__ import annotations

import copy
import itertools
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .codec import canonical, loads, utcnow
from .errors import A2CError, BAD_SIGNATURE, INVALID_OBJECT, UNKNOWN_CARD, fail
from .jws import find_key

Clock = Callable[[], datetime]


class Directory:
    """Resolves JWKS-published keys: party id → JWKS. ``fetch`` is used for unknown parties."""

    def __init__(self, fetch: Callable[[str], Mapping[str, Any]] | None = None) -> None:
        self._jwks: dict[str, dict] = {}
        self._fetched: dict[str, dict] = {}
        self._fetch = fetch

    def add(self, party: str, jwks: Mapping[str, Any]) -> None:
        self._jwks[party] = {"keys": [dict(k) for k in jwks.get("keys", [])]}

    def add_signer(self, signer) -> None:
        self.add(signer.id, {"keys": [signer.jwk]})

    def jwk(self, party: str, kid: str | None, uri: str | None = None) -> dict:
        """Key ``kid`` of ``party``. Keys added by configuration are used as they are; keys fetched
        from a ``uri`` are kept under (party, uri), so a JWKS URL one object names never stands in
        for the party elsewhere."""
        if party in self._jwks:
            return find_key(self._jwks[party], kid)
        key = f"{party} {uri}" if uri else party
        if key not in self._fetched and self._fetch is not None:
            self._fetched[key] = {"keys": [dict(k) for k in self._fetch(uri or party).get("keys", [])]}
        if key not in self._fetched:
            fail(BAD_SIGNATURE, f"no keys for {party}")
        return find_key(self._fetched[key], kid)


class Store:
    """A small JSON document store (in memory, optionally persisted to a file)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._data: dict[str, dict] = {}
        self._lock = threading.RLock()
        if self._path and self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def put(self, table: str, key: str, value: Any) -> None:
        with self._lock:
            self._data.setdefault(table, {})[key] = copy.deepcopy(value)
            self._save()

    def get(self, table: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(table, {}).get(key, default))

    def all(self, table: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data.get(table, {}))

    def delete(self, table: str, key: str) -> None:
        with self._lock:
            self._data.get(table, {}).pop(key, None)
            self._save()

    def _save(self) -> None:
        if self._path:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._path)


# ── messages and tasks ──────────────────────────────────────────────────

@dataclass
class Reply:
    """A result body ``{"op", …}``; ``task_id``/``state`` are set for task-based operations."""

    body: dict
    files: dict[str, bytes] = field(default_factory=dict)
    task_id: str | None = None
    state: str | None = None
    then: Any = None                 # the slow part of a task operation, run after the task is returned


@dataclass
class Task:
    id: str
    op: str
    state: str = "working"           # working · auth-required · completed · failed · rejected
    artifacts: list[dict] = field(default_factory=list)
    status: dict | None = None       # the latest status message body, e.g. {"stepRequest": …}
    files: dict[str, bytes] = field(default_factory=dict)


_MALFORMED = (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError, ArithmeticError)


class Endpoint:
    """Server side of an SDK: dispatches ``op`` to ``op_<name>`` methods and keeps tasks."""

    OPS: tuple[str, ...] = ()
    _ids = itertools.count(1)

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self._lock = threading.RLock()
        self._next_task_id: str | None = None

    def new_task(self, op: str) -> Task:
        with self._lock:
            tid, self._next_task_id = self._next_task_id or f"task-{next(self._ids)}", None
            t = Task(id=tid, op=op)
            self.tasks[t.id] = t
            return t

    def task(self, task_id: str) -> Task:
        if task_id not in self.tasks:
            fail(UNKNOWN_CARD, f"no task {task_id}")
        return self.tasks[task_id]

    def handle(self, op: str, body: Mapping[str, Any], files: Mapping[str, bytes] | None = None,
               task_id: str | None = None, *, new_task_id: str | None = None, defer: bool = False) -> Reply:
        """Dispatch one operation. ``new_task_id`` lets a binding reuse the id its transport assigned.

        A task operation validates its request at once and may leave its slow part (a review, drafting,
        material checks) in ``Reply.then``; it runs here unless ``defer`` is set, in which case the binding
        returns the task first and calls :meth:`finish` afterwards (设计/线上编码.md §一)."""
        if op not in self.OPS:
            return Reply({"op": op, "error": {"code": INVALID_OBJECT, "message": f"unsupported op {op}"}})
        try:
            with self._lock:
                self._next_task_id = new_task_id
                try:
                    reply = getattr(self, "op_" + op.replace(".", "_"))(dict(body), dict(files or {}), task_id)
                finally:
                    self._next_task_id = None
            if reply.then is not None and not defer:
                reply = self.finish(reply)
            return reply
        except A2CError as e:
            return Reply(e.to_payload(op))
        except _MALFORMED as e:
            return Reply({"op": op, "error": {"code": INVALID_OBJECT, "message": f"malformed request: {e!r}"}})

    def finish(self, reply: Reply) -> Reply:
        """Run the deferred part of a task operation. An error ends the task as ``failed`` with the error
        as its result, and is raised to the caller."""
        with self._lock:
            try:
                return reply.then()
            except A2CError as e:
                self._fail_task(reply, e)
                raise
            except _MALFORMED as e:
                err = A2CError(INVALID_OBJECT, f"malformed request: {e!r}")
                self._fail_task(reply, err)
                raise err from e

    def _fail_task(self, reply: Reply, e: A2CError) -> None:
        task = self.tasks.get(reply.task_id or "")
        if task is not None and task.state not in ("completed", "failed", "rejected"):
            task.state = "failed"
            task.artifacts.append({"error": e.to_payload(reply.body.get("op", ""))["error"]})


class Transport(Protocol):
    def send(self, to: str, op: str, body: Mapping[str, Any], files: Mapping[str, bytes] | None = None,
             task_id: str | None = None) -> Reply: ...

    def get_task(self, to: str, task_id: str) -> Task: ...


def _wire(obj: Any) -> Any:
    return loads(canonical(obj))


class LocalNetwork:
    """In-process transport: endpoints by URL; bodies make a JSON round trip, errors raise."""

    def __init__(self) -> None:
        self.endpoints: dict[str, Endpoint] = {}
        self.log: list[tuple[str, str, dict]] = []

    def register(self, url: str, endpoint: Endpoint) -> None:
        self.endpoints[url] = endpoint

    def send(self, to: str, op: str, body: Mapping[str, Any], files: Mapping[str, bytes] | None = None,
             task_id: str | None = None) -> Reply:
        if to not in self.endpoints:
            fail(UNKNOWN_CARD, f"no endpoint {to}")
        request = _wire({"op": op, **body})
        self.log.append((to, op, request))
        reply = self.endpoints[to].handle(op, {k: v for k, v in request.items() if k != "op"}, files, task_id)
        reply.body = _wire(reply.body)
        if "error" in reply.body:
            raise A2CError(reply.body["error"]["code"], reply.body["error"].get("message", ""))
        return reply

    def get_task(self, to: str, task_id: str) -> Task:
        return copy.deepcopy(self.endpoints[to].task(task_id))


def now_or(clock: Clock | None) -> Clock:
    return clock or utcnow


__all__ = ["Clock", "Directory", "Store", "Reply", "Task", "Endpoint", "Transport", "LocalNetwork", "now_or"]
