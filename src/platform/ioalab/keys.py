"""私钥：data/keys/<party>.json（0600），Ed25519（签合同）与 ES256（AP2 付款 / 收款）各自按名字取、首次生成。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from a2c.jws import Signer
from a2c.payment.ap2 import EcKey

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class KeyStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass

    def _path(self, name: str) -> Path:
        return self.root / (_SAFE.sub("_", name) + ".json")

    def _load(self, name: str) -> dict:
        p = self._path(name)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _save(self, name: str, d: dict) -> None:
        p = self._path(name)
        p.write_text(json.dumps(d), encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

    def ed25519(self, party_id: str, *, name: str | None = None, kid: str | None = None) -> Signer:
        name = name or party_id
        d = self._load(name)
        if "ed25519" not in d:
            key = ed25519.Ed25519PrivateKey.generate()
            d["ed25519"] = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                             serialization.NoEncryption()).hex()
            self._save(name, d)
        key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(d["ed25519"]))
        return Signer(id=party_id, key=key, kid=kid)

    def ec(self, name: str, *, kid: str | None = None) -> EcKey:
        d = self._load(name)
        if "ec" not in d:
            key = ec.generate_private_key(ec.SECP256R1())
            d["ec"] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
            self._save(name, d)
        key = serialization.load_pem_private_key(d["ec"].encode(), password=None)
        return EcKey(kid or f"{_SAFE.sub('_', name)}-ap2", key)


__all__ = ["KeyStore"]
