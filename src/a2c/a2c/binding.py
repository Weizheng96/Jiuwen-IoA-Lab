"""A2A binding over ``a2a-sdk`` 1.0 (design: 设计/线上编码.md §1).

Server side: :func:`build_app` serves an SDK endpoint (Provider, Market, Certified Service,
Payment Provider) as an A2A agent: Agent Card with the extension, JSON-RPC, JWKS. Every
operation is one ``SendMessage`` whose single DataPart (``application/a2c+json``) carries
``{"op": …}``; files ride as raw Parts named by ``filename``. Task-based operations return an
A2A task whose artifacts are the results; later state changes (a step request, anchor
notices) show through ``GetTask``.

Client side: :class:`A2ATransport` implements the SDK ``Transport`` over the same wire, so
the Client SDK and the providers' outbound calls run unchanged over A2A.
"""
from __future__ import annotations

import asyncio
import copy
import threading
import uuid
from typing import Any, Callable, Mapping

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.client.client import ClientCallContext
from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.helpers.proto_helpers import new_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (AgentCapabilities, AgentCard, AgentExtension, AgentInterface, AgentSkill, Artifact,
                       GetTaskRequest, Message, Part, Role, SendMessageRequest, Task, TaskState, TaskStatus)
from a2a.utils.errors import ExtensionSupportRequiredError, InvalidParamsError
from google.protobuf import json_format, struct_pb2
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .common import Endpoint, Reply
from .common import Task as A2CTask
from .errors import A2CError, INVALID_OBJECT, fail

A2C_EXTENSION = "urn:ietf:params:a2c:ext"
AP2_EXTENSION = "https://github.com/google-agentic-commerce/ap2/v1"
MEDIA_TYPE = "application/a2c+json"
RPC_PATH = "/a2a"
CARD_PATH = "/.well-known/agent-card.json"
JWKS_PATH = "/.well-known/jwks.json"

_STATES = {"working": TaskState.TASK_STATE_WORKING, "completed": TaskState.TASK_STATE_COMPLETED,
           "failed": TaskState.TASK_STATE_FAILED, "rejected": TaskState.TASK_STATE_REJECTED,
           "canceled": TaskState.TASK_STATE_CANCELED, "auth-required": TaskState.TASK_STATE_AUTH_REQUIRED}
_NAMES = {v: k for k, v in _STATES.items()}


def card_url(party: str) -> str:
    """A party is named by its Agent Card URL; a bare origin means the well-known card."""
    return party if party.endswith(".json") else party.rstrip("/") + CARD_PATH


def extension_for(op: str) -> str:
    return AP2_EXTENSION if op.startswith("ap2.") else A2C_EXTENSION


# ── parts ───────────────────────────────────────────────────────────────

def _ints(obj: Any) -> Any:
    """protobuf Value numbers are doubles; JSON makes no difference between 2 and 2.0."""
    if isinstance(obj, float) and obj.is_integer() and abs(obj) <= 2**53:
        return int(obj)
    if isinstance(obj, list):
        return [_ints(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _ints(v) for k, v in obj.items()}
    return obj


def to_parts(body: Mapping[str, Any], files: Mapping[str, bytes] | None = None) -> list[Part]:
    value = struct_pb2.Value()
    json_format.ParseDict(dict(body), value)
    parts = [Part(data=value, media_type=MEDIA_TYPE)]
    for name, data in (files or {}).items():
        parts.append(Part(raw=data, filename=name, media_type="application/octet-stream"))
    return parts


def from_parts(parts) -> tuple[dict, dict[str, bytes]]:
    bodies = [p for p in parts if p.HasField("data") and p.media_type == MEDIA_TYPE]
    if len(bodies) != 1:
        fail(INVALID_OBJECT, "a message carries exactly one application/a2c+json DataPart")
    body = _ints(json_format.MessageToDict(bodies[0].data))
    if not isinstance(body, dict):
        fail(INVALID_OBJECT, "the DataPart must hold an object")
    files = {p.filename: p.raw for p in parts if p.HasField("raw")}
    return body, files


# ── server ──────────────────────────────────────────────────────────────

def _artifact_id(task_id: str, i: int) -> str:
    return f"{task_id}-a{i}"


class EndpointTaskStore(InMemoryTaskStore):
    """A2A tasks that reflect the endpoint's own task state (set outside any request)."""

    def __init__(self, endpoint: Endpoint) -> None:
        super().__init__()
        self.endpoint = endpoint

    async def get(self, task_id, context=None):
        task = await super().get(task_id, context)
        ours = self.endpoint.tasks.get(task_id)
        if task is None or ours is None:
            return task
        task = copy.deepcopy(task)
        task.status.state = _STATES[ours.state]
        task.status.ClearField("message")
        if ours.status:
            task.status.message.CopyFrom(new_message(to_parts(ours.status), task_id=task_id, context_id=task.context_id))
        have = {a.artifact_id for a in task.artifacts}
        for i, art in enumerate(ours.artifacts):
            if _artifact_id(task_id, i) not in have:
                task.artifacts.append(Artifact(artifact_id=_artifact_id(task_id, i), name="a2c", parts=to_parts(art)))
        return task


class A2CExecutor(AgentExecutor):
    def __init__(self, endpoint: Endpoint, extensions: tuple[str, ...]) -> None:
        self.endpoint = endpoint
        self.extensions = extensions

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            body, files = from_parts(context.message.parts)
        except A2CError as e:
            raise InvalidParamsError(str(e)) from e
        op = str(body.pop("op", ""))
        current = context.current_task
        if current is not None:
            reply = await asyncio.to_thread(self.endpoint.handle, op, body, files, current.id)
            updater = TaskUpdater(event_queue, current.id, current.context_id)
            await updater.add_artifact(to_parts(reply.body, reply.files), name=f"reply:{op}")
            await self._mirror(updater, current.id)
            return
        reply = await asyncio.to_thread(self.endpoint.handle, op, body, files, None, new_task_id=context.task_id,
                                        defer=True)
        if reply.task_id is None:
            await event_queue.enqueue_event(new_message(to_parts(reply.body, reply.files), context_id=context.context_id))
            return
        # the Task goes out before its slow part runs, so a SendMessage with return_immediately comes back at
        # once and the Client follows the Task with GetTask (设计/线上编码.md §一)
        await event_queue.enqueue_event(Task(id=reply.task_id, context_id=context.context_id,
                                             status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                                             history=[context.message]))
        if reply.then is not None:
            try:
                reply = await asyncio.to_thread(self.endpoint.finish, reply)
            except A2CError:
                pass                                # the task now carries the error as its result
        ours = self.endpoint.task(reply.task_id)
        updater = TaskUpdater(event_queue, reply.task_id, context.context_id)
        for i, art in enumerate(ours.artifacts):
            await updater.add_artifact(to_parts(art, reply.files if i == 0 else None),
                                       artifact_id=_artifact_id(reply.task_id, i), name="a2c")
        await self._mirror(updater, reply.task_id)

    async def _mirror(self, updater: TaskUpdater, task_id: str) -> None:
        ours = self.endpoint.task(task_id)
        message = updater.new_agent_message(to_parts(ours.status)) if ours.status else None
        await updater.update_status(_STATES[ours.state], message=message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise InvalidParamsError("A2C tasks end by protocol operations, not by cancel")


class A2CRequestHandler(DefaultRequestHandler):
    """Rejects a request that does not activate the extension of its operation, before execution."""

    async def on_message_send(self, params, context):
        try:
            body, _files = from_parts(params.message.parts)
        except A2CError as e:
            raise InvalidParamsError(str(e)) from e
        uri = extension_for(str(body.get("op", "")))
        if uri not in context.requested_extensions:
            raise ExtensionSupportRequiredError(f"request must activate {uri}")
        return await super().on_message_send(params, context)


def agent_card(*, name: str, base_url: str, role: str, extensions: tuple[str, ...] = (A2C_EXTENSION,),
               description: str = "") -> AgentCard:
    exts = []
    for uri in extensions:
        params = struct_pb2.Struct()
        if uri == A2C_EXTENSION:
            params.update({"role": role, "jwksUri": base_url + JWKS_PATH})
        exts.append(AgentExtension(uri=uri, required=True, params=params, description="A2C" if uri == A2C_EXTENSION else "AP2"))
    return AgentCard(
        name=name, description=description or f"A2C {role}", version="1",
        supported_interfaces=[AgentInterface(url=base_url + RPC_PATH, protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False, extensions=exts),
        default_input_modes=[MEDIA_TYPE], default_output_modes=[MEDIA_TYPE],
        skills=[AgentSkill(id=f"a2c-{role}", name=f"A2C {role}", description=description or f"A2C {role}",
                           tags=["a2c"])])


def build_app(endpoint: Endpoint, *, name: str, base_url: str, role: str, jwks: Mapping[str, Any],
              extensions: tuple[str, ...] = (A2C_EXTENSION,)) -> Starlette:
    """A Starlette app serving ``endpoint`` as an A2A agent (card, JSON-RPC, JWKS)."""
    card = agent_card(name=name, base_url=base_url, role=role, extensions=extensions)
    handler = A2CRequestHandler(agent_executor=A2CExecutor(endpoint, extensions),
                                    task_store=EndpointTaskStore(endpoint), agent_card=card)
    keys = dict(jwks)
    routes = [*create_agent_card_routes(card, card_url=CARD_PATH), *create_jsonrpc_routes(handler, RPC_PATH),
              Route(JWKS_PATH, lambda request: JSONResponse(keys))]
    return Starlette(routes=routes)


# ── client ──────────────────────────────────────────────────────────────

class A2ATransport:
    """The SDK ``Transport`` over A2A. Calls are synchronous; a private event loop runs them.

    Live transports are kept referenced until :meth:`close`, so their loops (and the a2a-sdk
    tasks running on them) are never finalized from another thread.
    """

    _live: list["A2ATransport"] = []

    def __init__(self, http: Callable[[], httpx.AsyncClient] | None = None, timeout: float = 60.0) -> None:
        self._make_http = http or (lambda: httpx.AsyncClient(timeout=timeout))
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._http: httpx.AsyncClient | None = None
        self._clients: dict[str, Any] = {}
        self._cards: dict[str, dict] = {}
        A2ATransport._live.append(self)

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = self._make_http()
        return self._http

    async def _card(self, url: str) -> dict:
        url = card_url(url)
        if url not in self._cards:
            r = await (await self._http_client()).get(url)
            r.raise_for_status()
            self._cards[url] = r.json()
        return self._cards[url]

    async def _client(self, url: str):
        url = card_url(url)
        if url not in self._clients:
            card = parse_agent_card(copy.deepcopy(await self._card(url)))
            factory = ClientFactory(ClientConfig(streaming=False, polling=True, httpx_client=await self._http_client()))
            self._clients[url] = factory.create(card)
        return self._clients[url]

    def jwks(self, party: str) -> dict:
        """JWKS of a party: its Agent Card's A2C extension ``params.jwksUri`` (or a JWKS URL itself)."""
        async def go():
            http = await self._http_client()
            if party.endswith(CARD_PATH) or not party.endswith(".json"):
                card = await self._card(card_url(party))
                for ext in card.get("capabilities", {}).get("extensions", []):
                    if ext.get("uri") == A2C_EXTENSION:
                        r = await http.get(ext["params"]["jwksUri"])
                        r.raise_for_status()
                        return r.json()
                fail(INVALID_OBJECT, f"{party} does not declare the A2C extension")
            r = await http.get(party)
            r.raise_for_status()
            return r.json()
        return self._run(go())

    def send(self, to: str, op: str, body: Mapping[str, Any], files: Mapping[str, bytes] | None = None,
             task_id: str | None = None) -> Reply:
        return self._run(self._send(to, op, dict(body), dict(files or {}), task_id))

    async def _send(self, to, op, body, files, task_id) -> Reply:
        client = await self._client(to)
        message = Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()), parts=to_parts({"op": op, **body}, files),
                          extensions=[extension_for(op)])
        if task_id:
            message.task_id = task_id
        ctx = ClientCallContext(service_parameters=ServiceParametersFactory.create([with_a2a_extensions([extension_for(op)])]))
        last = None
        async for resp in client.send_message(SendMessageRequest(message=message), context=ctx):
            last = resp
        if last.HasField("message"):
            out, out_files = from_parts(last.message.parts)
            reply = Reply(out, out_files)
        else:
            task = last.task
            arts = [a for a in task.artifacts if a.name == f"reply:{op}"] if task_id else \
                   [a for a in task.artifacts if a.artifact_id == _artifact_id(task.id, 0)]
            if arts:
                out, out_files = from_parts(arts[-1].parts)
            elif task_id:
                fail(INVALID_OBJECT, f"{op}: no reply on the task")
            else:                                   # a stream task (anchor.watch): results come later
                out, out_files = {"op": op}, {}
            reply = Reply(out, out_files, task_id=task.id, state=_NAMES.get(task.status.state, "working"))
        if "error" in reply.body:
            raise A2CError(reply.body["error"]["code"], reply.body["error"].get("message", ""))
        return reply

    def get_task(self, to: str, task_id: str) -> A2CTask:
        async def go():
            client = await self._client(to)
            ctx = ClientCallContext(service_parameters=ServiceParametersFactory.create([with_a2a_extensions([A2C_EXTENSION])]))
            task = await client.get_task(GetTaskRequest(id=task_id), context=ctx)
            artifacts = [from_parts(a.parts)[0] for a in task.artifacts if a.artifact_id.startswith(f"{task_id}-a")]
            status = from_parts(task.status.message.parts)[0] if task.status.HasField("message") and \
                task.status.message.parts else None
            return A2CTask(id=task.id, op="", state=_NAMES.get(task.status.state, "working"), artifacts=artifacts,
                           status=status)
        return self._run(go())

    def close(self) -> None:
        """Close the HTTP client, cancel what still runs on the loop, stop it."""
        async def go():
            if self._http is not None:
                await self._http.aclose()
            me = asyncio.current_task()
            rest = [t for t in asyncio.all_tasks() if t is not me]
            for t in rest:
                t.cancel()
            await asyncio.gather(*rest, return_exceptions=True)
            await self._loop.shutdown_asyncgens()
        if self._loop.is_running():
            self._run(go())
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        if self in A2ATransport._live:
            A2ATransport._live.remove(self)

    @classmethod
    def close_all(cls) -> None:
        for t in list(cls._live):
            t.close()


class HostRouter(httpx.AsyncBaseTransport):
    """Routes requests by host to in-process ASGI apps (tests, single-process deployments)."""

    def __init__(self, apps: Mapping[str, Any]) -> None:
        self.apps = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self.apps.get(request.url.host)
        if transport is None:
            return httpx.Response(404, json={"error": f"no app for {request.url.host}"})
        return await transport.handle_async_request(request)


__all__ = ["A2C_EXTENSION", "AP2_EXTENSION", "MEDIA_TYPE", "to_parts", "from_parts", "A2CExecutor", "EndpointTaskStore",
           "A2CRequestHandler", "agent_card", "build_app", "A2ATransport", "HostRouter"]
