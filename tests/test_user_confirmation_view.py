"""Run the shipped owner-page script in Node VM with DOM/network doubles.

This covers deterministic draft and request races, not real-browser rendering.
The neighboring HTTP tests cover the actual Origin/CSRF and binding boundary.
"""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required for owner-page JavaScript checks')
@pytest.mark.parametrize('scenario', ['drafts', 'success', 'failure', 'unknown', 'binding', 'closed'])
def test_owner_confirmation_view(scenario):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [shutil.which('node'), str(root / 'tests/js/user_confirmation_view.cjs'),
         str(root / 'src/access/sdk/ioalab_sdk/runtime/web/user.html'), scenario],
        capture_output=True, text=True, encoding='utf-8', timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
