"""验收器包：平台打包、按摘要存 data/packages/<digest>.zip，模板引用固定 ``ioalab://pkg/<digest>``。

三方的 ``fetch`` 只认这个 scheme；只许 ``kind == "script"``；单包 ≤ 256 KB。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from a2c.codec import digest_bytes
from a2c.errors import INVALID_OBJECT, fail
from a2c.verifier.package import build

SCHEME = "ioalab://pkg/"
MAX_PACKAGE = 256 * 1024
_ID = re.compile(r"^[a-z][a-z0-9_]*@\d+\.\d+(\.\d+)?$")


class PackageStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, digest: str) -> Path:
        return self.root / (digest.split(":", 1)[1] + ".zip")

    def fetch(self, uri: str) -> bytes:
        if not uri.startswith(SCHEME):
            fail(INVALID_OBJECT, f"平台只认 {SCHEME} 的验收器包：{uri}")
        p = self.path(uri[len(SCHEME):])
        if not p.exists():
            fail(INVALID_OBJECT, f"没有这个验收器包：{uri}")
        return p.read_bytes()

    def put(self, *, verifier_id: str, source: str, reads: list[str], module: str = "verify") -> dict:
        """打包一个 L1 脚本验收器，返回模板里用的引用 {id, uri, digest}。"""
        if not _ID.match(verifier_id):
            fail(INVALID_OBJECT, f"验收器 id 应形如 name@1.0：{verifier_id}")
        manifest = {"id": verifier_id, "kind": "script", "runtime": "cpython-3.13-wasi",
                    "entry": f"{module}:verify", "reads": list(reads)}
        blob = build({f"{module}.py": source}, manifest)
        if len(blob) > MAX_PACKAGE:
            fail(INVALID_OBJECT, "验收器包超过 256 KB")
        d = digest_bytes(blob)
        p = self.path(d)
        if not p.exists():
            p.write_bytes(blob)
        return {"id": verifier_id, "uri": SCHEME + d, "digest": d}

    def check_ref(self, ref: Mapping[str, Any]) -> None:
        if not str(ref.get("uri", "")).startswith(SCHEME):
            fail(INVALID_OBJECT, "服务定义不得自带验收器 uri")


def only_script_verifiers(template: Mapping[str, Any]) -> None:
    """模板的验收标准只许 script 一类（平台 Market(models={}) 也会拒签 skill）。"""
    for name, block in (template.get("acceptance") or {}).items():
        kinds = set((block.get("verifiers") or {}).keys())
        if kinds - {"script"}:
            fail(INVALID_OBJECT, f"{name}: 首版只支持 script 验收器，不支持 {sorted(kinds - {'script'})}")
    for m in template.get("inputs") or []:
        kinds = set((m.get("verifiers") or {}).keys())
        if kinds - {"script"}:
            fail(INVALID_OBJECT, f"输入 {m['name']}: 只支持 script 输入验收器")


__all__ = ["PackageStore", "SCHEME", "only_script_verifiers"]
