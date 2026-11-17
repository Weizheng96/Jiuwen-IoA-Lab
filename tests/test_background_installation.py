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
    objects={}
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
            elif 'ls-tree' in command:
                # One committed blob per requested installer path; trees are local in a partial clone.
                names=[name if name.endswith(('.json','.whl')) else name+'/pyproject.toml' for name in command[command.index('--')+1:]]
                for name in names:objects[hashlib.sha1(name.encode()).hexdigest()]=('fixed '+name).encode()
                output=''.join('100644 blob '+hashlib.sha1(name.encode()).hexdigest()+'\t'+name+'\0' for name in names)
            elif 'cat-file' in command:
                output=b''.join(oid+b' blob '+str(len(objects[oid.decode()])).encode()+b'\n'+objects[oid.decode()]+b'\n'
                                for oid in kwargs['input'].split())
            elif 'config' in command or 'sparse-checkout' in command:output=''
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
        elif 'skills-install' in command:output=json.dumps({'status':'completed','data':{'installed':True,
            'skills':[{'package':'assistant.zip'},{'package':'provider.zip'}]}})
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
        if failure=='revision' and 'ls-remote' in command:response.stdout='b'*40+'\trefs/tags/client-test\n'
        if failure=='export' and 'skills-install' in command:response.stdout='{"status":"rejected"}'
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


@pytest.mark.parametrize('tamper',['origin','script','tree-link','tree-escape','object-missing','path-missing'])
def test_checkout_or_source_tampering_never_reaches_pip(bootstrap,boundary,monkeypatch,tmp_path,capsys,tamper):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        result=original(command,**kwargs)
        if tamper=='origin' and 'get-url' in command:result.stdout='https://another.invalid/repo.git'
        if tamper=='script' and 'show' in command and command[-1].endswith('bootstrap.py'):
            result.stdout=b'print("untrusted")'
        if tamper=='tree-link' and 'ls-tree' in command:result.stdout='120000 blob '+'1'*40+'\tsrc/client/link\0'+result.stdout
        if tamper=='tree-escape' and 'ls-tree' in command:result.stdout='100644 blob '+'1'*40+'\t../escaped\0'+result.stdout
        if tamper=='path-missing' and 'ls-tree' in command:result.stdout=result.stdout.split('\0',1)[1]
        if tamper=='object-missing' and 'cat-file' in command:result.stdout=kwargs['input'].split()[0]+b' missing\n'
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
    assert bootstrap.main(['--host','codex','--host-ref','desktop','--commit',COMMIT,'--channel','develop',
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
                           '-InstallDir',str(tmp_path/'client')],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==1
    assert json.loads(result.stdout)['data']['stage']=='preflight'
    assert not (tmp_path/'client').exists()


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
    assert bootstrap.main([*args,'--commit',new_commit,'--channel','develop','--reuse-installed','--host','claude_code'])==0
    data=json.loads(capsys.readouterr().out)['data']
    assert data['bootstrapSource']['commit']==new_commit
    assert data['checkout']['commit']==COMMIT and data['checkout']['repository']==bootstrap.REPOSITORY
    assert any('ls-tree' in c and COMMIT in c for c in boundary)
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



NEW='b'*40


def test_gitcode_main_commit_is_formal_source_with_channel_and_design_tag(bootstrap,boundary,monkeypatch,tmp_path,capsys):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        if 'merge-base' in command:boundary.append(command);return SimpleNamespace(returncode=0,stdout='')
        result=original(command,**kwargs)
        # The user's one-sentence clone URL carries no .git suffix.
        if 'get-url' in command:result.stdout='https://gitcode.com/WeiZheng96/jiuwen-IoA-lab\n'
        if 'ls-remote' in command and 'refs/tags/design-v*' in command:
            result.stdout='f'*40+'\trefs/tags/design-v0.0.201\n'+COMMIT+'\trefs/tags/design-v0.0.201^{}\n'+'c'*40+'\trefs/tags/design-v0.0.200\n'
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    folder=tmp_path/'client'
    assert bootstrap.main(['--host','codex','--commit',COMMIT,'--channel','main','--install-dir',str(folder)])==0
    data=json.loads(capsys.readouterr().out)['data']
    assert data['resolvedCommit']==COMMIT
    assert data['sourceClone']['removable'] and data['sourceClone']['path']==str(ROOT)
    assert data['bootstrapSource']=={'repository':bootstrap.REPOSITORY,'revision':COMMIT,'channel':'main','commit':COMMIT,'designTag':'design-v0.0.201'}
    assert next(c for c in boundary if 'merge-base' in c)[-1]=='refs/remotes/origin/main'
    record=json.loads((folder/'ioalab-bootstrap.json').read_text())
    assert record['channel']=='main' and record['designTag']=='design-v0.0.201' and record['phase']=='completed'


@pytest.mark.parametrize('case',['not-ancestor','channel-origin-mismatch','short-commit'])
def test_channel_source_outside_its_official_lineage_never_installs(bootstrap,boundary,monkeypatch,tmp_path,capsys,case):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        if 'merge-base' in command:return SimpleNamespace(returncode=1 if case=='not-ancestor' else 0,stdout='')
        return original(command,**kwargs)
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    channel='develop' if case=='channel-origin-mismatch' else 'main'
    commit='abc1234' if case=='short-commit' else COMMIT
    assert bootstrap.main(['--host','codex','--commit',commit,'--channel',channel,'--install-dir',str(tmp_path/'client')])==1
    assert json.loads(capsys.readouterr().out)['data']['format']=='ioalab.onboarding/1'
    assert not any('install' in c or 'venv' in c for c in boundary) and not (tmp_path/'client').exists()


@pytest.mark.parametrize('argv',[['--commit',COMMIT],['--tag','client-test','--channel','main'],
                                 ['--tag','client-test','--upgrade','--reuse-installed']])
def test_source_and_lifecycle_options_are_explicit(bootstrap,boundary,tmp_path,argv):
    with pytest.raises(SystemExit):bootstrap.main(['--host','codex','--install-dir',str(tmp_path/'client'),*argv])
    assert not boundary and not (tmp_path/'client').exists()


def test_existing_client_with_other_source_requires_explicit_upgrade(bootstrap,boundary,tmp_path,capsys):
    folder=tmp_path/'client'
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(folder)])==0
    capsys.readouterr();boundary.clear()
    before=(folder/'ioalab-bootstrap.json').read_bytes()
    assert bootstrap.main(['--host','codex','--tag','client-next','--install-dir',str(folder)])==1
    assert json.loads(capsys.readouterr().out)['error']['code']=='explicit_upgrade_required'
    assert not boundary and (folder/'ioalab-bootstrap.json').read_bytes()==before


@pytest.mark.parametrize('state',['clean','config','data'])
def test_interrupted_install_resumes_in_place_only_without_configuration_or_data(bootstrap,boundary,monkeypatch,tmp_path,capsys,state):
    original=bootstrap.subprocess.run;failing=[True]
    def execute(command,**kwargs):
        if failing[0] and 'wheel' in command:return SimpleNamespace(returncode=1,stdout='',stderr='Connection reset by peer')
        return original(command,**kwargs)
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    folder=tmp_path/'client';marker=folder/'ioalab-bootstrap.json'
    args=['--host','codex','--tag','client-test','--install-dir',str(folder)]
    assert bootstrap.main(args)==1
    capsys.readouterr();failing[0]=False
    assert json.loads(marker.read_text())['phase']=='installing'
    (folder/'env/interrupted').write_bytes(b'partial environment')
    if state=='config':(folder/'settings.local.json').write_bytes(b'original account')
    if state=='data':(folder/'settings.local.data').mkdir()
    before=marker.read_bytes()
    code=bootstrap.main(args)
    packet=json.loads(capsys.readouterr().out)
    if state=='clean':
        assert code==0 and json.loads(marker.read_text())['phase']=='completed'
        kept=list(folder.glob('env.prev-*'))
        assert len(kept)==1 and (kept[0]/'interrupted').read_bytes()==b'partial environment'
    else:
        assert code==1 and 'incomplete' in packet['error']['message']
        assert marker.read_bytes()==before and (folder/'env/interrupted').is_file()
        assert not list(folder.glob('env.prev-*'))


@pytest.fixture
def installed(bootstrap,boundary,monkeypatch,tmp_path,capsys):
    """An installed client with an account; the original SDK answers stop and data-directory probes."""
    folder=tmp_path/'client'
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(folder)])==0
    capsys.readouterr();boundary.clear()
    config=folder/'settings.local.json';config.write_bytes(b'original account, default and business references')
    (folder/'env/original-program').write_bytes(b'original program')
    state=SimpleNamespace(folder=folder,config=config,home=tmp_path/'data',catalog=True,fail=None,
                          marker=folder/'ioalab-bootstrap.json')
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        text=' '.join(map(str,command))
        if 'CATALOG' in text:boundary.append(command);return SimpleNamespace(returncode=0 if state.catalog else 1,stdout='',stderr='')
        if 'RuntimeConfig.load' in text:boundary.append(command);return SimpleNamespace(returncode=0,stdout=json.dumps(str(state.home)))
        if 'runtime.stop' in command:boundary.append(command);return SimpleNamespace(returncode=1,stdout='',stderr='not running')
        if 'merge-base' in command:return SimpleNamespace(returncode=0,stdout='')
        if state.fail and state.fail in command:return SimpleNamespace(returncode=1,stdout='',stderr='Connection reset by peer')
        result=original(command,**kwargs)
        if 'rev-parse' in command and command[-1]=='HEAD':result.stdout=NEW
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    return state

UPGRADE=['--host','codex','--commit',NEW,'--channel','main','--upgrade']


def test_explicit_upgrade_replaces_only_stopped_program_and_keeps_account(bootstrap,boundary,installed,capsys):
    assert bootstrap.main([*UPGRADE,'--install-dir',str(installed.folder)])==0
    data=json.loads(capsys.readouterr().out)['data']
    stop=next(c for c in boundary if 'runtime.stop' in c)
    assert stop[stop.index('--config')+1]==str(installed.config)
    assert installed.config.read_bytes()==b'original account, default and business references'
    record=json.loads(installed.marker.read_text())
    assert record['commit']==NEW and record['channel']=='main' and record['upgradedFrom']==COMMIT and record['phase']=='completed'
    previous=installed.folder/record['previousEnvironment']
    assert (previous/'original-program').read_bytes()==b'original program' and not (installed.folder/'env/original-program').exists()
    assert data['upgrade']['entryBinding']=='migration_pending' and data['upgrade']['from']==COMMIT
    assert data['resolvedCommit']==NEW and len(data['files'])==2
    assert any('skills-install' in c for c in boundary)


@pytest.mark.parametrize('rejection',['writer-active','no-safe-stop','install-fails'])
def test_upgrade_without_proven_stop_or_with_failed_install_keeps_original(bootstrap,boundary,installed,monkeypatch,capsys,rejection):
    before=installed.marker.read_bytes()
    holder=None
    if rejection=='writer-active':
        monkeypatch.setattr(bootstrap,'STOP_WAIT',0)
        holder=bootstrap.source_lease(ROOT)(installed.home/'client.lock',timeout=0)
    if rejection=='no-safe-stop':installed.catalog=False
    if rejection=='install-fails':installed.fail='wheel'
    try:assert bootstrap.main([*UPGRADE,'--install-dir',str(installed.folder)])==1
    finally:
        if holder:holder.close()
    packet=json.loads(capsys.readouterr().out)
    assert packet['error']['code']=={'writer-active':'not_ready','no-safe-stop':'explicit_upgrade_required','install-fails':'installation_failed'}[rejection]
    assert packet['data']['format']=='ioalab.onboarding/1'
    assert installed.marker.read_bytes()==before
    assert (installed.folder/'env/original-program').read_bytes()==b'original program'
    assert not list(installed.folder.glob('env.prev-*'))
    assert installed.config.read_bytes()==b'original account, default and business references'
    if rejection!='install-fails':assert not any('venv' in c or 'install' in c for c in boundary)


def _launcher(*options,env=None):
    import subprocess
    return subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','RemoteSigned','-File',str(ROOT/'src/client/bootstrap.ps1'),*options],
                          capture_output=True,text=True,timeout=60,env=env)


def _handoff(packet):
    data=packet['data']
    assert data['format']=='ioalab.onboarding/1' and data['connected'] is False
    assert [item['section'] for item in data['handoff']]==['接入状态','客户端位置','账号与凭据','双角色Skill','宿主与通知','官网']
    return data


@pytest.mark.skipif(sys.platform!='win32',reason='Windows launcher')
@pytest.mark.parametrize('options',[['-Channel','beta'],['-Tag','client-test','-Channel','main'],
                                    ['-Commit',COMMIT,'-Tag','client-test'],['-Upgrade','-ReuseInstalled']])
def test_windows_launcher_preflight_failures_return_six_item_handoff(tmp_path,options):
    result=_launcher('-HostType','codex',*options,'-InstallDir',str(tmp_path/'client'))
    assert result.returncode==1
    data=_handoff(json.loads(result.stdout))
    assert data['stage']=='preflight' and data['handoff'][1]['value']==str(tmp_path/'client')
    assert data['handoff'][4]['value'].startswith('codex')
    assert not (tmp_path/'client').exists()


GIT_STUB = """import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(Path(__file__).with_name('calls.txt'),'a',encoding='utf-8') as log:log.write(json.dumps(args)+'\\n')
if args==['--version']:print('git version stub');raise SystemExit(0)
tail=args[2:] if args[:1]==['-C'] else args
if tail==['rev-parse','--show-toplevel']:print(os.environ['STUB_TOPLEVEL'])
elif tail==['remote','get-url','origin']:print(os.environ['STUB_ORIGIN'])
elif tail==['rev-parse','HEAD']:print('a'*40)
elif tail[:1]==['show']:print('# not the running launcher')
else:raise SystemExit(128)
"""


@pytest.mark.skipif(sys.platform!='win32',reason='Windows launcher')
@pytest.mark.parametrize('channel,origin,stage',[(None,'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab','source-verification'),
                                                (None,'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git/','source-verification'),
                                                (None,'https://github.com/Weizheng96/Jiuwen-IoA-Lab.git','source'),
                                                ('develop','https://github.com/Weizheng96/Jiuwen-IoA-Lab.git','source-verification'),
                                                ('develop','https://gitcode.com/WeiZheng96/jiuwen-IoA-lab','source')])
def test_windows_launcher_uses_its_official_clone_without_cloning_again(tmp_path,channel,origin,stage):
    import os
    stub=tmp_path/'stub';stub.mkdir()
    # A Git stand-in on PATH answers only local clone queries; the launcher's own object differs, so nothing installs.
    (stub/'git_stub.py').write_text(GIT_STUB,encoding='utf-8')
    (stub/'git.cmd').write_text('@"'+sys.executable+'" -I "%~dp0git_stub.py" %*\r\n',encoding='ascii')
    env={**os.environ,'PATH':str(stub)+os.pathsep+os.environ['PATH'],'STUB_ORIGIN':origin,'STUB_TOPLEVEL':ROOT.as_posix()}
    result=_launcher('-HostType','claude_code',*(['-Channel',channel] if channel else []),'-InstallDir',str(tmp_path/'client'),env=env)
    assert result.returncode==1
    data=_handoff(json.loads(result.stdout))
    assert data['stage']==stage and data['sourceClone']==str(ROOT)
    assert data['resolvedCommit']==('a'*40 if stage=='source-verification' else None)
    calls=[json.loads(line) for line in (stub/'calls.txt').read_text(encoding='utf-8').splitlines()]
    assert not any('clone' in call or 'fetch' in call for call in calls)
    assert not (tmp_path/'client').exists()


@pytest.fixture
def served(tmp_path):
    import functools,http.server,socket,threading
    root=tmp_path/'mirror';root.mkdir()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));closed=probe.getsockname()[1]
    yield SimpleNamespace(root=root,url='http://127.0.0.1:%d/'%server.server_address[1],closed='http://127.0.0.1:%d/'%closed)
    server.shutdown();server.server_close()


@pytest.mark.skipif(sys.platform!='win32',reason='Windows launcher')
@pytest.mark.parametrize('mirror',['valid','tampered','unreachable'])
def test_windows_portable_download_falls_back_to_registered_mirror_under_fixed_sha256(tmp_path,served,mirror):
    import subprocess
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:archive.writestr('tool.txt','portable tool')
    good=buffer.getvalue()
    (served.root/'tool.zip').write_bytes(good if mirror=='valid' else b'not the fixed archive')
    harness=tmp_path/'harness'/'download.ps1';harness.parent.mkdir()
    harness.write_text('''param([string]$Launcher,[string]$Tools,[string]$Official,[string]$Mirror,[string]$Hash)
$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $tools=$Tools; $Proxy=$null; $stage='test'
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Launcher,[ref]$null,[ref]$null)
foreach ($f in $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true)) { . ([scriptblock]::Create($f.Extent.Text)) }
$asset=@{name='tool.zip';directory='tool';executable='tool.txt';url=$Official;mirrors=@($Mirror);hash=$Hash}
try { @{ok=$true;path=(Get-PortableTool $asset)} | ConvertTo-Json -Compress } catch { @{ok=$false;message=$_.Exception.Message} | ConvertTo-Json -Compress }
''',encoding='ascii')
    tools=tmp_path/'tools'
    result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','RemoteSigned','-File',str(harness),'-Launcher',str(ROOT/'src/client/bootstrap.ps1'),
                           '-Tools',str(tools),'-Official',served.closed+'tool.zip',
                           '-Mirror',(served.closed if mirror=='unreachable' else served.url)+'tool.zip','-Hash',hashlib.sha256(good).hexdigest()],
                          capture_output=True,text=True,timeout=60)
    packet=json.loads(result.stdout)
    if mirror=='valid':
        assert packet['ok'] and Path(packet['path']).read_text()=='portable tool'
        assert (tools/'tool.zip').read_bytes()==good
    else:
        assert not packet['ok'] and not (tools/'tool.zip').exists()
        assert ('does not match the fixed SHA256' if mirror=='tampered' else 'mirror downloads failed') in packet['message']
        assert len(list(tools.glob('tool.zip.partial-*')))==(1 if mirror=='tampered' else 0)


def test_sparse_onboarding_clone_materialises_only_installer_paths_before_reading_objects(bootstrap,boundary,monkeypatch,tmp_path,capsys):
    original=bootstrap.subprocess.run
    def execute(command,**kwargs):
        result=original(command,**kwargs)
        if command[0]=='git' and 'config' in command:result.stdout='true\n'
        if command[0]=='git' and 'show' in command and command[-1].endswith('SOURCES.json'):
            result.stdout=json.dumps({'components':[{'id':'jiuwenswarm-process-sdk','wheelInfo':{'file':'wheels/process_sdk-0.1.0-py3-none-any.whl'}},
                                                    {'id':'workswarm','wheelInfo':{'file':'wheels/large-unrelated.whl'}}]})
        return result
    monkeypatch.setattr(bootstrap.subprocess,'run',execute)
    assert bootstrap.main(['--host','codex','--tag','client-test','--install-dir',str(tmp_path/'client')])==0
    sparse=next(i for i,c in enumerate(boundary) if 'sparse-checkout' in c)
    objects=next(i for i,c in enumerate(boundary) if 'cat-file' in c)
    assert sparse<objects
    assert boundary[sparse][boundary[sparse].index('--no-cone')+1:]==['/*','!/*/','/src/client/','/src/a2c/','/vendor/SOURCES.json','/vendor/licenses/',
                                                                       '/vendor/wheels/process_sdk-0.1.0-py3-none-any.whl']


def test_partial_sparse_clone_snapshot_fetches_only_installer_objects(bootstrap,tmp_path):
    import subprocess
    # Real Git over a local file:// remote: the one-sentence clone shape without network access.
    upload='git -c uploadpack.allowFilter=true -c uploadpack.allowAnySHA1InWant=true upload-pack'
    clone=tmp_path/'clone'
    git=lambda *args:subprocess.run(['git',*args],capture_output=True,check=True).stdout
    git('clone','-q','--depth','1','--filter=blob:none','--sparse','--upload-pack',upload,ROOT.as_uri(),str(clone))
    git('-C',str(clone),'config','remote.origin.uploadpack',upload)
    git('-C',str(clone),'sparse-checkout','set','src/client')
    commit=git('-C',str(clone),'rev-parse','HEAD').decode().strip()
    target=tmp_path/'snapshot';target.mkdir()
    assert bootstrap.snapshot(clone,commit,target)==target/'src/client'
    size=sum(p.stat().st_size for p in (clone/'.git').rglob('*') if p.is_file())
    assert size<40*1024*1024, size
    assert (clone/'README_AGENT.md').is_file()
    files=[p for p in target.rglob('*') if p.is_file()]
    committed={}
    for line in git('-C',str(ROOT),'ls-tree','-r','-z',commit,'--','src/client','src/a2c','vendor').decode().split(chr(0)):
        if line:committed[line.split(chr(9),1)[1]]=line.split()[2]
    blob=lambda data:hashlib.sha1(b'blob '+str(len(data)).encode()+bytes(1)+data).hexdigest()
    assert files and all(blob(p.read_bytes())==committed[p.relative_to(target).as_posix()] for p in files)
