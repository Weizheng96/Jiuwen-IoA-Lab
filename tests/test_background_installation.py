"""Git-tag bootstrap tests through its public CLI and external process boundary."""
import importlib.util
import hashlib
import zipfile
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
            (Path(command[-1])/('Scripts' if sys.platform=='win32' else 'bin')).mkdir(parents=True,exist_ok=True)
            output=''
        elif command[-1]=='status':
            output=json.dumps({'status':'completed','data':{'python':command[0],'source':{
                'url':source_url,'archive_info':{'hashes':{'sha256':hashlib.sha256(b'fresh wheel').hexdigest()}}}}})
        elif 'skills' in command:
            if 'ioalab-capability-' in command[command.index('--output')+1]:
                files=[]
                for role in ['assistant','service']:
                    path=Path(command[command.index('--output')+1])/(role+'.zip')
                    with zipfile.ZipFile(path,'w') as archive:
                        archive.writestr(role+'/.ioalab-skill.json',json.dumps({'format':'ioalab.skill/2',
                            'sourceBinding':{'hostType':command[command.index('--host')+1],
                                             'hostRef':command[command.index('--host-ref')+1]}}))
                    files.append(str(path))
            else:files=['assistant.zip','provider.zip']
            output=json.dumps({'status':'completed','data':{'files':files}})
        elif 'skills-install' in command:output=json.dumps({'status':'completed','data':{'installed':True,'skills':[]}})
        elif 'wheel' in command:
            wheels=Path(command[command.index('--wheel-dir')+1]);wheels.mkdir()
            (wheels/'ioalab_client-0.0.186-py3-none-any.whl').write_bytes(b'fresh wheel');output=''
        elif 'install' in command:
            if '--force-reinstall' in command:source_url=Path(command[-1]).as_uri()
            output=''
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
    assert len(installs)==2 and installs[0][-1].endswith('.whl')
    assert all('--no-cache-dir' in call for call in installs)
    assert result['data']['checkout']['commit']==COMMIT
    assert result['data']['checkout']['selectedHost']=='codex'
    assert installs[1][-1].endswith('[codex]')
    assert not any('git+' in str(part) for command in boundary for part in command)
    assert not any('runtime.start' in c for c in boundary)
    assert bootstrap.main(args)==0
    assert len([c for c in boundary if 'install' in c])==2


def test_copied_host_python_uses_verified_versioned_base(bootstrap,boundary,tmp_path):
    folder=tmp_path/'client'
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(folder)])==0
    expected=Path(getattr(sys,'_base_executable',sys.executable))
    creates=[c for c in boundary if 'venv' in c]
    assert creates==[[str(expected),'-X','utf8','-I','-m','venv',str(folder/'env')]]


def test_adding_calling_host_reuses_environment_and_original_execution_host(bootstrap,boundary,tmp_path,capsys):
    args=['--tag','client-test','--install-dir',str(tmp_path/'client')]
    assert bootstrap.main([*args,'--host','codex'])==0
    capsys.readouterr();boundary.clear()
    assert bootstrap.main([*args,'--host','jiuwen'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['data']['checkout']['selectedHost']=='codex'
    assert not any('venv' in call or '--force-reinstall' in call or 'runtime.start' in call for call in boundary)
    assert len([c for c in boundary if 'install' in c])==1
    native=next(call for call in boundary if 'skills-install' in call)
    assert native[native.index('--host')+1]=='jiuwen'


@pytest.mark.parametrize('wrong_stage',['base','created'])
def test_wrong_python_never_reaches_pip(bootstrap,boundary,monkeypatch,tmp_path,wrong_stage,capsys):
    execute=bootstrap.subprocess.run
    probes=[]
    def wrong_python(command,**kwargs):
        result=execute(command,**kwargs)
        if command[-1]==bootstrap.PYTHON_PROBE:
            probes.append(command)
            if len(probes)==(1 if wrong_stage=='base' else 2):result.stdout=json.dumps(['cpython',[3,10]])
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
            response.stdout=json.dumps(['cpython',[3,10]])
        return response
    monkeypatch.setattr(bootstrap.subprocess,'run',wrong_python)
    assert bootstrap.main(args)==1
    assert json.loads(capsys.readouterr().out)['status']=='rejected'
    assert not any('pip' in command or 'ioalab_client' in command for command in boundary)
    assert json.loads((tmp_path/'client/ioalab-bootstrap.json').read_text())['phase']=='completed'


def test_python312_is_supported_by_bootstrap_version_gate(bootstrap,boundary,monkeypatch,tmp_path):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        result=original(command,**kwargs)
        if command[-1]==bootstrap.PYTHON_PROBE: result.stdout=json.dumps(['cpython',[3,12]])
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(tmp_path/'client')])==0


def test_fixed_develop_source_and_registration_are_preserved(bootstrap,boundary,monkeypatch,tmp_path,capsys):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        if 'merge-base' in command:return SimpleNamespace(returncode=0,stdout='')
        result=original(command,**kwargs)
        if 'get-url' in command:result.stdout=bootstrap.DEVELOP_REPOSITORY
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    assert bootstrap.main(['--host','codex','--host-ref','desktop','--commit',COMMIT,
                           '--install-dir',str(tmp_path/'client')])==0
    result=json.loads(capsys.readouterr().out)
    assert result['data']['checkout']['repository']==bootstrap.DEVELOP_REPOSITORY
    native=next(c for c in boundary if 'skills-install' in c)
    assert native[native.index('--host-ref')+1]=='desktop'


def test_failed_extra_preserves_successful_host_set_and_configuration(bootstrap,boundary,monkeypatch,tmp_path,capsys):
    folder=tmp_path/'client'
    args=['--tag','client-test','--install-dir',str(folder)]
    assert bootstrap.main([*args,'--host','codex'])==0
    capsys.readouterr()
    config=folder/'settings.local.json';config.write_bytes(b'original private config and default')
    record=(folder/'ioalab-bootstrap.json').read_bytes()
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        if '-c' in command and any('commands=json.loads' in part for part in command):
            return SimpleNamespace(returncode=1,stdout='',stderr='dependency failure')
        return original(command,**kwargs)
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    boundary.clear()
    assert bootstrap.main([*args,'--host','claude_code'])==1
    assert (folder/'ioalab-bootstrap.json').read_bytes()==record
    assert config.read_bytes()==b'original private config and default'
    assert not any('--force-reinstall' in c for c in boundary)
    assert json.loads(capsys.readouterr().out)['data']['format']=='ioalab.onboarding/1'


@pytest.mark.skipif(sys.platform!='win32',reason='Windows PowerShell policy behavior')
def test_windows_launcher_executes_under_process_remote_signed_without_global_changes(tmp_path):
    import subprocess
    result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','RemoteSigned','-File',
                           str(ROOT/'src/client/bootstrap.ps1'),'-HostType','codex','-Commit','invalid',
                           '-InstallDir',str(tmp_path/'client'),'-SourceDirectory',str(tmp_path/'source')],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==1
    assert json.loads(result.stdout)['data']['stage']=='preflight'
    assert not (tmp_path/'client').exists() and not (tmp_path/'source').exists()


def test_new_readme_reuses_original_fixed_commit_and_client_record(bootstrap,boundary,monkeypatch,tmp_path,capsys):
    folder=tmp_path/'client';args=['--install-dir',str(folder)]
    assert bootstrap.main([*args,'--tag','client-test','--host','codex'])==0
    capsys.readouterr();boundary.clear()
    original=bootstrap.subprocess.run;new_commit='b'*40
    def execute(command,**kwargs):
        if 'fetch' in command or 'merge-base' in command:
            boundary.append(command);return SimpleNamespace(returncode=0,stdout='')
        result=original(command,**kwargs)
        if 'get-url' in command:result.stdout=bootstrap.DEVELOP_REPOSITORY
        if 'rev-parse' in command and command[-1]=='HEAD':result.stdout=new_commit
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    assert bootstrap.main([*args,'--commit',new_commit,'--reuse-installed','--host','claude_code'])==0
    data=json.loads(capsys.readouterr().out)['data']
    assert data['bootstrapSource']['commit']==new_commit
    assert data['checkout']['commit']==COMMIT and data['checkout']['repository']==bootstrap.REPOSITORY
    assert any('archive' in c and COMMIT in c for c in boundary)
    assert not any('--force-reinstall' in c or 'venv' in c for c in boundary)
    record=json.loads((folder/'ioalab-bootstrap.json').read_text())
    assert record['commit']==COMMIT and record['installedHosts']==['codex','claude_code']


@pytest.mark.parametrize('rejection',['old-capability','wrong-owner','occupied','tampered-installed-source'])
def test_reuse_rejects_before_environment_mutation(bootstrap,boundary,monkeypatch,tmp_path,capsys,rejection):
    folder=tmp_path/'client';args=['--install-dir',str(folder),'--tag','client-test']
    assert bootstrap.main([*args,'--host','codex'])==0
    capsys.readouterr();boundary.clear()
    marker=folder/'ioalab-bootstrap.json'
    if rejection=='wrong-owner':
        record=json.loads(marker.read_text());record['repository']='https://untrusted.invalid/repo.git'
        marker.write_text(json.dumps(record))
    if rejection=='tampered-installed-source':
        record=json.loads(marker.read_text());record['installSource']='file:///unrelated.whl';marker.write_text(json.dumps(record))
    before=marker.read_bytes()
    if rejection=='occupied':(folder/'settings.local.json').write_bytes(b'original account and default')
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        if rejection=='old-capability' and 'skills' in command:
            return SimpleNamespace(returncode=1,stdout='',stderr='unknown --host-ref')
        if rejection=='occupied' and '-c' in command and any('commands=json.loads' in p for p in command):
            return SimpleNamespace(returncode=1,stdout='',stderr='Identity directory is already in use')
        if 'fetch' in command:return SimpleNamespace(returncode=0,stdout='')
        return original(command,**kwargs)
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    assert bootstrap.main([*args,'--reuse-installed','--host','claude_code'])==1
    packet=json.loads(capsys.readouterr().out)
    assert packet['error']['code']==('explicit_upgrade_required' if rejection=='old-capability' else
                                    'identity_conflict' if rejection=='wrong-owner' else 'installation_changed' if rejection=='tampered-installed-source' else 'installation_failed')
    assert marker.read_bytes()==before
    assert not any('install' in c or '--force-reinstall' in c for c in boundary)
    if rejection=='occupied':assert (folder/'settings.local.json').read_bytes()==b'original account and default'


@pytest.mark.parametrize('detail,code',[("ERROR: Package 'example' requires a different Python: 3.15 not in '<3.15'",'unsupported_python'),
                                      ('Connection reset by peer','installation_failed')])
def test_only_explicit_python_incompatibility_is_classified(bootstrap,boundary,monkeypatch,tmp_path,capsys,detail,code):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        result=original(command,**kwargs)
        if 'wheel' in command:result.returncode=1;result.stderr=detail
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(tmp_path/'client')])==1
    packet=json.loads(capsys.readouterr().out)
    assert packet['error']['code']==code
    assert not any('uv' in c for c in boundary)
    assert (tmp_path/'client/install.log').is_file()


@pytest.mark.parametrize('failed',[False,True])
def test_non_ascii_python_child_output_crosses_public_install_seam(bootstrap,boundary,monkeypatch,tmp_path,capsys,failed):
    import subprocess
    original=bootstrap.subprocess.run
    # Popen stays a real OS boundary even while other installer subprocesses are simulated.
    def execute(command,**kwargs):
        if 'wheel' in command:
            assert command[1:4]==['-X','utf8','-I']
            code="import sys; print('中文安装目录/用户名'); print('中文错误',file=sys.stderr); sys.exit("+str(int(failed))+")"
            with subprocess.Popen([sys.executable,*command[1:4],'-c',code],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
                out,err=process.communicate()
                assert '中文安装目录'.encode('utf-8') in out
                if failed:return SimpleNamespace(returncode=1,stdout=out.decode('utf-8'),stderr=err.decode('utf-8'))
            result=original(command,**kwargs)
            result.stdout=out.decode('utf-8');result.stderr=err.decode('utf-8')
            return result
        return original(command,**kwargs)
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    directory=tmp_path/'中文用户'/'客户端'
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(directory)])==int(failed)
    result=json.loads(capsys.readouterr().out)
    assert result['status']==('rejected' if failed else 'completed')
    assert '中文安装目录/用户名' in (directory/'install.log').read_text(encoding='utf-8')
    assert '中文错误' in (directory/'install.log').read_text(encoding='utf-8')
