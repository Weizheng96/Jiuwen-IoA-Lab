"""Expose this round's authorized bytes to the same local host, never model paths."""
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from a2c.codec import digest
from ..runtime import restrict, RuntimeFault


MAX_BYTES = 16 * 1024 * 1024


def _unlinked(path):
    for item in (path, *path.parents):
        try:
            value = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
            raise RuntimeFault('forbidden', 'Business files cannot traverse links or reparse points')


def _directory(path):
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeFault('invalid_input', 'Use the original absolute SDK business workspace')
    _unlinked(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _unlinked(path)
    restrict(path, directory=True)
    return path


def artifact_directory(workspace):
    return _directory(Path(workspace) / 'artifacts')


def material_files(workspace, scope, materials):
    """Caller obtains authorized bytes for the current phase/period before each call.

    Returned paths point at complete content-addressed files on this client machine.
    Original material names remain labels and never become filesystem paths.
    """
    if not isinstance(scope, dict) or not isinstance(materials, dict):
        raise RuntimeFault('invalid_input', 'Use the original authorized material bytes')
    if not materials:
        return []
    root = _directory(Path(workspace) / 'materials' / digest(scope).removeprefix('sha256:'))
    result = []
    for name, data in materials.items():
        if not isinstance(name, str) or not name or len(name) > 8192 or not isinstance(data, bytes) or len(data) > MAX_BYTES:
            raise RuntimeFault('invalid_input', 'Use bounded original material names and actual bytes')
        checksum = hashlib.sha256(data).hexdigest()
        key = hashlib.sha256(name.encode('utf-8') + b'\0' + data).hexdigest()
        suffix = Path(name).suffix
        suffix = suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,12}', suffix) else ''
        target = root / (key + suffix)
        _unlinked(target)
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix='.material-', dir=root)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    restrict(temporary)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Publish complete bytes without replacing any existing original.
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        _unlinked(target)
        with target.open('rb') as stream:
            actual = stream.read(MAX_BYTES + 1)
        if actual != data:
            raise RuntimeFault('identity_conflict', 'Original material file changed; preserve it for inspection')
        result.append({'name': name, 'size': len(data), 'mediaType': 'application/octet-stream',
                       'contentDigest': 'sha256:' + checksum, 'path': str(target)})
    return result
