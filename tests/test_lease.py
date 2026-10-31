"""OS leases serialize fresh users without an unprotected marker write."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from ioalab_sdk.lease import Lease
from conftest import wait_for


@pytest.mark.skipif(os.name != 'nt', reason='Windows byte-range lock semantics')
def test_empty_file_locked_by_another_handle_is_contention_not_a_write_error(tmp_path):
    import msvcrt
    path = tmp_path / 'empty.lock'
    with path.open('a+b') as owner:
        msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            with pytest.raises(RuntimeError, match='already in use'):
                Lease(path, timeout=0)
        finally:
            owner.seek(0)
            msvcrt.locking(owner.fileno(), msvcrt.LK_UNLCK, 1)
    with Lease(path, timeout=0):
        assert path.stat().st_size == 0


def test_fresh_lease_serializes_actual_independent_processes(tmp_path):
    code = '''
import sys,time
from pathlib import Path
from ioalab_sdk.lease import Lease
root=Path(sys.argv[1]);worker=sys.argv[2]
(root/('ready-'+worker)).touch()
until=time.monotonic()+20
while not (root/'go').exists():
    if time.monotonic()>until:raise RuntimeError('coordinator did not release this test')
    time.sleep(.01)
for _ in range(4):
    with Lease(root/'fresh.lock',timeout=10):
        state=root/'counter'
        value=int(state.read_text()) if state.exists() else 0
        time.sleep(.005)
        state.write_text(str(value+1))
print('done')
'''
    children = [subprocess.Popen([sys.executable, '-B', '-c', code, str(tmp_path), str(index)],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, 'PYTHONUTF8': '1'}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) for index in range(4)]
    try:
        wait_for(lambda: all((tmp_path / ('ready-' + str(index))).is_file() for index in range(4)))
        (tmp_path / 'go').touch()
        for child in children:
            output, errors = child.communicate(timeout=25)
            assert child.returncode == 0, output + errors
            assert output.strip() == 'done'
        assert (tmp_path / 'counter').read_text() == '16'
        assert (tmp_path / 'fresh.lock').stat().st_size == 0
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
