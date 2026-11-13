"""Verify the installed wheel against pip's RECORD before exposing either role."""
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys


def inspect():
    dist = metadata.distribution('ioalab-client')
    files = dist.files
    if not files: raise RuntimeError('Client installation RECORD is missing')
    for item in files:
        if not item.hash: continue
        if item.hash.mode != 'sha256': raise RuntimeError('Unsupported installation hash')
        path = Path(dist.locate_file(item))
        if path.is_symlink() or not path.is_file(): raise RuntimeError('Installed client file is missing or linked')
        actual = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).rstrip(b'=').decode()
        if actual != item.hash.value: raise RuntimeError('Installed client checksum mismatch')
    origin = json.loads(dist.read_text('direct_url.json') or '{}')
    # A Linux venv's python can be a symlink. Resolving it would select the base environment.
    return {'version': dist.version, 'python': str(Path(sys.executable).absolute()),
            'source': origin, 'skills': ['assistant', 'service']}


def prepare():
    try:
        return {'status': 'completed', 'data': inspect(), 'error': None}
    except (metadata.PackageNotFoundError, OSError, ValueError, RuntimeError) as error:
        return {'status': 'rejected', 'data': {'requestSubmitted': False},
                'error': {'code': 'not_ready', 'message': str(error), 'recovery': 'run_bootstrap'}}
