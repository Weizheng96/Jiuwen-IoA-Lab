"""The verifier sandbox: a pinned CPython WASI build under wasmtime (design: 设计/验收器.md §2).

The guest sees only read-only directories it is given, stdin and stdout. The realtime clock
is fixed, the monotonic clock is a counter and ``random_get`` is a hash stream seeded by the
input, so the same input gives the same output. CPU is bounded by fuel, memory by a store
limit, output by a byte cap.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import wasmtime as wt

RUNTIME_DIR = Path(__file__).parent / "runtime"
FIXED_REALTIME_NS = 946684800 * 1_000_000_000      # 2000-01-01T00:00:00Z
DEFAULT_FUEL = 60_000_000_000
DEFAULT_MEMORY = 512 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
MAX_RANDOM = 64 * 1024
ENOSYS = 52
EINVAL = 28


class SandboxError(RuntimeError):
    pass


def runtimes() -> dict:
    return json.loads((RUNTIME_DIR / "runtimes.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_dir() -> Path:
    env = os.environ.get("A2C_CACHE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "a2c"


def install(runtime_id: str) -> Path:
    """Unpack the pinned archive into the cache (once) and return its directory."""
    spec = runtimes().get(runtime_id)
    if spec is None:
        raise SandboxError(f"unknown runtime {runtime_id!r}")
    target = cache_dir() / "runtimes" / spec["archiveSha256"]
    wasm = target / spec["wasm"]
    if not wasm.exists():
        archive = RUNTIME_DIR / spec["archive"]
        if _sha256(archive) != spec["archiveSha256"]:
            raise SandboxError("runtime archive digest mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=target.parent))
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if n.startswith("/") or ".." in n.split("/"):
                    raise SandboxError(f"unsafe path in runtime archive: {n}")
            z.extractall(tmp)
        try:
            tmp.rename(target)
        except OSError:            # another process won the race
            shutil.rmtree(tmp, ignore_errors=True)
    if _sha256(wasm) != spec["wasmSha256"]:
        raise SandboxError("python.wasm digest mismatch")
    return target


@dataclass
class RunResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    trap: str | None
    fuel_used: int


class Sandbox:
    """One compiled runtime; :meth:`run` instantiates a fresh store per call."""

    _lock = threading.Lock()
    _cache: dict[str, "Sandbox"] = {}

    def __init__(self, runtime_id: str = "cpython-3.13-wasi") -> None:
        self.runtime_id = runtime_id
        self.spec = runtimes()[runtime_id]
        self.root = install(runtime_id)
        cfg = wt.Config()
        cfg.consume_fuel = True
        cfg.cache = True
        self.engine = wt.Engine(cfg)
        self.module = wt.Module.from_file(self.engine, str(self.root / self.spec["wasm"]))

    @classmethod
    def get(cls, runtime_id: str = "cpython-3.13-wasi") -> "Sandbox":
        with cls._lock:
            if runtime_id not in cls._cache:
                cls._cache[runtime_id] = cls(runtime_id)
            return cls._cache[runtime_id]

    @property
    def wasm_sha256(self) -> str:
        return self.spec["wasmSha256"]

    def run(self, argv: list[str], stdin: bytes, mounts: Mapping[str, Path], *, fuel: int = DEFAULT_FUEL,
            memory: int = DEFAULT_MEMORY) -> RunResult:
        store = wt.Store(self.engine)
        store.set_fuel(fuel)
        store.set_limits(memory_size=memory)
        linker = wt.Linker(self.engine)
        linker.allow_shadowing = True
        linker.define_wasi()
        state = {"mono": 0, "rand": 0}
        seed = hashlib.sha256(stdin).digest()
        i32, i64 = wt.ValType.i32(), wt.ValType.i64()

        def clock_time_get(caller, clock_id, _precision, out_ptr):
            if clock_id == 0:
                ns = FIXED_REALTIME_NS
            else:
                state["mono"] += 1_000_000
                ns = state["mono"]
            caller["memory"].write(caller, ns.to_bytes(8, "little"), out_ptr)
            return 0

        def clock_res_get(caller, _clock_id, out_ptr):
            caller["memory"].write(caller, (1_000_000).to_bytes(8, "little"), out_ptr)
            return 0

        def random_get(caller, buf, length):
            if length > MAX_RANDOM:
                return EINVAL
            out = bytearray()
            while len(out) < length:
                state["rand"] += 1
                out += hashlib.sha256(seed + state["rand"].to_bytes(8, "little")).digest()
            caller["memory"].write(caller, bytes(out[:length]), buf)
            return 0

        def poll_oneoff(_caller, _in, _out, _n, _nevents):
            return ENOSYS

        ns = "wasi_snapshot_preview1"
        linker.define_func(ns, "clock_time_get", wt.FuncType([i32, i64, i32], [i32]), clock_time_get,
                           access_caller=True)
        linker.define_func(ns, "clock_res_get", wt.FuncType([i32, i32], [i32]), clock_res_get, access_caller=True)
        linker.define_func(ns, "random_get", wt.FuncType([i32, i32], [i32]), random_get, access_caller=True)
        linker.define_func(ns, "poll_oneoff", wt.FuncType([i32, i32, i32, i32], [i32]), poll_oneoff,
                           access_caller=True)

        work = Path(tempfile.mkdtemp(prefix="a2c-run-"))
        try:
            (work / "stdin").write_bytes(stdin)
            wasi = wt.WasiConfig()
            wasi.argv = argv
            wasi.stdin_file = str(work / "stdin")
            wasi.stdout_file = str(work / "stdout")
            wasi.stderr_file = str(work / "stderr")
            wasi.preopen_dir(str(self.root / self.spec["stdlib"]), "/lib", fs_mutable=False)
            for guest, host in mounts.items():
                wasi.preopen_dir(str(host), guest, fs_mutable=False)
            store.set_wasi(wasi)
            instance = linker.instantiate(store, self.module)
            exit_code, trap = 0, None
            try:
                instance.exports(store)["_start"](store)
            except wt.ExitTrap as e:
                exit_code = e.code
            except wt.Trap as e:
                trap = str(e).splitlines()[0] if str(e) else "trap"
            except wt.WasmtimeError as e:
                trap = str(e).splitlines()[0] if str(e) else "wasmtime error"
            used = fuel - store.get_fuel()
            def head(name: str, limit: int) -> tuple[bytes, bool]:
                f = work / name
                if not f.exists():
                    return b"", False
                with f.open("rb") as fh:
                    data = fh.read(limit + 1)
                return data[:limit], len(data) > limit
            out, over = head("stdout", MAX_OUTPUT)
            err, _ = head("stderr", 4096)
            if over:
                trap, out = "output limit exceeded", b""
            return RunResult(out, err, exit_code, trap, used)
        finally:
            shutil.rmtree(work, ignore_errors=True)


__all__ = ["Sandbox", "SandboxError", "RunResult", "runtimes", "install", "cache_dir", "DEFAULT_FUEL"]
