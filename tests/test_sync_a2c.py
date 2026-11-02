"""Committed source provenance, including Windows checkout bytes and dirty input."""
import importlib.util
from pathlib import Path
import subprocess
import zipfile

import pytest

spec=importlib.util.spec_from_file_location('sync_a2c',Path(__file__).parents[1]/'tools/sync_a2c.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)


def git(repo,*args):
    return subprocess.run(['git','-c','core.autocrlf=false','-c','commit.gpgSign=false',*args],
        cwd=repo,check=True,capture_output=True,text=True,encoding='utf-8').stdout


def project(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();git(repo,'init')
    (repo/'.gitattributes').write_text('*.py text eol=lf\n',encoding='utf-8')
    (repo/'设计').mkdir();(repo/'设计/VERSION').write_text('v0.0.21\n',encoding='utf-8')
    src=repo/'src';(src/'a2c').mkdir(parents=True)
    (src/'VERSION').write_text('v0.0.21\n',encoding='utf-8');(src/'pyproject.toml').write_text('[project]\nname="a2c"\n',encoding='utf-8')
    (src/'a2c/__init__.py').write_bytes("name='九问'\r\n".encode())
    git(repo,'add','.');git(repo,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','source')
    return repo,src


def test_snapshot_uses_committed_lf_not_windows_checkout_and_keeps_commit(tmp_path):
    repo,src=project(tmp_path);target=tmp_path/'snapshot'
    record=sync.snapshot(src,target)
    assert record['commit']==git(repo,'rev-parse','HEAD').strip()
    assert record['design_version']=='v0.0.21'
    assert b'\r\n' in (src/'a2c/__init__.py').read_bytes()
    assert (target/'a2c/__init__.py').read_bytes()=="name='九问'\n".encode()


@pytest.mark.parametrize('kind',['modified','untracked','staged'])
def test_dirty_source_cannot_be_misattributed_to_a_commit(tmp_path,kind):
    repo,src=project(tmp_path)
    (src/('a2c/new.py' if kind=='untracked' else 'a2c/__init__.py')).write_text('changed=True\n',encoding='utf-8')
    if kind=='staged':git(repo,'add','src')
    with pytest.raises(ValueError,match='Commit the A2C source'):sync.snapshot(src,tmp_path/'snapshot')


def test_wheel_with_a_foreign_module_is_rejected(tmp_path):
    _,src=project(tmp_path);record=sync.snapshot(src,tmp_path/'snapshot');wheel=tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel,'w') as archive:
        archive.writestr('a2c/__init__.py',"name='九问'\n")
        archive.writestr('a2c/untracked.py','unexpected=True')
        archive.writestr('a2c-0.0.21.dist-info/METADATA','Name: a2c\nVersion: 0.0.21\n')
    with pytest.raises(ValueError,match='Built modules differ'):sync.verify_wheel(wheel,record)
