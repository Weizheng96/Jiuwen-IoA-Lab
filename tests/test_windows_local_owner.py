"""Actual Windows ACL acceptance/refusal; no administrator or model calls."""
import os
import subprocess

import pytest

from ioalab_admin_assistant.local_owner import owned
from ioalab_sdk.runtime.files import private_json, restrict
from ioalab_sdk.runtime.protocol import RuntimeFault


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows SID/DACL semantics')
def test_private_owner_accepts_and_extra_public_grant_is_rejected(tmp_path):
    private = tmp_path / 'private'; private.mkdir()
    restrict(private, directory=True)
    path = private / 'config.json'; private_json(path, {'nonsecret': 'fixture'})
    owned(private); owned(path)
    result = subprocess.run(['icacls', str(path), '/grant', '*S-1-1-0:R'], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0
    with pytest.raises(RuntimeFault, match='only to its local user'):
        owned(path)
    assert path.read_text().strip() == '{"nonsecret": "fixture"}'
    result = subprocess.run(['icacls', str(path), '/remove:g', '*S-1-1-0'], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0
    owned(path)
