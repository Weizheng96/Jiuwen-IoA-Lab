"""Git-tag bootstrap tests through its public CLI and external process boundary."""
import importlib.util
import json
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
    def execute(command,**kwargs):
        calls.append(command)
        if command[0]=='git':output=COMMIT+'\trefs/tags/client-test\n'
        elif command[-1]==bootstrap.PYTHON_PROBE:output=json.dumps(['cpython',[3,11]])
        elif 'venv' in command:
            (Path(command[-1])/('Scripts' if sys.platform=='win32' else 'bin')).mkdir(exist_ok=True)
            output=''
        elif command[-1]=='status':
            output=json.dumps({'status':'completed','data':{'python':command[0],'source':{
                'url':bootstrap.REPOSITORY,'subdirectory':'src/client',
                'vcs_info':{'vcs':'git','commit_id':COMMIT,'requested_revision':COMMIT}}}})
        elif 'skills' in command:output=json.dumps({'status':'completed','data':{'files':['assistant.zip','provider.zip']}})
        else:output=''
        return SimpleNamespace(returncode=0,stdout=output)
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    return calls

def test_tag_install_pins_commit_and_reuses_client(bootstrap,boundary,tmp_path,capsys):
    args=['--tag','client-test','--install-dir',str(tmp_path/'client'),'--output',str(tmp_path/'skills')]
    assert bootstrap.main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['data']['connected'] is False and len(result['data']['files'])==2
    installs=[c for c in boundary if 'install' in c]
    assert len(installs)==1 and installs[0][-1]=='ioalab-client @ git+https://github.com/ZhangRan-Zora/Jiuwen-IoA-Lab.git@'+COMMIT+'#subdirectory=src/client'
    assert [c for c in boundary if c[0]=='git'][0][3]=='https://github.com/ZhangRan-Zora/Jiuwen-IoA-Lab.git'
    assert not any('runtime.start' in c for c in boundary)
    assert bootstrap.main(args)==0
    assert len([c for c in boundary if 'install' in c])==1


def test_copied_host_python_uses_verified_versioned_base(bootstrap,boundary,tmp_path):
    folder=tmp_path/'client'
    assert bootstrap.main(['--tag','client-test','--install-dir',str(folder)])==0
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
    assert bootstrap.main(['--tag','client-test','--install-dir',str(folder)])==1
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
    assert bootstrap.main(['--tag','client-test','--install-dir',str(folder)])==1
    assert not boundary and original.read_bytes()==b'user-data'

@pytest.mark.parametrize('tag',['../main','bad tag','-option','x..y'])
def test_invalid_tag_before_download(bootstrap,boundary,tmp_path,tag):
    assert bootstrap.main(['--tag='+tag,'--install-dir',str(tmp_path/'client')])==1
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
            if failure=='commit':origin['vcs_info']['commit_id']='b'*40
            if failure=='revision':origin['vcs_info']['requested_revision']='main'
            response.stdout=json.dumps(packet)
        if failure=='export' and 'skills' in command:response.stdout='{"status":"rejected"}'
        return response
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    folder=tmp_path/'client'
    assert bootstrap.main(['--tag','client-test','--install-dir',str(folder),'--output',str(tmp_path/'skills')])==1
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
