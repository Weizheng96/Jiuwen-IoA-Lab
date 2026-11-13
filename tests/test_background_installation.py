"""Git-tag bootstrap tests through its public CLI and external process boundary."""
import importlib.util
import json
import io
import tarfile
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
COMMIT='a'*40

@pytest.fixture
def bootstrap():
    spec=importlib.util.spec_from_file_location('client_bootstrap',ROOT/'src/client/bootstrap.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

@pytest.fixture
def boundary(bootstrap,monkeypatch):
    calls=[]
    source_url=None
    def execute(command,**kwargs):
        nonlocal source_url
        calls.append(command)
        if command[0]=='git':
            if 'ls-remote' in command:output=COMMIT+'\trefs/tags/client-test\n'
            elif 'get-url' in command:output=bootstrap.REPOSITORY
            elif 'rev-parse' in command:output=COMMIT
            elif 'show' in command:
                if command[-1].endswith('bootstrap.py'):output=Path(bootstrap.__file__).read_bytes()
                else:output=json.dumps({'components':[]})
            elif 'archive' in command:
                data=io.BytesIO()
                with tarfile.open(fileobj=data,mode='w') as archive:
                    content=b'[project]\nname="ioalab-client"\n'
                    info=tarfile.TarInfo('src/client/pyproject.toml');info.size=len(content)
                    archive.addfile(info,io.BytesIO(content))
                output=data.getvalue()
            else:raise AssertionError(command)
        elif command[-1]==bootstrap.PYTHON_PROBE:output=json.dumps(['cpython',[3,11]])
        elif 'venv' in command:
            (Path(command[-1])/('Scripts' if sys.platform=='win32' else 'bin')).mkdir(exist_ok=True)
            output=''
        elif command[-1]=='status':
            output=json.dumps({'status':'completed','data':{'python':command[0],'source':{
                'url':source_url,'dir_info':{}}}})
        elif 'skills' in command:output=json.dumps({'status':'completed','data':{'files':['assistant.zip','provider.zip']}})
        elif 'install' in command:
            source_url=Path(command[-1].removesuffix('[codex]')).as_uri();output=''
        else:output=''
        return SimpleNamespace(returncode=0,stdout=output)
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    return calls

def test_tag_install_pins_commit_and_reuses_client(bootstrap,boundary,tmp_path,capsys):
    args=['--host','codex','--tag','client-test','--install-dir',str(tmp_path/'client'),'--output',str(tmp_path/'skills')]
    assert bootstrap.main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['data']['connected'] is False and len(result['data']['files'])==2
    installs=[c for c in boundary if 'install' in c]
    assert len(installs)==1 and Path(installs[0][-1].removesuffix('[codex]')).parts[-2:]==('src','client')
    assert result['data']['checkout']['commit']==COMMIT
    assert result['data']['checkout']['selectedHost']=='codex'
    assert installs[0][-1].endswith('[codex]')
    assert not any('git+' in str(part) for command in boundary for part in command)
    assert not any('runtime.start' in c for c in boundary)
    assert bootstrap.main(args)==0
    assert len([c for c in boundary if 'install' in c])==1


def test_copied_host_python_uses_verified_versioned_base(bootstrap,boundary,tmp_path):
    folder=tmp_path/'client'
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(folder)])==0
    expected=Path(sys.base_prefix)/('python.exe' if sys.platform=='win32' else 'bin/python3.11')
    creates=[c for c in boundary if 'venv' in c]
    assert creates==[[str(expected),'-I','-m','venv',str(folder)]]


@pytest.mark.parametrize('wrong_stage',['base','created'])
def test_wrong_python_never_reaches_pip(bootstrap,boundary,monkeypatch,tmp_path,wrong_stage,capsys):
    execute=bootstrap.subprocess.run
    probes=[]
    def wrong_python(command,**kwargs):
        result=execute(command,**kwargs)
        if command[-1]==bootstrap.PYTHON_PROBE:
            probes.append(command)
            if len(probes)==(1 if wrong_stage=='base' else 2):result.stdout=json.dumps(['cpython',[3,12]])
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',wrong_python)
    folder=tmp_path/'client'
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(folder)])==1
    result=json.loads(capsys.readouterr().out)
    assert result['data']['connected'] is False
    assert not any('install' in command for command in boundary)
    assert json.loads((folder/'ioalab-bootstrap.json').read_text())['phase']=='installing'

@pytest.mark.parametrize('existing',['foreign','incomplete','different-tag'])
def test_existing_directory_preserved(bootstrap,boundary,tmp_path,existing,capsys):
    folder=tmp_path/'client';folder.mkdir();original=folder/'original';original.write_bytes(b'user-data')
    if existing!='foreign':
        (folder/'ioalab-bootstrap.json').write_text(json.dumps({'format':1,'repository':bootstrap.REPOSITORY,
            'tag':'other' if existing=='different-tag' else 'client-test','phase':'installing'}))
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(folder)])==1
    assert not boundary and original.read_bytes()==b'user-data'

@pytest.mark.parametrize('tag',['../main','bad tag','-option','x..y'])
def test_invalid_tag_before_download(bootstrap,boundary,tmp_path,tag):
    assert bootstrap.main(['--host','codex','--tag='+tag,'--install-dir',str(tmp_path/'client')])==1
    assert not boundary and not (tmp_path/'client').exists()

@pytest.mark.parametrize('failure',['git','pip','origin','commit','revision','export'])
def test_failed_verification_never_connected(bootstrap,boundary,monkeypatch,tmp_path,failure,capsys):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        response=original(command,**kwargs)
        if failure=='git' and command[0]=='git':response.stdout=''
        if failure=='pip' and 'install' in command:response.returncode=1
        if command[-1]=='status':
            packet=json.loads(response.stdout);origin=packet['data']['source']
            if failure=='origin':origin['url']='https://unrelated.invalid/repo.git'
            response.stdout=json.dumps(packet)
        if failure=='commit' and 'rev-parse' in command:response.stdout='b'*40
        if failure=='revision' and 'rev-parse' in command and command[-1]!='HEAD':response.stdout='b'*40
        if failure=='export' and 'skills' in command:response.stdout='{"status":"rejected"}'
        return response
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    folder=tmp_path/'client'
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(folder),'--output',str(tmp_path/'skills')])==1
    assert json.loads(capsys.readouterr().out)['data']['connected'] is False
    if folder.exists():assert json.loads((folder/'ioalab-bootstrap.json').read_text())['phase']=='installing'

def test_client_record_rejects_changed_file(tmp_path,monkeypatch):
    import base64,hashlib
    from ioalab_client import installation
    item=SimpleNamespace(hash=SimpleNamespace(mode='sha256',value=base64.urlsafe_b64encode(hashlib.sha256(b'original').digest()).rstrip(b'=').decode()))
    file=tmp_path/'client.py';file.write_bytes(b'changed')
    dist=SimpleNamespace(files=[item],locate_file=lambda _:file,version='1',read_text=lambda _:None)
    monkeypatch.setattr(installation.metadata,'distribution',lambda _:dist)
    result=installation.prepare()
    assert result['status']=='rejected' and result['data']['requestSubmitted'] is False


@pytest.mark.parametrize('tamper',['origin','script','archive-link','archive-escape'])
def test_checkout_or_archive_tampering_never_reaches_pip(bootstrap,boundary,monkeypatch,tmp_path,capsys,tamper):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        result=original(command,**kwargs)
        if tamper=='origin' and 'get-url' in command:result.stdout='https://another.invalid/repo.git'
        if tamper=='script' and 'show' in command and command[-1].endswith('bootstrap.py'):
            result.stdout=b'print("untrusted")'
        if tamper.startswith('archive-') and 'archive' in command:
            buffer=io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w') as archive:
                member=tarfile.TarInfo('../escaped' if tamper=='archive-escape' else 'src/client/link')
                if tamper=='archive-link':member.type=tarfile.SYMTYPE;member.linkname='/outside'
                archive.addfile(member)
            result.stdout=buffer.getvalue()
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(tmp_path/'client')])==1
    assert json.loads(capsys.readouterr().out)['data']['connected'] is False
    assert not any('install' in command for command in boundary)
    assert not (tmp_path/'escaped').exists()


def test_reused_client_rechecks_python_before_running_installed_code(bootstrap,boundary,monkeypatch,tmp_path,capsys):
    args=['--host','codex','--tag','client-test','--install-dir',str(tmp_path/'client')]
    assert bootstrap.main(args)==0
    capsys.readouterr()
    original=bootstrap.subprocess.run
    boundary.clear()
    def wrong_python(command,**kwargs):
        response=original(command,**kwargs)
        if command[-1]==bootstrap.PYTHON_PROBE:
            response.stdout=json.dumps(['cpython',[3,12]])
        return response
    monkeypatch.setattr(bootstrap.subprocess,'run',wrong_python)
    assert bootstrap.main(args)==1
    assert json.loads(capsys.readouterr().out)['status']=='rejected'
    assert not any('pip' in command or 'ioalab_client' in command for command in boundary)
    assert json.loads((tmp_path/'client/ioalab-bootstrap.json').read_text())['phase']=='completed'
