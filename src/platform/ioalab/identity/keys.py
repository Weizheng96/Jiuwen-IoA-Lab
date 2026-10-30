"""Canonical public-key identity used for account bindings."""
import hashlib
import json
from a2c.errors import CARD_INVALID,fail
from a2c.jws import key_from_jwk
from a2c.codec import digest_bytes


def thumbprint(jwk):
    fields=('crv','kty','x','y') if jwk.get('kty')=='EC' else ('crv','kty','x')
    core = {key:jwk[key] for key in fields}
    return hashlib.sha256(json.dumps(core,separators=(',',':'),sort_keys=True).encode()).hexdigest()


def public_key(value,*,ec=False,strict=False):
    if not isinstance(value,dict) or 'd' in value or 'k' in value:
        fail(CARD_INVALID,'Only public keys are accepted')
    if strict and set(value)-({'kty','crv','x','y','kid'} if ec else {'kty','crv','x','kid'}):
        fail(CARD_INVALID,'Unsupported public key member')
    if ec:
        if value.get('kty')!='EC' or value.get('crv')!='P-256':fail(CARD_INVALID,'Expected ES256 public key')
        from a2c.codec import b64url_decode
        from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1,EllipticCurvePublicNumbers
        EllipticCurvePublicNumbers(int.from_bytes(b64url_decode(value['x']),'big'),int.from_bytes(b64url_decode(value['y']),'big'),SECP256R1()).public_key()
    else:
        key_from_jwk(value)
    return value


def fingerprints(key):
    legacy=json.dumps({name:key[name] for name in sorted(key) if name!='kid'},ensure_ascii=False,separators=(',',':'))
    return ('jwk:'+thumbprint(key),'certificate:'+digest_bytes(legacy.encode()))
