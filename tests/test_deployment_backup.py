"""Snapshot real files after the caller's writer gate; no business state restore exists."""
import json
import tarfile
import uuid

import pytest

from deploy.backup import capture,verify
from deploy.layout import Layout


def source(tmp_path):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    (layout.state/'core').mkdir();(layout.state/'agents').mkdir()
    (layout.state/'core/database.sqlite').write_bytes(b'closed database evidence')
    (layout.state/'agents/unknown-payment.json').write_text('{"outcome":"unknown"}')
    (layout.config/'credential.env').write_text('PRIVATE="do not publish"')
    (layout.private/'operation.json').write_text('{"stage":"stopped"}')
    return layout


def test_real_archive_preserves_unknown_effects_and_reuses_original_snapshot(tmp_path):
    layout=source(tmp_path);operation=uuid.uuid4().hex
    result=capture(layout,operation,'original-set',released=lambda:True)
    record,checksum=verify(result['path'])
    assert result['restored'] is False and record['phase']=='ready' and checksum==result['archiveSha256']
    with tarfile.open(result['path']+'/state.tar','r:') as archive:
        assert archive.extractfile('agents/unknown-payment.json').read()==b'{"outcome":"unknown"}'
        assert archive.extractfile('config/credential.env').read()==b'PRIVATE="do not publish"'
        assert not any('cache' in p.name or 'runtime' in p.name for p in archive.getmembers())
    (layout.state/'agents/unknown-payment.json').write_text('{"outcome":"reconciled"}')
    assert capture(layout,operation,'original-set',released=lambda:True)==result
    assert json.loads((layout.state/'agents/unknown-payment.json').read_text())['outcome']=='reconciled'


def test_writers_block_backup_before_any_archive_is_created(tmp_path):
    layout=source(tmp_path)
    with pytest.raises(RuntimeError,match='not released'):capture(layout,uuid.uuid4().hex,None,released=lambda:False)
    assert not (layout.state/'backups').exists()


def test_reappearing_writer_blocks_ready_marker(tmp_path):
    layout=source(tmp_path);operation=uuid.uuid4().hex;checks=iter([True,False])
    with pytest.raises(RuntimeError,match='reappeared'):capture(layout,operation,None,released=lambda:next(checks))
    folder=layout.state/'backups'/operation
    assert json.loads((folder/'manifest.json').read_text())['phase']=='writer_returned'
    with pytest.raises(RuntimeError,match='original backup manifest'):
        capture(layout,operation,None,released=lambda:True)


def test_crash_after_valid_archive_recovers_original_manifest_only(tmp_path):
    layout=source(tmp_path);operation=uuid.uuid4().hex
    result=capture(layout,operation,None,released=lambda:True)
    from pathlib import Path
    manifest=Path(result['path'])/'manifest.json';record=json.loads(manifest.read_text())
    record['phase']='archiving';record.pop('archiveSha256');manifest.write_text(json.dumps(record))
    assert capture(layout,operation,None,released=lambda:True)==result


def test_truncated_backup_is_retained_and_never_silently_recreated(tmp_path):
    layout=source(tmp_path);operation=uuid.uuid4().hex
    result=capture(layout,operation,None,released=lambda:True)
    from pathlib import Path
    archive=Path(result['path'])/'state.tar';archive.write_bytes(b'partial original')
    with pytest.raises(RuntimeError,match='verification failed'):capture(layout,operation,None,released=lambda:True)
    assert archive.read_bytes()==b'partial original'


def test_backup_does_not_adopt_an_unknown_directory(tmp_path):
    layout=source(tmp_path);operation=uuid.uuid4().hex
    folder=layout.state/'backups'/operation;folder.mkdir(parents=True)
    (folder/'keep.txt').write_text('operator archive')
    with pytest.raises((RuntimeError,FileNotFoundError)):capture(layout,operation,None,released=lambda:True)
    assert (folder/'keep.txt').read_text()=='operator archive'


def test_changed_source_during_archive_is_never_completed_on_resume(tmp_path,monkeypatch):
    import deploy.backup as module
    layout=source(tmp_path);operation=uuid.uuid4().hex;original=module.members;calls=[]
    def changing(selected):
        calls.append(1)
        if len(calls)==2:(layout.state/'agents/unknown-payment.json').write_text('{"new":"evidence"}')
        return original(selected)
    monkeypatch.setattr(module,'members',changing)
    with pytest.raises(RuntimeError,match='State changed'):module.capture(layout,operation,None,released=lambda:True)
    folder=layout.state/'backups'/operation
    assert json.loads((folder/'manifest.json').read_text())['phase']=='source_changed'
    with pytest.raises(RuntimeError,match='original backup manifest'):module.capture(layout,operation,None,released=lambda:True)
    assert (layout.state/'agents/unknown-payment.json').read_text()=='{"new":"evidence"}'
