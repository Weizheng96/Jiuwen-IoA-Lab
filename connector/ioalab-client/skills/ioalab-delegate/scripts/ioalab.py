"""Deterministically prepare the bundled SDK in an isolated user environment."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import venv


def ensure_runtime(assets=None, cache=None):
    assets=Path(assets or Path(__file__).resolve().parents[1]/'assets')
    manifest_file=assets/'runtime.json'
    if not manifest_file.exists():
        raise RuntimeError('请下载平台提供的完整技能安装包；源码技能需先执行 tools/build_connector.py')
    raw=manifest_file.read_bytes();manifest=json.loads(raw)
    release=hashlib.sha256(raw).hexdigest()[:20]
    cache=Path(cache or Path.home()/'.ioalab/runtimes').resolve()
    cache.mkdir(parents=True,exist_ok=True)
    envdir=(cache/release).resolve()
    if not envdir.is_relative_to(cache): raise RuntimeError('Invalid runtime cache path')
    python=envdir/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    ready=envdir/'.ready'
    expected=hashlib.sha256(raw).hexdigest()
    if ready.exists() and python.exists() and ready.read_text()==expected:
        return python
    lock=cache/(release+'.installing')
    until=time.monotonic()+180
    while True:
        try:
            fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.write(fd,str(os.getpid()).encode());os.close(fd)
            break
        except FileExistsError:
            if ready.exists() and python.exists() and ready.read_text()==expected: return python
            if time.monotonic()>until:
                raise RuntimeError('SDK安装尚未完成；若安装进程已经退出，请删除安装锁后重试：'+str(lock))
            time.sleep(.2)
    try:
        wheels=[]
        for item in manifest['wheels']:
            path=(assets/'wheels'/item['file']).resolve()
            if path.parent!=(assets/'wheels').resolve() or not path.is_file():
                raise RuntimeError('SDK安装包文件缺失')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                raise RuntimeError('SDK安装包校验失败')
            wheels.append(str(path))
        if hashlib.sha256((assets/'requirements.lock').read_bytes()).hexdigest()!=manifest['constraintsSha256']:
            raise RuntimeError('SDK依赖清单校验失败')
        venv.EnvBuilder(with_pip=True,clear=False).create(envdir)
        cmd=[str(python),'-m','pip','install','--disable-pip-version-check','--no-input',
             '--constraint',str(assets/'requirements.lock'),*wheels]
        subprocess.run(cmd,check=True,stdout=sys.stderr,stderr=sys.stderr)
        check='import ioalab_sdk; import importlib.metadata as m; assert m.version("ioalab-sdk")=='+repr(manifest['sdkVersion'])
        subprocess.run([str(python),'-c',check],check=True,stdout=sys.stderr,stderr=sys.stderr)
        tmp=envdir/'.ready.tmp';tmp.write_text(expected);tmp.replace(ready)
        return python
    finally:
        lock.unlink(missing_ok=True)


def main():
    try:
        settings=Path(__file__).resolve().parents[1]/'settings.local.json'
        if settings.exists():
            values=json.loads(settings.read_text(encoding='utf-8'))
            for key in ('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD','IOALAB_HOME','IOALAB_SCHEDULE_FILE','IOALAB_MIN_QUOTES'):
                if values.get(key): os.environ.setdefault(key,str(values[key]))
        cache=os.environ.get('IOALAB_RUNTIME_CACHE') or (str(Path(os.environ['IOALAB_HOME']).parent/'runtimes') if os.environ.get('IOALAB_HOME') else None)
        python=ensure_runtime(cache=cache)
        raise SystemExit(subprocess.call([str(python),'-m','ioalab_sdk.cli',*sys.argv[1:]],env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__': main()
