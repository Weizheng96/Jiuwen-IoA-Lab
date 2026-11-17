"""P40 non-model callback execution. P20 owns routing/binding; roles own effects."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
import hashlib
import json
import threading

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from .host import output_schema_check, validate_output
from .host_submission import HostToolCall, HostToolResult, HostToolDeclaration
from .protocol import RuntimeFault, encode


class CallbackPool:
    """Assembly-owned bounded control workers, separate from the model scheduler."""
    def __init__(self, capacity, *, executor=None):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('Use a positive callback capacity')
        self.capacity = capacity
        self.executor = executor or ThreadPoolExecutor(max_workers=capacity, thread_name_prefix='ioa-p40')
        self.condition = threading.Condition(threading.RLock())
        self.active = set()
        self.closed = False

    def submit(self, function, *arguments):
        with self.condition:
            if self.closed:raise RuntimeFault('closed', 'SDK callback pool is draining')
            if len(self.active) >= self.capacity:
                raise RuntimeFault('busy', 'SDK callbacks are still executing')
            future = self.executor.submit(function, *arguments)
            self.active.add(future)
            future.add_done_callback(self._finished)
            return future

    def _finished(self, future):
        # Includes cancellation before execution: no callback finally is required.
        with self.condition:
            self.active.discard(future)
            self.condition.notify_all()

    def seal(self):
        with self.condition:self.closed = True

    def drain(self, timeout):
        with self.condition:return self.condition.wait_for(lambda:not self.active, timeout)

    def close(self):
        with self.condition:
            self.closed = True
            if self.active:
                raise RuntimeFault('not_ready', 'Preserve callback resources until actual execution ends')
        self.executor.shutdown(wait=True)


class SdkToolCallbacks:
    """One P20 scope. Only its validated on_tool wrapper may call handle/start.

    No activate/bind, business journal, effect key, model request or S01 invocation.
    RuntimeFault and callback exceptions remain for P20's sdk_tool error envelope.
    """
    def __init__(self, *, tools, on_tool, pool):
        self.schemas = {}
        self.output_contracts = {}
        for tool in tools:
            if tool.name in self.schemas:raise RuntimeFault('invalid_input', 'Duplicate SDK tool name')
            try:output_schema_check(tool.input_schema)
            except RuntimeFault:
                raise RuntimeFault('invalid_input', 'SDK input schema must be bounded and self-contained') from None
            self.schemas[tool.name] = json.loads(encode(tool.input_schema))
            if tool.output_schema is not None:output_schema_check(tool.output_schema)
            self.output_contracts[tool.name] = HostToolDeclaration(
                tool.name,tool.description,self.schemas[tool.name],json.loads(encode(tool.output_schema)))
        self.on_tool, self.pool = on_tool, pool
        self.context = copy_context()
        self.condition = threading.Condition(threading.RLock())
        self.calls = {}  # Only in-process transport futures, never business/effect truth.
        self.active = None
        self.closed = False

    def start(self, call):
        if not isinstance(call, HostToolCall) or not isinstance(call.call_id, str) or not call.call_id:
            raise RuntimeFault('invalid_input', 'Use the original P20-validated tool call')
        arguments = json.loads(encode(call.arguments))
        fingerprint = hashlib.sha256(encode([call.name, arguments]).encode()).hexdigest()
        with self.condition:
            old = self.calls.get(call.call_id)
            if old:
                if old[0] != fingerprint:raise RuntimeFault('id_conflict', 'Original tool input changed')
                return old[1]
            if self.closed:raise RuntimeFault('closed', 'Original SDK callbacks have ended')
            schema = self.schemas.get(call.name)
            if schema is None:raise RuntimeFault('forbidden', 'Tool is outside the frozen definition')
            if not isinstance(arguments,dict) or next(Draft202012Validator(
                schema, format_checker=FormatChecker(), registry=Registry()).iter_errors(arguments), None):
                raise RuntimeFault('invalid_input', 'Arguments do not match the frozen SDK tool schema')
            if self.active is not None:raise RuntimeFault('busy', 'Original SDK callback is still executing')
            frozen_call = replace(call, arguments=arguments)
            future = self.pool.submit(self.context.copy().run, self._invoke, frozen_call)
            self.active = future
            self.calls[call.call_id] = (fingerprint, future)
            future.add_done_callback(self._finished)
            return future

    def _invoke(self, call):
        # No callback/channel lock spans role effects, cancellation or observer.
        value = self.on_tool(call)
        return validate_output(self.output_contracts[call.name],value)

    def _finished(self, future):
        with self.condition:
            if self.active is future:self.active = None
            self.condition.notify_all()

    def handle(self, call):
        return HostToolResult(value=self.start(call).result())

    def seal(self):
        with self.condition:self.closed = True

    def drain(self, timeout):
        with self.condition:return self.condition.wait_for(lambda:self.active is None, timeout)
