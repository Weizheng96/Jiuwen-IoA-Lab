"""Deployment must work from local inputs and reject hidden VCS or changed files."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
from tools.check_vendor import validate

ROOT=Path(__file__).resolve().parents[1]


def sha(blob):return hashlib.sha256(blob).hexdigest()


def bundle(tmp_path):
    root=tmp_path;vendor=root/'vendor';vendor.mkdir();(root/'personal-assistant').mkdir()
    license=vendor/'LICENSE';license.write_bytes(b'upstream license')
    archive=vendor/'source.tar.gz';raw=b'pinned upstream source';archive.write_bytes(gzip.compress(raw,mtime=0))
    source=vendor/'A2X-registry/module.py';source.parent.mkdir();source.write_bytes(b'VALUE=1\n')
    wheel=vendor/'runtime-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as output:
        output.writestr('runtime-1.0.dist-info/METADATA','Name: runtime\nVersion: 1.0\n')
        output.writestr('jiuwenswarm/channels/web/frontend/dist/index.html','original UI')
    common={'repository':'https://example.test/upstream','commit':'a'*40,'licenseFiles':{'LICENSE':sha(license.read_bytes())}}
    manifest={'components':[
        {**common,'id':'registry','delivery':'directory','sourceDirectory':'A2X-registry','sourceFiles':{'module.py':sha(source.read_bytes())}},
        {**common,'id':'runtime','delivery':'build-wheel','wheel':wheel.name,'archive':archive.name,'archiveSha256':sha(archive.read_bytes()),'sourceTarSha256':sha(raw),'frontend':True,'frontendBuild':{'assetFiles':1},'wheelInfo':{'file':wheel.name,'sha256':sha(wheel.read_bytes()),'package':'runtime','version':'1.0'}}]}
    (vendor/'SOURCES.json').write_text(json.dumps(manifest))
    lock=root/'personal-assistant/requirements-swarm.lock';lock.write_text('./vendor/'+wheel.name+'\n')
    (root/'personal-assistant/swarm.lock.json').write_text(json.dumps({'requirementsSha256':sha(lock.read_bytes())}))
    return root


def test_local_bundle_is_valid_and_source_tampering_fails(tmp_path):
    root=bundle(tmp_path);validate(root)
    (root/'vendor/A2X-registry/module.py').write_text('changed')
    with pytest.raises(ValueError,match='changed vendored'):validate(root)


def test_lock_cannot_hide_remote_vcs_with_updated_digest(tmp_path):
    root=bundle(tmp_path);lock=root/'personal-assistant/requirements-swarm.lock'
    lock.write_text(lock.read_text()+'pkg @ git+https://github.com/example/repo.git\n')
    (root/'personal-assistant/swarm.lock.json').write_text(json.dumps({'requirementsSha256':sha(lock.read_bytes())}))
    with pytest.raises(ValueError,match='Remote/VCS'):validate(root)


def test_bundled_runtime_must_include_original_web_ui(tmp_path):
    root=bundle(tmp_path);manifest=root/'vendor/SOURCES.json';data=json.loads(manifest.read_text());item=data['components'][1]
    wheel=root/'vendor'/item['wheel']
    with zipfile.ZipFile(wheel,'w') as output:output.writestr('runtime-1.0.dist-info/METADATA','Name: runtime\nVersion: 1.0\n')
    item['wheelInfo']['sha256']=sha(wheel.read_bytes());manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='web UI missing'):validate(root)


def test_archive_traversal_and_git_checkout_are_rejected(tmp_path):
    root=bundle(tmp_path);(root/'vendor/A2X-registry/.git').write_text('gitdir: elsewhere')
    with pytest.raises(ValueError,match='submodules'):validate(root)
    (root/'vendor/A2X-registry/.git').unlink()
    manifest=root/'vendor/SOURCES.json';data=json.loads(manifest.read_text());data['components'][0]['licenseFiles']={'../private.txt':'0'*64};manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='escapes'):validate(root)


def test_verify_only_needs_no_git_npm_or_package_index(tmp_path):
    result=subprocess.run([sys.executable,str(ROOT/'personal-assistant/install_swarm.py'),'--verify-only'],cwd=tmp_path,
        env={**os.environ,'PATH':str(tmp_path),'PIP_NO_INDEX':'1','PYTHONUTF8':'1'},capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr
    assert 'local Jiuwen inputs verified' in result.stdout


def test_same_version_new_wheel_reinstalls_without_overwriting_workspace(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('local_swarm_installer',ROOT/'personal-assistant/install_swarm.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    environment=tmp_path/'venv';environment.mkdir();data=tmp_path/'workspace';data.mkdir();config=data/'config.yaml';config.write_text('existing user settings')
    manifest={'components':[{'id':'runtime','delivery':'build-wheel','wheelInfo':{'sha256':'first'}}]};wheel=tmp_path/'runtime.whl';calls=[]
    monkeypatch.setattr(installer,'installation_plan',lambda:(manifest,[wheel],'local requirements\n'))
    monkeypatch.setattr(installer,'run',lambda argv,**kwargs:calls.append([str(a) for a in argv]))
    monkeypatch.setattr(installer,'initialize_workspace',lambda *args:None)
    monkeypatch.setattr(installer,'ensure_pip',lambda *args:None)
    monkeypatch.setattr(sys,'argv',['install_swarm.py','--venv',str(environment),'--data',str(data)])
    installer.main();assert len(calls)==2 and '--no-deps' in calls[0] and '--no-index' in calls[1]
    calls.clear();installer.main();assert not calls
    manifest['components'][0]['wheelInfo']['sha256']='second';installer.main()
    assert len(calls)==2 and '--force-reinstall' in calls[1]
    assert config.read_text()=='existing user settings'


def test_wheel_cannot_contain_unmaterialized_git_lfs_pointer(tmp_path):
    root=bundle(tmp_path);manifest=root/'vendor/SOURCES.json';data=json.loads(manifest.read_text());item=data['components'][1]
    wheel=root/'vendor'/item['wheel']
    with zipfile.ZipFile(wheel,'a') as output:output.writestr('runtime/data.bin',b'version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 100\n')
    item['wheelInfo']['sha256']=sha(wheel.read_bytes());manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Git LFS pointer'):validate(root)


@pytest.mark.parametrize('line',['https://github.com/example/package.whl','--extra-index-url https://example.test/simple','-r remote-requirements.txt'])
def test_lock_cannot_add_remote_inputs_or_include_files(tmp_path,line):
    root=bundle(tmp_path);lock=root/'personal-assistant/requirements-swarm.lock';lock.write_text(lock.read_text()+line+'\n')
    (root/'personal-assistant/swarm.lock.json').write_text(json.dumps({'requirementsSha256':sha(lock.read_bytes())}))
    with pytest.raises(ValueError):validate(root)


def test_jiuwen_provenance_cannot_drift_between_manifests(tmp_path):
    root=bundle(tmp_path);path=root/'vendor/SOURCES.json';data=json.loads(path.read_text());data['components'][1]['id']='workswarm';path.write_text(json.dumps(data))
    spec=root/'personal-assistant/swarm.lock.json';lock=json.loads(spec.read_text());lock.update(commit='b'*40,sourceArchiveSha256=data['components'][1]['sourceTarSha256']);spec.write_text(json.dumps(lock))
    with pytest.raises(ValueError,match='source pin differs'):validate(root)


def upgrade_fixture(tmp_path):
    import shutil
    bash=Path('D:/tool/Git/bin/bash.exe') if os.name=='nt' else Path(shutil.which('bash') or '/missing-bash')
    if not bash.is_file():pytest.skip('Bash is required for deployment script checks')
    app=tmp_path/'app';(app/'deploy').mkdir(parents=True);(app/'设计').mkdir();(app/'设计/VERSION').write_text('test-version')
    state=tmp_path/'state';state.mkdir();bin_dir=tmp_path/'bin';bin_dir.mkdir();config=tmp_path/'config';config.mkdir()
    script=(ROOT/'deploy/upgrade.sh').read_text('utf-8').replace('/etc/ioalab','"$UPGRADE_TEST_CONFIG"')
    (app/'deploy/upgrade.sh').write_text(script,encoding='utf-8',newline='\n')
    (app/'deploy/install.sh').write_text('#!/usr/bin/env bash\nprintf "install local\\n" >> "$UPGRADE_TEST_LOG"\n',newline='\n')
    mocks={
        'git':'printf "git %s\\n" "$*" >> "$UPGRADE_TEST_LOG"; if [ "$*" = "remote get-url origin" ]; then echo https://github.com/example/lab.git; else exit 91; fi',
        'sudo':'printf "backup\\n" >> "$UPGRADE_TEST_LOG"',
        'systemctl':"""printf "systemctl %s\n" "$*" >> "$UPGRADE_TEST_LOG"
if [ "$1" = is-active ]; then [[ "${@: -1}" = ioalab || "${@: -1}" = ioalab-providers ]]; fi"""}
    for name,body in mocks.items():
        path=bin_dir/name;path.write_text('#!/usr/bin/env bash\n'+body+'\n',newline='\n');path.chmod(0o755)
    log=tmp_path/'calls.log'
    code='''convert() { if command -v cygpath >/dev/null; then cygpath -u "$1"; else printf "%s" "$1"; fi; }
export IOALAB_APP_DIR="$(convert "$1")" IOALAB_STATE_DIR="$(convert "$2")" UPGRADE_TEST_CONFIG="$(convert "$3")" UPGRADE_TEST_LOG="$(convert "$4")"
export PATH="$(convert "$5"):$PATH"
bash "$IOALAB_APP_DIR/deploy/upgrade.sh" "$6"
'''
    def run(ref):
        return subprocess.run([str(bash),'-c',code,'upgrade-test',str(app),str(state),str(config),str(log),str(bin_dir),ref],capture_output=True,text=True,encoding='utf-8',env={**os.environ,'LANG':'C.UTF-8'})
    return run,log


def test_upgrade_rejects_github_before_stopping_services(tmp_path):
    run,log=upgrade_fixture(tmp_path);result=run('main')
    assert result.returncode!=0
    calls=log.read_text();assert 'git remote get-url origin' in calls and 'systemctl' not in calls and 'backup' not in calls


def test_local_upgrade_restores_only_previously_active_services_without_git(tmp_path):
    run,log=upgrade_fixture(tmp_path);result=run('--local')
    assert result.returncode==0,result.stdout+result.stderr
    calls=log.read_text();assert 'git ' not in calls and 'install local' in calls and 'backup' in calls
    assert 'systemctl start ioalab ioalab-providers' in calls
    assert 'systemctl start ioalab-swarm' not in calls and 'systemctl start ioalab-assistant' not in calls


def test_initialization_preserves_packaged_template_and_existing_user_configuration(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('workspace_installer',ROOT/'personal-assistant/install_swarm.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    package=tmp_path/'fake/jiuwenswarm';(package/'common').mkdir(parents=True);(package/'resources').mkdir()
    (package/'__init__.py').write_text('');(package/'common/__init__.py').write_text('')
    template=package/'resources/config.yaml';template.write_text(json.dumps({'updater':{'enabled':True},'preferred_language':'zh'}));original=template.read_bytes()
    (package/'common/utils.py').write_text('''import json,os
from pathlib import Path
ROOT=Path(os.environ['JIUWENSWARM_DATA_DIR'])
TEMPLATE=Path(__file__).parents[1]/'resources/config.yaml'
CACHED_CONFIG=ROOT/'config/config.yaml' if (ROOT/'config').exists() else TEMPLATE
def prepare_workspace(overwrite,preferred_language,workspace_dir):
    path=workspace_dir/'config/config.yaml';path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():path.write_bytes(TEMPLATE.read_bytes())
    if preferred_language is not None:
        data=json.loads(path.read_text());data['preferred_language']=preferred_language;path.write_text(json.dumps(data))
''')
    (package/'common/config.py').write_text('''import json
from .utils import CACHED_CONFIG
def update_config(mutator):
    data=mutator(json.loads(CACHED_CONFIG.read_text()));CACHED_CONFIG.write_text(json.dumps(data))
''')
    monkeypatch.setenv('PYTHONPATH',str(package.parent))
    workspace=tmp_path/'workspace';installer.initialize_workspace(Path(sys.executable),workspace)
    config=workspace/'config/config.yaml';data=json.loads(config.read_text());assert data['updater']['enabled'] is False and template.read_bytes()==original
    data.update(preferred_language='en',userSetting='keep');config.write_text(json.dumps(data));before=config.read_bytes()
    settings=workspace/'skill/settings.local.json';settings.parent.mkdir();settings.write_text('{"userAccount":"preserved"}')
    installer.initialize_workspace(Path(sys.executable),workspace)
    assert config.read_bytes()==before and template.read_bytes()==original and settings.read_text()=='{"userAccount":"preserved"}'
