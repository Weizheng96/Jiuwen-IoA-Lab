"""Atomic private JSON and protection for SDK-owned local data."""
import json
import os
from pathlib import Path
import tempfile
import csv
from functools import lru_cache
import re
import subprocess

from .protocol import RuntimeFault


@lru_cache(maxsize=1)
def windows_sid():
    result=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,timeout=5,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    rows=list(csv.reader(result.stdout.decode('utf-8',errors='replace').splitlines()))
    sid=rows[0][1] if rows and len(rows[0])>1 else ''
    if not re.fullmatch(r'S-[0-9-]+',sid):raise RuntimeFault('not_ready','Cannot determine the local owner SID')
    return sid


def restrict(path,*,directory=False):
    if os.name!='nt':
        os.chmod(path,0o700 if directory else 0o600)
        return
    permission='(OI)(CI)F' if directory else '(F)'
    result=subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r','*'+windows_sid()+':'+permission],
                          capture_output=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeFault('not_ready','Cannot restrict local data to its owning user','configure_private_directory')


def prepare_home(path):
    """Only touch an SDK-owned directory, never a parent/user document directory."""
    path=Path(path)
    if (path/'keys.json').exists():
        raise RuntimeFault('identity_conflict','Direct-client identity directory needs explicit migration','migrate')
    allowed={'client.lock','account.sqlite','account.sqlite-wal','account.sqlite-shm','account.sqlite.init.lock',
             'runtime.sqlite','runtime.sqlite-wal','runtime.sqlite-shm','runtime.json','runtime.log',
             'identities','identity-index.json','roles','confirmations','materials'}
    if path.exists() and any(child.name not in allowed and not child.name.startswith(('.runtime.json-','.identity-index.json-'))
                             for child in path.iterdir()):
        raise RuntimeFault('invalid_input','Use a dedicated SDK data directory; unrelated files are present','configure_data_dir')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    restrict(path,directory=True)


def private_json(path,value):
    _write_private(path,lambda stream:json.dump(value,stream,ensure_ascii=False,sort_keys=True,allow_nan=False))


def private_text(path,value):
    _write_private(path,lambda stream:stream.write(value))


def _write_private(path,write):
    path=Path(path)
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
            restrict(name)
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))
