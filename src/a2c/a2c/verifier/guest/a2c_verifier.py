"""Guest-side helper, importable by verifier packages as ``a2c_verifier`` (runs inside the sandbox).

A verifier is a pure function ``verify(ctx) -> {"status": ..., ...}``. When it needs an
external answer it calls :func:`observe` (skill) or :func:`attest` (service): a recorded
answer is returned; otherwise the helper prints the need and the host runs it again with
the answer added (request replay, 设计/验收器.md §3).
"""
import json
import sys

_INPUT = None
_CURSOR = 0
_PENDING = []


class _Need(BaseException):
    """Raised to stop the run when an answer is missing; not an Exception on purpose."""


class Divergence(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _ask(kind, request):
    global _CURSOR
    if _INPUT["kind"] != {"observe": "skill", "attest": "service"}[kind]:
        raise RuntimeError(f"{kind}() is not available to a {_INPUT['kind']} verifier")
    need = {"kind": kind, "request": request}
    recorded = _INPUT.get("responses", [])
    if _CURSOR < len(recorded):
        rec = recorded[_CURSOR]
        if _canon(rec["need"]) != _canon(need):
            raise Divergence("request differs from the recorded one")
        _CURSOR += 1
        return rec["response"]
    _PENDING.append(need)
    raise _Need()


def observe(request):
    """Ask the caller's model (skill verifiers only); returns the model's answer."""
    return _ask("observe", request)


def attest(request):
    """Ask the declared certified service (service verifiers only); returns
    ``{"statement": ..., "signedAt": RFC 3339}`` of a verified attestation."""
    return _ask("attest", request)


class Context:
    """What the verifier may read."""

    def __init__(self, data):
        self.kind = data["kind"]
        self.period = data.get("period")
        self.target = data["target"]
        self.values = data.get("values", {})
        self.files = data.get("files", [])
        self.anchored_at = data.get("anchoredAt")

    def file(self, name):
        for f in self.files:
            if f["name"] == name:
                return f["path"]
        raise KeyError(name)

    def read(self, name, mode="rb"):
        with open(self.file(name), mode) as fh:
            return fh.read()


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def main():
    global _INPUT
    _INPUT = json.load(sys.stdin)
    module_name, _, func_name = _INPUT["entry"].partition(":")
    try:
        import importlib
        func = getattr(importlib.import_module(module_name), func_name)
        result = func(Context(_INPUT))
    except _Need:
        result = None
    except Divergence as e:
        _emit({"error": {"type": "divergence", "message": str(e)}})
        return
    except Exception as e:  # noqa: BLE001 - reported to the host as an error
        if _PENDING:
            _emit({"need": _PENDING[0]})
        else:
            _emit({"error": {"type": type(e).__name__, "message": str(e)[:500]}})
        return
    if _PENDING:
        _emit({"need": _PENDING[0]})
        return
    if not isinstance(result, dict) or result.get("status") not in ("pass", "fail", "pending"):
        _emit({"error": {"type": "bad-result", "message": "verify() must return {status: pass|fail|pending}"}})
        return
    _emit({"result": result})
