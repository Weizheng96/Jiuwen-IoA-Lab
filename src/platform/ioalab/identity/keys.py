"""Canonical public-key identity used for account bindings."""
import hashlib
import json


def thumbprint(jwk):
    core = {key:jwk[key] for key in ('crv','kty','x','y')}
    return hashlib.sha256(json.dumps(core,separators=(',',':'),sort_keys=True).encode()).hexdigest()
