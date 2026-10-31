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
    manifest={'versions':{},'constraintsSha256':hashlib.sha256(b'').hexdigest(),
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
