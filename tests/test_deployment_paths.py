"""Exercise the actual upgrade path helper with isolated environment files."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


def bash():
    executable=Path('D:/tool/Git/bin/bash.exe') if os.name=='nt' else Path(shutil.which('bash') or '/missing')
    if not executable.is_file():pytest.skip('Bash required for deployment checks')
    return str(executable)


def configure(path,app):
    result=subprocess.run([bash(),(ROOT/'deploy/configure_paths.sh').as_posix(),path.as_posix(),app.as_posix()],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'do-not-log' not in result.stdout+result.stderr


def test_upgrade_adds_missing_paths_and_preserves_values(tmp_path):
    path=tmp_path/'ioalab.env'
    original='IOALAB_SECRET=do-not-log\nIOALAB_BUNDLES_DIR=/operator/chosen\n'
    path.write_text(original,encoding='utf-8',newline='\n')
    app=tmp_path/'app with spaces'
    app.mkdir()
    configure(path,app)
    text=path.read_text()
    assert text.startswith(original)
    before=path.read_bytes()
    configure(path,app)
    assert path.read_bytes()==before
    script='source "$1"; printf "%s" "$IOALAB_SEED_FILE"'
    result=subprocess.run([bash(),'-c',script,'paths-test',path.as_posix()],capture_output=True,text=True,timeout=10)
    assert result.returncode==0
    assert result.stdout==app.as_posix()+'/deploy/default-seed.json'


def test_explicit_empty_values_are_not_silently_replaced(tmp_path):
    path=tmp_path/'existing.env'
    original='IOALAB_SEED_FILE=\nIOALAB_BUNDLES_DIR=\n'
    path.write_text(original,encoding='utf-8',newline='\n')
    configure(path,tmp_path)
    assert path.read_text()==original
