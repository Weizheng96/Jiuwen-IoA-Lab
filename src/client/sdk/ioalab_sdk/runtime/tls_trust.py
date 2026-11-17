"""Origin-scoped, immutable public trust material from the installed package."""
from datetime import datetime, timezone
import hashlib
import importlib.resources
import json
from pathlib import Path, PurePosixPath
import re
import ssl
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .protocol import RuntimeFault


def origin(url):
    value = urlsplit(url)
    if (value.scheme not in ('http', 'https') or not value.hostname or value.username is not None
            or value.password is not None or value.fragment):
        raise ValueError('Invalid platform origin')
    port = value.port or {'http': 80, 'https': 443}[value.scheme]
    host = value.hostname.lower()
    return f'{value.scheme}://{("["+host+"]") if ":" in host else host}:{port}'


def resource_bytes(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or len(path.parts) != 1 or name in ('', '.', '..') or '\\' in name:
        raise ValueError('Trust resource must be a package-local file')
    target = root.joinpath(name)
    if isinstance(target, Path):
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError('Trust resource must not contain links')
    with target.open('rb') as stream:
        raw = stream.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ValueError('Trust resource is too large')
    return raw


def context(platform):
    """Return one SSL context and its non-secret, frozen policy description."""
    try:
        target = origin(platform)
        package = importlib.resources.files('ioalab_sdk.runtime')
        root = package.joinpath('trust')
        if isinstance(package, Path):
            # The environment prefix may be a verified deployment/current alias.
            # Package directories and the trust resources themselves may not be links.
            if any(item.is_symlink() for item in (package, package.parent, root)):
                raise ValueError('Trust package resources must not contain links')
            root = root.resolve(strict=True)
        raw = resource_bytes(root, 'platform-trust.json')
        policy = json.loads(raw)
        if (not isinstance(policy, dict) or set(policy) != {'format', 'origins', 'anchors'}
                or policy['format'] != 'ioalab.platform-trust/1'
                or not isinstance(policy['origins'], list) or not policy['origins']
                or not isinstance(policy['anchors'], list) or not policy['anchors']):
            raise ValueError('Invalid trust policy')
        for item in policy['origins']:
            parsed = urlsplit(item)
            if parsed.scheme != 'https' or parsed.path not in ('', '/') or parsed.query or origin(item) != item:
                raise ValueError('Trust policy requires canonical HTTPS origins')
        selected = target in policy['origins']
        result = ssl.create_default_context()
        active, inactive = [], []
        now = datetime.now(timezone.utc)
        for anchor in policy['anchors']:
            if (not isinstance(anchor, dict) or set(anchor) != {'file', 'sha256'}
                    or not isinstance(anchor['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', anchor['sha256'])):
                raise ValueError('Invalid trust anchor')
            pem = resource_bytes(root, anchor['file'])
            if pem.count(b'-----BEGIN CERTIFICATE-----') != 1 or b'PRIVATE KEY' in pem:
                raise ValueError('Trust anchor must contain exactly one public certificate')
            cert = x509.load_pem_x509_certificate(pem)
            if (cert.fingerprint(hashes.SHA256()).hex() != anchor['sha256']
                    or not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca):
                raise ValueError('CA fingerprint or purpose mismatch')
            try:
                if not cert.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:
                    raise ValueError('CA cannot sign certificates')
            except x509.ExtensionNotFound:
                pass
            if selected:
                if cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                    result.load_verify_locations(cadata=pem.decode('ascii'))
                    active.append(anchor['sha256'])
                else:
                    inactive.append(anchor['sha256'])
        return result, {'source': 'bundled' if active else 'system', 'origin': target,
                        'policyDigest': hashlib.sha256(raw).hexdigest(),
                        'caFingerprints': active, 'inactiveCaFingerprints': inactive}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, x509.ExtensionNotFound, ssl.SSLError) as error:
        raise RuntimeFault('not_ready', 'Invalid installed platform trust resources', 'restore_installation') from error
