"""Local output safety and interruption; no network, model or live runtime."""
import os
from pathlib import Path
import re
import stat
import subprocess

import pytest

from ioalab_sdk.assistant import delivery_files as output
from ioalab_sdk.runtime.files import prepare_home,windows_sid
from ioalab_sdk.runtime.protocol import RuntimeFault


REF='sha256:'+'a'*64


@pytest.fixture
def home(tmp_path):
    path=tmp_path/'sdk';prepare_home(path);return path


def folder(home):return home/'deliveries'/REF[7:]/'1'


def test_binary_bytes_paths_reuse_permissions_and_home_restart(home,tmp_path):
    files={'原件.bin':b'\x00\xff\xfe\r\noriginal\x00'}
    result=output.save_delivery(home,REF,1,files)
    item=result['原件.bin'];path=Path(item['path'])
    assert path==folder(home)/'原件.bin' and path.read_bytes()==files['原件.bin']
    assert set(item)=={'digest','size','path','alreadyPresent'} and not item['alreadyPresent']
    assert item['size']==len(files['原件.bin'])
    original=path.stat()
    repeated=output.save_delivery(home,REF,1,files)
    assert repeated['原件.bin']['alreadyPresent'] and path.stat().st_mtime_ns==original.st_mtime_ns
    prepare_home(home)  # Its own delivery directory must survive normal startup.
    for index,target in enumerate((home,folder(home),path)):
        if os.name!='nt':assert stat.S_IMODE(target.stat().st_mode)==(0o700 if target.is_dir() else 0o600)
        else:
            acl=tmp_path/f'acl-{index}.txt'
            process=subprocess.run(['icacls',str(target),'/save',str(acl)],capture_output=True,timeout=10,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            assert process.returncode==0
            assert set(re.findall(r'\(A;[^)]*;;;([^)]+)\)',acl.read_text(encoding='utf-16-le')))=={windows_sid()}


@pytest.mark.parametrize('name',['','.', '..','../outside','a/b','a\\b','/root','C:settings','file:stream',
                                'CON','nul.txt','LPT1.log','COM¹.txt','CONIN$','CONOUT$',
                                'bad\x00name','bad\nname','bad\x85name','a?b','tail.','tail '])
def test_unsafe_protocol_names_fail_before_creating_output(home,name):
    with pytest.raises(ValueError,match='filename'):output.save_delivery(home,REF,1,{name:b'original'})
    assert not (home/'deliveries').exists()


def test_all_existing_targets_preflight_before_first_output(home):
    output.save_delivery(home,REF,1,{'second':b'keep'})
    with pytest.raises(ValueError,match='differs'):
        output.save_delivery(home,REF,1,{'first':b'new','second':b'changed'})
    assert not (folder(home)/'first').exists() and (folder(home)/'second').read_bytes()==b'keep'


def test_interruption_retains_completed_file_and_cache_can_fill_missing(home,monkeypatch):
    original=output._Directory.commit
    def interrupted(self,name,data):
        if name=='second':raise OSError('simulated interruption')
        return original(self,name,data)
    with monkeypatch.context() as patch:
        patch.setattr(output._Directory,'commit',interrupted)
        with pytest.raises(OSError):output.save_delivery(home,REF,1,{'first':b'a','second':b'b'})
    assert (folder(home)/'first').read_bytes()==b'a' and not (folder(home)/'second').exists()
    result=output.save_delivery(home,REF,1,{'first':b'a','second':b'b'})
    assert result['first']['alreadyPresent'] and not result['second']['alreadyPresent']
    assert not list(folder(home).glob('.delivery-*.tmp'))


@pytest.mark.parametrize('same',[True,False])
def test_target_appearing_during_commit_never_overwrites(home,monkeypatch,same):
    original=output._Directory.commit
    def competing(self,name,data):
        (self.path/name).write_bytes(data if same else b'other owner data')
        return original(self,name,data)
    monkeypatch.setattr(output._Directory,'commit',competing)
    if same:assert output.save_delivery(home,REF,1,{'file':b'original'})['file']['alreadyPresent']
    else:
        with pytest.raises(ValueError,match='differs'):output.save_delivery(home,REF,1,{'file':b'original'})
        assert (folder(home)/'file').read_bytes()==b'other owner data'


def test_multilink_target_never_read_or_overwritten(home,tmp_path):
    output.save_delivery(home,REF,1,{'placeholder':b'ok'})
    outside=tmp_path/'settings-secret';outside.write_bytes(b'do not read or change')
    os.link(outside,folder(home)/'file')
    with pytest.raises(ValueError,match='link'):output.save_delivery(home,REF,1,{'file':b'new'})
    assert outside.read_bytes()==b'do not read or change'


@pytest.mark.parametrize('target',['directory','file'])
def test_symbolic_or_junction_target_is_not_followed(home,tmp_path,target):
    outside=tmp_path/'outside';outside.mkdir()
    sentinel=outside/'file';sentinel.write_bytes(b'preserve')
    if target=='directory':link=home/'deliveries';destination=outside
    else:
        output.save_delivery(home,REF,1,{'placeholder':b'ok'})
        link=folder(home)/'file';destination=sentinel
    try:os.symlink(destination,link,target_is_directory=target=='directory')
    except OSError:
        if os.name!='nt':raise
        # A real junction exercises the same reparse-point rejection even on
        # Windows accounts without the symbolic-link creation privilege.
        process=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True,timeout=10,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert process.returncode==0,process.stderr
    with pytest.raises((ValueError,OSError)):output.save_delivery(home,REF,1,{'file':b'new'})
    assert sentinel.read_bytes()==b'preserve' and list(outside.iterdir())==[sentinel]


@pytest.mark.skipif(os.name!='nt',reason='Windows names are case insensitive')
def test_windows_case_collision_rejected_for_manifest_and_existing_names(home):
    with pytest.raises(ValueError,match='collide'):output.save_delivery(home,REF,1,{'A':b'a','a':b'a'})
    output.save_delivery(home,REF,1,{'A':b'a'})
    with pytest.raises(ValueError,match='collide'):output.save_delivery(home,REF,1,{'a':b'a'})


def test_private_permission_failure_writes_no_delivery_bytes(home,monkeypatch):
    if os.name=='nt':
        monkeypatch.setattr(output,'restrict',lambda *_args,**_kwargs:(_ for _ in ()).throw(RuntimeFault('not_ready','private permission failed')))
    else:monkeypatch.setattr(output.os,'fchmod',lambda *_args:(_ for _ in ()).throw(PermissionError('private permission failed')))
    with pytest.raises((RuntimeFault,PermissionError)):output.save_delivery(home,REF,1,{'file':b'new'})
    assert not (folder(home)/'file').exists()


def test_exported_directory_allows_real_resident_restart(platform,tmp_path):
    from test_resident_runtime import configuration
    from ioalab_sdk.runtime.config import RuntimeConfig
    from ioalab_sdk.runtime.resident import ResidentRuntime
    config,_=configuration(platform,tmp_path,service=False)
    first=ResidentRuntime(RuntimeConfig.load(config)).start()
    try:
        actor=first.identities[0].agent_id
        saved=output.save_delivery(first.config.home,REF,1,{'file':b'original'})
    finally:first.close()
    second=ResidentRuntime(RuntimeConfig.load(config)).start()
    try:
        assert second.identities[0].agent_id==actor
        assert Path(saved['file']['path']).read_bytes()==b'original'
    finally:second.close()


@pytest.mark.skipif(os.name=='nt',reason='Windows directory handles deny rename while exporting')
def test_renamed_directory_does_not_return_a_false_success_path(home,monkeypatch):
    original=output._Directory.commit
    def replaced(self,name,data):
        result=original(self,name,data)
        self.path.rename(self.path.with_name('moved'))
        self.path.mkdir()
        return result
    monkeypatch.setattr(output._Directory,'commit',replaced)
    with pytest.raises(ValueError,match='directory changed'):output.save_delivery(home,REF,1,{'file':b'original'})
