"""Installation lifecycle: public CLI preconditions, real locks and failure recovery."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest
from test_access_products import bootstrap


def bundle(tmp_path):
    module=bootstrap();installer=module.installation();root=tmp_path/'skill';assets=root/'assets'
    wheels=assets/'wheels';wheels.mkdir(parents=True)
    (wheels/'placeholder.whl').write_bytes(b'input');(assets/'requirements.lock').write_bytes(b'')
    manifest={'format':1,'skillVersion':'0.0.1','versions':{},'constraintsSha256':hashlib.sha256(b'').hexdigest(),
        'wheels':[{'file':'placeholder.whl','sha256':hashlib.sha256(b'input').hexdigest()}]}
    (assets/'runtime.json').write_text(json.dumps(manifest),encoding='utf-8')
    (assets/'skill.json').write_text('{"role":"service"}',encoding='utf-8')
    scripts=root/'scripts';scripts.mkdir()
    shutil.copy2(module.__file__,scripts/'ioalab.py');shutil.copy2(installer.__file__,scripts/'installer.py')
    module.__file__=str(scripts/'ioalab.py')
    return module,installer,assets,tmp_path/'cache'


def state(release,phase='installing'):
    attempt=str(uuid.uuid4())
    return {'releaseId':release.id,'attemptId':attempt,'phase':phase,'pid':2147483647,'logPath':str(release.log(attempt))}


@pytest.mark.parametrize('version',[None,'','v0.0.1',False])
def test_product_manifest_requires_its_explicit_skill_version_before_creating_cache(tmp_path,version):
    _,installer,assets,cache=bundle(tmp_path)
    path=assets/'runtime.json';manifest=json.loads(path.read_text())
    if version is None:manifest.pop('skillVersion')
    else:manifest['skillVersion']=version
    path.write_text(json.dumps(manifest),encoding='utf-8');original=path.read_bytes()
    with pytest.raises(RuntimeError,match='skillVersion'):installer.Release(assets,cache)
    assert not cache.exists() and path.read_bytes()==original


def test_new_skill_version_uses_a_distinct_release_and_preserves_previous_cache(tmp_path):
    _,installer,assets,cache=bundle(tmp_path)
    before=installer.Release(assets,cache);before.envdir.mkdir()
    original=before.envdir/'original';original.write_bytes(b'previous-cache')
    path=assets/'runtime.json';manifest=json.loads(path.read_text());manifest['skillVersion']='0.0.2'
    path.write_text(json.dumps(manifest),encoding='utf-8')
    after=installer.Release(assets,cache)
    assert after.id!=before.id and after.envdir!=before.envdir
    assert after.inspect()['status']=='rejected' and not after.envdir.exists()
    assert original.read_bytes()==b'previous-cache'


@pytest.mark.parametrize('operation',['runtime.install.status','runtime.status','runtime.validate','runtime.web-extension','provider.pause'])
def test_unprepared_queries_and_business_never_install_or_read_user_files(tmp_path,monkeypatch,capsys,operation):
    module,installer,assets,cache=bundle(tmp_path);monkeypatch.setenv('IOALAB_ACCESS_CACHE',str(cache))
    config=tmp_path/'private-settings.json';args=tmp_path/'request.json'
    config.write_bytes(b'PRIVATE NOT JSON');args.write_bytes(b'{"profile":"service"}')
    original=config.read_bytes(),args.read_bytes()
    read_text=Path.read_text;read_bytes=Path.read_bytes
    def guarded_text(path,*a,**k):
        assert path not in (config,args),'Bootstrap read a user-owned file'
        return read_text(path,*a,**k)
    def guarded_bytes(path,*a,**k):
        assert path not in (config,args),'Bootstrap read a user-owned file'
        return read_bytes(path,*a,**k)
    monkeypatch.setattr(Path,'read_text',guarded_text);monkeypatch.setattr(Path,'read_bytes',guarded_bytes)
    monkeypatch.setattr(module,'invoke',lambda *_:pytest.fail('Unprepared release reached the SDK'))
    assert module.main([operation,'--config',str(config),'--args-file',str(args)])==1
    result=json.loads(capsys.readouterr().out)
    assert result['error']['code']=='not_ready' and result['data']['requestSubmitted'] is False
    assert not list(cache.glob('*.install.json')) and not (cache/installer.Release(assets,cache).id[:24]).exists()
    assert (read_bytes(config),read_bytes(args))==original


def test_worker_environment_is_allowlisted_and_not_platform_or_model_context(tmp_path,monkeypatch):
    module,installer,assets,cache=bundle(tmp_path)
    canaries={'IOALAB_PASSWORD':'private-platform-canary','CUSTOM_OWNER_CREDENTIAL':'private-owner-canary',
        'DASHSCOPE_API_KEY':'private-model-canary','SMTP_PASSWORD':'private-mail-canary',
        'PYTHONPATH':'private-import-canary','PIP_EXTRA_INDEX_URL':'https://private:password@example.invalid'}
    for key,value in canaries.items():monkeypatch.setenv(key,value)
    monkeypatch.setenv('IOALAB_PYPI_INDEX','https://pypi.tuna.tsinghua.edu.cn/simple')
    env=installer.worker_environment()
    assert not set(canaries)&set(env) and env['IOALAB_PYPI_INDEX'].startswith('https://')
    actual=subprocess.run([sys.executable,'-I','-c','import os,json; print(json.dumps(dict(os.environ)))'],
        env=env,capture_output=True,text=True,check=True)
    child=json.loads(actual.stdout)
    assert not any(value in actual.stdout for value in canaries.values())
    assert child['PYTHONNOUSERSITE']=='1' and child['PYTHONUTF8']=='1'
    # Native dependencies must still detect the real Windows architecture in the isolated worker.
    import platform
    native=subprocess.run([sys.executable,'-I','-c','import platform,wasmtime; print(platform.machine())'],
        env=env,capture_output=True,text=True,check=True)
    assert native.stdout.strip()==platform.machine()


def test_live_os_lock_is_evidence_and_interrupted_owner_is_not_automatically_replaced(tmp_path):
    module,installer,assets,cache=bundle(tmp_path);release=installer.Release(assets,cache)
    job=state(release);release.save(job);original=release.task.read_bytes();started=tmp_path/'started'
    script=tmp_path/'lock-owner.py'
    script.write_text('import importlib.util,time\nfrom pathlib import Path\n'
        's=importlib.util.spec_from_file_location("installer",'+repr(str(Path(installer.__file__).resolve()))+')\n'
        'm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
        'with m.lock(Path('+repr(str(release.install_lock))+')):\n'
        '    Path('+repr(str(started))+').write_text("ready")\n    time.sleep(30)\n',encoding='utf-8')
    child=subprocess.Popen([sys.executable,'-I',str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        until=time.monotonic()+10
        while not started.exists():
            assert child.poll() is None,child.communicate()[1]
            assert time.monotonic()<until;time.sleep(.02)
        waiting=installer.prepare(assets,cache)
        assert waiting['status']=='waiting' and waiting['data']['attemptId']==job['attemptId']
    finally:
        child.terminate();child.communicate(timeout=10)
    assert not installer.busy(release.install_lock)
    for launch in (False,True):
        result=installer.prepare(assets,cache,launch=launch,retry=True)
        assert result['status']=='unknown' and result['data']['attemptId']==job['attemptId']
    assert release.task.read_bytes()==original and not release.envdir.exists()


def test_known_failure_requires_explicit_install_retry_and_retains_attempt_log(tmp_path,monkeypatch):
    module,installer,assets,cache=bundle(tmp_path)
    # Fail before pip/venv; this tests a real independent worker without network or orphan installers.
    env={**os.environ,'IOALAB_ACCESS_CACHE':str(cache),'IOALAB_PYPI_INDEX':'http://not-allowed.invalid'}
    command=[sys.executable,'-I',module.__file__]
    def call(operation):
        result=subprocess.run([*command,operation],env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
        return json.loads(result.stdout)
    first=call('runtime.start');assert first['status'] in ('waiting','failed')
    until=time.monotonic()+10
    while True:
        result=call('runtime.install.status')
        if result['status']=='failed':break
        assert result['status']=='waiting' and time.monotonic()<until
        time.sleep(.02)
    before=installer.Release(assets,cache);attempt=result['data']['attemptId'];original=before.task.read_bytes()
    assert result['error']['code']=='installation_failed' and Path(result['data']['logPath']).is_file()
    assert call('runtime.start')['data']['attemptId']==attempt and before.task.read_bytes()==original
    retry=call('runtime.install');assert retry['data']['attemptId']!=attempt
    until=time.monotonic()+10
    while call('runtime.install.status')['status']=='waiting':
        assert time.monotonic()<until;time.sleep(.02)
    assert call('runtime.install.status')['status']=='failed' and before.log(attempt).is_file()
    assert not before.envdir.exists()


def test_failed_record_cannot_adopt_or_overwrite_an_unowned_environment(tmp_path,monkeypatch):
    _,installer,assets,cache=bundle(tmp_path);release=installer.Release(assets,cache)
    job=state(release,'failed');release.save(job)
    release.envdir.mkdir();foreign=release.envdir/'owner-business-evidence';foreign.write_bytes(b'preserve-original')
    original=release.task.read_bytes()
    monkeypatch.setattr(installer.subprocess,'Popen',lambda *a,**kw:pytest.fail('Unowned environment launched a new installer'))
    result=installer.prepare(assets,cache,launch=True,retry=True)
    assert result['status']=='unknown' and result['data']['attemptId']==job['attemptId']
    assert result['error']['code']=='unknown_result'
    assert foreign.read_bytes()==b'preserve-original' and release.task.read_bytes()==original


def test_dangling_task_link_is_unknown_and_never_replaced_by_install(tmp_path):
    _,installer,assets,cache=bundle(tmp_path);release=installer.Release(assets,cache)
    target=tmp_path/'absent-original-task.json'
    try:release.task.symlink_to(target)
    except OSError as error:
        if os.name=='nt':pytest.skip('Windows account cannot create the real symlink: '+type(error).__name__)
        raise
    original=os.readlink(release.task)
    for launch in (False,True):
        reply=installer.prepare(assets,cache,launch=launch,retry=True)
        assert reply['status']=='unknown' and reply['error']['code']=='unknown_result'
        assert release.task.is_symlink() and os.readlink(release.task)==original
        assert not target.exists() and not release.envdir.exists()


def test_explicit_retry_archives_its_failed_environment_and_original_attempt(tmp_path):
    module,installer,assets,cache=bundle(tmp_path)
    env={**os.environ,'IOALAB_ACCESS_CACHE':str(cache),'IOALAB_PYPI_INDEX':'https://pypi.tuna.tsinghua.edu.cn/simple'}
    command=[sys.executable,'-I',module.__file__]
    def call(operation):
        child=subprocess.run([*command,operation],env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        return json.loads(child.stdout)
    def failed():
        until=time.monotonic()+30
        while True:
            result=call('runtime.install.status')
            if result['status']=='failed':return result
            assert result['status']=='waiting' and time.monotonic()<until,result
            time.sleep(.05)
    # Invalid local wheel name fails after real venv creation, before any network request.
    call('runtime.install');first=failed();release=installer.Release(assets,cache)
    assert release.envdir.is_dir() and not release.ready.exists()
    evidence=release.envdir/'original-failure.txt';evidence.write_bytes(b'preserved-failure')
    original=release.state();assert original['phase']=='failed'
    env['IOALAB_PYPI_INDEX']='http://not-allowed.invalid'
    retry=call('runtime.install');last=failed()
    retained=last['data']['retainedAttempt']
    assert retained['attemptId']==first['data']['attemptId']!=last['data']['attemptId']
    archived=Path(retained['environment'])
    assert (archived/'original-failure.txt').read_bytes()==b'preserved-failure'
    receipt=json.loads(Path(retained['receiptPath']).read_text(encoding='utf-8'))
    assert receipt['originalTask']==original and receipt['phase']=='archived'
    assert Path(first['data']['logPath']).is_file() and not release.envdir.exists()


def test_wrong_base_interpreter_fails_before_claiming_or_creating_environment(tmp_path,monkeypatch):
    _,installer,assets,cache=bundle(tmp_path);release=installer.Release(assets,cache)
    job=state(release,'launching');release.save(job)
    def different_python(command,**kwargs):
        return subprocess.CompletedProcess(command,0,json.dumps({'implementation':'cpython','version':[3,99],'prefix':'unrelated'}),'')
    # The only replacement is the OS process boundary for the base interpreter probe.
    monkeypatch.setattr(installer.subprocess,'run',different_python)
    assert installer.work(assets,cache,job['attemptId'])==1
    observed=installer.prepare(assets,cache)
    assert observed['status']=='failed' and 'Bootstrap base Python differs' in observed['error']['message']
    assert not release.envdir.exists() and not release.environment_owner.exists()


def test_partial_marker_with_live_writer_is_waiting_not_corruption(tmp_path):
    module,installer,assets,cache=bundle(tmp_path);release=installer.Release(assets,cache)
    job=state(release);release.save(job);release.envdir.mkdir()
    release.marker.write_text(json.dumps(release.identity),encoding='utf-8')
    with installer.lock(release.install_lock):
        waiting=installer.prepare(assets,cache)
        assert waiting['status']=='waiting' and waiting['data']['attemptId']==job['attemptId']
    # The same incomplete markers with no writer must never authorize SDK startup or repair.
    with pytest.raises(RuntimeError,match='do not repair it in place'):
        installer.prepare(assets,cache,launch=True,retry=True)
    assert not release.ready.exists() and release.state()==job


@pytest.mark.parametrize('phase',['launching','installing','completed'])
def test_metadata_alone_never_proves_running_or_ready(tmp_path,phase):
    module,installer,assets,cache=bundle(tmp_path);release=installer.Release(assets,cache)
    job=state(release,phase);release.save(job)
    result=installer.prepare(assets,cache,launch=True,retry=True)
    assert result['status']=='unknown' and result['data']['attemptId']==job['attemptId']
    assert json.loads(release.task.read_text())==job and not release.envdir.exists()


def test_install_metadata_reader_and_writer_share_one_snapshot_lock(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    _,installer,assets,cache=bundle(tmp_path)
    reader,writer=installer.Release(assets,cache),installer.Release(assets,cache)
    original=state(reader,'launching');reader.save(original)
    reading=threading.Event();release_read=threading.Event();writing=threading.Event()
    read_text=Path.read_text
    def held_read(path,*args,**kwargs):
        if path!=reader.task:return read_text(path,*args,**kwargs)
        with path.open('r',encoding='utf-8') as stream:
            reading.set();assert release_read.wait(5),'test did not release original reader'
            return stream.read()
    monkeypatch.setattr(Path,'read_text',held_read)
    updated={**original,'phase':'installing','pid':os.getpid()}
    def write():writing.set();writer.save(updated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(reader.state);second=None
        try:
            assert reading.wait(5)
            second=pool.submit(write);assert writing.wait(5)
            # Keep the real file handle open; replacement must wait for its snapshot.
            assert not second.done(),'writer replaced metadata while its reader held the snapshot'
            assert not threading.Event().wait(.1)
            assert not second.done(),'writer bypassed metadata reader ownership'
        finally:release_read.set()
        assert first.result(timeout=5)==original
        second.result(timeout=5)
    assert writer.state()==updated


def test_corrupt_install_metadata_is_not_trusted_even_with_a_live_worker_lock(tmp_path):
    _,installer,assets,cache=bundle(tmp_path);release=installer.Release(assets,cache)
    job=state(release);release.save(job)
    changed={**job,'logPath':str(tmp_path/'wrong-log')};release.task.write_text(json.dumps(changed),encoding='utf-8')
    before=release.task.read_bytes()
    with installer.lock(release.install_lock):
        result=installer.prepare(assets,cache,launch=True,retry=True)
    assert result['status']=='unknown' and result['error']['code']=='unknown_result'
    assert release.task.read_bytes()==before and not release.envdir.exists()


def test_install_worker_waits_for_status_probe_then_keeps_its_original_attempt(tmp_path):
    import threading
    from conftest import wait_for
    _,installer,assets,cache=bundle(tmp_path)
    release=installer.Release(assets,cache);attempt=str(uuid.uuid4())
    release.save({'releaseId':release.id,'attemptId':attempt,'phase':'launching','logPath':str(release.log(attempt))})
    marker=tmp_path/'claim-started';driver=tmp_path/'claim-worker.py'
    driver.write_text('''import importlib.util
from contextlib import contextmanager
from pathlib import Path
import sys
spec=importlib.util.spec_from_file_location('isolated_installer',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
original=module.lock
@contextmanager
def observed(path,*args,**kwargs):
    if str(path).endswith('.install.lock'):Path(sys.argv[5]).write_text('claiming')
    with original(path,*args,**kwargs):yield
module.lock=observed
raise SystemExit(module.work(sys.argv[2],sys.argv[3],sys.argv[4]))
''',encoding='utf-8')
    env={**os.environ,'IOALAB_PYPI_INDEX':'http://not-allowed.invalid'}
    child=None
    try:
        with release.log(attempt).open('wb') as log:
            with installer.lock(release.install_lock):
                child=subprocess.Popen([sys.executable,'-I',str(driver),str(assets.parent/'scripts/installer.py'),
                    str(assets),str(cache),attempt,str(marker)],env=env,stdout=log,stderr=subprocess.STDOUT)
                wait_for(marker.is_file,timeout=5)
                threading.Event().wait(.1)
                assert child.poll() is None,'Status probe collision killed the original installation worker'
            assert child.wait(timeout=10)==1  # Deliberate mirror validation failure, before venv/pip.
        current=release.state()
        assert current['attemptId']==attempt and current['phase']=='failed'
        assert 'HTTPS package mirror' in current['message']
        assert not release.envdir.exists() and release.log(attempt).is_file()
    finally:
        if child and child.poll() is None:
            child.terminate();child.wait(timeout=10)
