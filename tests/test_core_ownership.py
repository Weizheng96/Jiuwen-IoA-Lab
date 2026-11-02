"""Actual Core ownership, failure cleanup and real writer convergence."""
import asyncio
import hashlib
import os
import signal
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from ioalab.config import Settings
from ioalab.platform import Platform
from ioalab.store import DB, DataInUse, DataOwner


def settings(path):
    return Settings(data=path,autoseed=False,public_url_env='http://127.0.0.1:9998')


def files(path):
    return {str(p.relative_to(path)):(p.stat().st_size,p.stat().st_mtime_ns) if p.name=='.core.lock' else hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


def test_second_core_rejects_before_config_or_db_changes_and_db_stays_a_connection(tmp_path):
    core=Platform(settings(tmp_path))
    try:
        before=files(tmp_path)
        other=settings(tmp_path)
        other.public_url_env='http://127.0.0.1:9999'
        other.llm={'api_key':'test-only-other-key','api_base':'https://example.invalid'}
        with pytest.raises(DataInUse):Platform(other)
        assert files(tmp_path)==before
        # Opening a normal connection for original-receipt inspection is legitimate.
        connection=DB(core.db.path)
        try:assert connection.one('SELECT COUNT(*) AS n FROM users')==core.db.one('SELECT COUNT(*) AS n FROM users')
        finally:connection.close()
    finally:core.close()
    replacement=Platform(settings(tmp_path))
    replacement.close()
    replacement.close()


def test_other_process_rejects_the_same_core_data_before_any_mutation(tmp_path):
    core=Platform(settings(tmp_path))
    try:
        before=files(tmp_path)
        script='''from pathlib import Path
from ioalab.config import Settings
from ioalab.platform import Platform
from ioalab.store import DataInUse
import sys
try: Platform(Settings(data=Path(sys.argv[1]),autoseed=False,public_url_env='http://other.invalid'))
except DataInUse: print('original-core-still-owns-data')
else: raise AssertionError('Second process acquired active data')
'''
        result=subprocess.run([sys.executable,'-B','-c',script,str(tmp_path)],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
        assert result.stdout.strip()=='original-core-still-owns-data'
        assert files(tmp_path)==before
    finally:core.close()


def test_actual_os_lock_is_released_when_its_process_is_terminated(tmp_path):
    ready=tmp_path/'ready'
    directory=tmp_path/'core'
    script='''from pathlib import Path
from ioalab.store import DataOwner
import os,sys,time
owner=DataOwner(Path(sys.argv[1]))
Path(sys.argv[2]).write_text(str(os.getpid()))
time.sleep(300)
'''
    process=subprocess.Popen([sys.executable,'-B','-c',script,str(directory),str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+20
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        assert ready.exists() and process.poll() is None
        with pytest.raises(DataInUse):DataOwner(directory)
        # Windows venv python.exe may be a redirector; stop the actual lock owner.
        actual_pid=int(ready.read_text())
        os.kill(actual_pid,signal.SIGTERM)
        process.wait(timeout=10)
        with DataOwner(directory):pass
        assert (directory/'.core.lock').exists()
    finally:
        if process.poll() is None:process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize('stage',['configuration','assembly'])
def test_failed_construction_releases_the_real_owner_and_resources(tmp_path,monkeypatch,stage):
    def fail(*_):raise ValueError('injected-'+stage)
    if stage=='configuration':
        with monkeypatch.context() as patch:
            patch.setattr(Settings,'write_a2x_key',fail)
            with pytest.raises(ValueError,match='injected-configuration'):Platform(settings(tmp_path))
    else:
        from ioalab.observation import ContractProjection
        with monkeypatch.context() as patch:
            patch.setattr(ContractProjection,'rebuild',fail)
            with pytest.raises(ValueError,match='injected-assembly'):Platform(settings(tmp_path))
    core=Platform(settings(tmp_path));core.close()


def test_failed_db_initialization_closes_its_partial_connection(tmp_path,monkeypatch):
    from ioalab import store
    opened=[];connect=store.sqlite3.connect
    def track(*args,**kwargs):
        result=connect(*args,**kwargs);opened.append(result);return result
    def fail(*_):raise ValueError('injected-migration')
    with monkeypatch.context() as patch:
        patch.setattr(store.sqlite3,'connect',track);patch.setattr(DB,'_migrate',fail)
        with pytest.raises(ValueError,match='injected-migration'):Platform(settings(tmp_path))
    assert len(opened)==1
    with pytest.raises(store.sqlite3.ProgrammingError,match='closed'):opened[0].execute('SELECT 1')
    core=Platform(settings(tmp_path));core.close()


def test_failed_startup_closes_event_log_and_owner(tmp_path,monkeypatch):
    core=Platform(settings(tmp_path))
    def fail():raise ValueError('injected-startup')
    monkeypatch.setattr(core.discovery,'startup',fail)
    with pytest.raises(ValueError,match='injected-startup'):asyncio.run(core.startup())
    assert core.bus._file is None
    assert not core.started
    with DataOwner(tmp_path):pass


def test_cancelled_startup_cannot_release_while_its_real_thread_runs(tmp_path,monkeypatch):
    core=Platform(settings(tmp_path));entered=threading.Event();release=threading.Event()
    def writer():
        entered.set()
        assert release.wait(10)
        (tmp_path/'last-write').write_text('original writer finished')
    monkeypatch.setattr(core.discovery,'startup',writer)
    async def run():
        task=asyncio.create_task(core.startup())
        try:
            deadline=time.monotonic()+5
            while not entered.is_set() and time.monotonic()<deadline:await asyncio.sleep(.01)
            assert entered.is_set()
            task.cancel();await asyncio.sleep(.03)
            assert not task.done()
            with pytest.raises(DataInUse):DataOwner(tmp_path)
        finally:release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert (tmp_path/'last-write').read_text()=='original writer finished'
        with DataOwner(tmp_path):pass
    asyncio.run(run())


def test_cancelled_shutdown_observer_does_not_abandon_a_live_search_writer(tmp_path):
    core=Platform(settings(tmp_path));entered=threading.Event();release=threading.Event()
    def writer():
        entered.set();assert release.wait(10)
        (tmp_path/'last-search-write').write_text('finished')
    future=core.discovery._pool.submit(writer)
    assert entered.wait(5)
    async def run():
        observer=asyncio.create_task(core.shutdown())
        try:
            await asyncio.sleep(.03)
            assert not observer.done()
            observer.cancel()
            with pytest.raises(asyncio.CancelledError):await observer
            assert not future.done()
            with pytest.raises(DataInUse):DataOwner(tmp_path)
        finally:release.set()
        await core.shutdown()
        assert future.done() and future.exception() is None
        assert (tmp_path/'last-search-write').read_text()=='finished'
        with DataOwner(tmp_path):pass
    asyncio.run(run())


def test_web_lifespan_failure_still_closes_the_original_core(tmp_path):
    from ioalab.web.app import create_app
    core=Platform(settings(tmp_path));app=create_app(core)
    async def run():
        with pytest.raises(ValueError,match='injected-request-lifespan'):
            async with app.router.lifespan_context(app):
                assert core.started
                raise ValueError('injected-request-lifespan')
        assert not core.started
        with DataOwner(tmp_path):pass
    asyncio.run(run())


@pytest.mark.parametrize('command',[['init','--no-seed'],['serve'],['reset'],['reset','--keep-accounts'],['backup']])
def test_every_cli_write_entry_rejects_active_data_without_modification(tmp_path,monkeypatch,command):
    from ioalab.cli import main
    core=Platform(settings(tmp_path));before=files(tmp_path)
    monkeypatch.setenv('IOALAB_DATA',str(tmp_path))
    monkeypatch.setenv('IOALAB_PUBLIC_URL','http://different.invalid')
    try:
        with pytest.raises(DataInUse):main(command)
        assert files(tmp_path)==before
    finally:core.close()


def test_cli_init_returns_with_closed_data_and_backup_does_not_restore_lock_state(tmp_path,monkeypatch):
    import tarfile
    from ioalab.cli import main
    monkeypatch.setenv('IOALAB_DATA',str(tmp_path/'core'))
    monkeypatch.setenv('IOALAB_PUBLIC_URL','http://127.0.0.1:9998')
    main(['init','--no-seed'])
    with DataOwner(tmp_path/'core'):pass
    main(['backup','--out',str(tmp_path/'backups')])
    archive=next((tmp_path/'backups').glob('*.tar.gz'))
    with tarfile.open(archive) as package:
        assert 'data/public_url' in package.getnames()
        assert 'data/.core.lock' not in package.getnames()
    with DataOwner(tmp_path/'core'):pass
