"""File primitives shared by the deployment modules; no business decisions."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid


def linked(path):
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def path_without_links(value):
    path = Path(value).expanduser().absolute()
    if any(part == '..' for part in path.parts):
        raise ValueError('Deployment paths must not contain parent traversal')
    for part in (*reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            if linked(part):
                raise ValueError('Deployment paths must not contain links')
    return path


def regular_file(path):
    path = Path(path)
    if path.absolute() != path.resolve() or (path.exists() and not path.is_file()):
        raise ValueError('Use an owned regular deployment file without links')
    return path


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    """Atomically replace one private (0600) JSON record."""
    path = Path(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def writer(path):
    if path.exists() and linked(path):
        raise ValueError('Installation lock cannot be a link')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'0')
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another installer holds this environment writer lock; inspect that original attempt') from None
        try:
            yield
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == 'nt':
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
