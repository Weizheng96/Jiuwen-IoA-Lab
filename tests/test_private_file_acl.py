"""Private file ACL: SDK-written files belong only to the local user; an extra public grant does not survive."""
import os
from pathlib import Path
import re
import stat
import subprocess

import pytest

from ioalab_sdk.runtime.files import private_json, private_text, restrict, windows_sid

WINDOWS = os.name == 'nt'
EVERYONE = 'S-1-1-0'


def grantees(path):
    """(protected, trustee SIDs) of the actual DACL; Everyone appears as its SID or the SDDL alias WD."""
    saved = Path(str(path) + '.acl')
    target = Path(path)
    result = subprocess.run(['icacls', target.name, '/save', str(saved)], cwd=target.parent, capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0
    try:
        sddl = saved.read_bytes().decode('utf-16').splitlines()[1]
    finally:
        saved.unlink()
    dacl = sddl.split('D:', 1)[1]
    trustees = {ace.split(';')[-1] for ace in re.findall(r'\(([^)]*)\)', dacl)}
    return dacl.startswith('P'), {EVERYONE if trustee == 'WD' else trustee for trustee in trustees}


def grant_everyone(path, permission='R'):
    result = subprocess.run(['icacls', str(path), '/grant', '*' + EVERYONE + ':' + permission], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0


@pytest.mark.skipif(not WINDOWS, reason='Actual Windows SID and DACL semantics')
def test_owner_only_write_passes_and_extra_public_grant_is_removed(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    restrict(private, directory=True)
    path = private / 'config.json'
    private_json(path, {'nonsecret': 'fixture'})
    owner = windows_sid()
    assert grantees(private) == (True, {owner}) and grantees(path) == (True, {owner})
    grant_everyone(path)
    assert EVERYONE in grantees(path)[1]
    private_json(path, {'nonsecret': 'rewritten'})
    assert grantees(path) == (True, {owner}) and path.read_text(encoding='utf-8') == '{"nonsecret": "rewritten"}'
    # An inheritable public grant on the parent must not reach a newly written private file.
    grant_everyone(private, '(OI)(R)')
    assert EVERYONE in grantees(private)[1]
    fresh = private / 'fresh.json'
    private_json(fresh, {'nonsecret': 'fresh'})
    assert grantees(fresh) == (True, {owner})


@pytest.mark.skipif(WINDOWS, reason='POSIX mode bits')
def test_owner_only_modes_and_public_bits_are_removed(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    restrict(private, directory=True)
    path = private / 'note.txt'
    private_text(path, 'fixture')
    assert stat.S_IMODE(private.stat().st_mode) == 0o700 and stat.S_IMODE(path.stat().st_mode) == 0o600
    os.chmod(path, 0o644)
    private_text(path, 'rewritten')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.read_text() == 'rewritten'
