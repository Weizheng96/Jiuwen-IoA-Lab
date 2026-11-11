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
    """Leave only the owning user on the path: POSIX mode bits, or a protected DACL replaced as a whole on Windows,
    so earlier explicit grants to anyone else are removed rather than kept."""
    if os.name!='nt':
        os.chmod(path,0o700 if directory else 0o600)
        return
    import ctypes
    from ctypes import wintypes
    advapi=ctypes.WinDLL('advapi32',use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=wintypes.BOOL
    advapi.GetSecurityDescriptorDacl.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.BOOL),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.BOOL)]
    advapi.GetSecurityDescriptorDacl.restype=wintypes.BOOL
    advapi.SetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,ctypes.c_int,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    advapi.SetNamedSecurityInfoW.restype=wintypes.DWORD
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    # Protected DACL with one full-control entry for the owner; directories pass it on to new children.
    sddl='D:P(A;'+('OICI' if directory else '')+';FA;;;'+windows_sid()+')'
    descriptor=ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(descriptor),None):
        raise RuntimeFault('not_ready','Cannot restrict local data to its owning user','configure_private_directory')
    try:
        present,defaulted,dacl=wintypes.BOOL(),wintypes.BOOL(),ctypes.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(descriptor,ctypes.byref(present),ctypes.byref(dacl),ctypes.byref(defaulted)) or not present:
            raise RuntimeFault('not_ready','Cannot restrict local data to its owning user','configure_private_directory')
        # SE_FILE_OBJECT=1; DACL_SECURITY_INFORMATION|PROTECTED_DACL_SECURITY_INFORMATION replaces every entry.
        if advapi.SetNamedSecurityInfoW(str(path),1,0x00000004|0x80000000,None,None,dacl,None):
            raise RuntimeFault('not_ready','Cannot restrict local data to its owning user','configure_private_directory')
    finally:
        kernel.LocalFree(descriptor)


def prepare_home(path):
    """Only touch an SDK-owned directory, never a parent/user document directory."""
    path=Path(path)
    if (path/'keys.json').exists():
        raise RuntimeFault('identity_conflict','Direct-client identity directory needs explicit migration','migrate')
    allowed={'client.lock','account.sqlite','account.sqlite-wal','account.sqlite-shm','account.sqlite.init.lock',
             'runtime.sqlite','runtime.sqlite-wal','runtime.sqlite-shm','runtime.json','runtime.log',
             'identities','identity-index.json','roles','confirmations','materials','deliveries',
             'account-setup.json','account-setup-result.json','account-setup.lock','account-setup-launch.lock'}
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


def read_arguments(path,*,configuration,home):
    """Read a distinct user arguments file, never a settings or runtime file."""
    path=Path(path).resolve()
    try:
        if path.samefile(configuration) or path.is_relative_to(Path(home).resolve()):
            raise RuntimeFault('invalid_input','Use a separate arguments file outside the configuration and SDK data','correct_input')
        value=read_json(path)
    except UnicodeDecodeError:
        raise RuntimeFault('invalid_input','Arguments file must use UTF-8; recreate it with the native write_file tool','correct_input') from None
    except json.JSONDecodeError as exc:
        raise RuntimeFault('invalid_input',f'Arguments JSON syntax is invalid at line {exc.lineno}, column {exc.colno}; correct the JSON without changing its encoding','correct_input') from None
    except OSError:
        raise RuntimeFault('invalid_input','Arguments file is unavailable or unreadable; create a separate arguments file','correct_input') from None
    if not isinstance(value,dict):raise RuntimeFault('invalid_input','Arguments file must be a JSON object','correct_input')
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))
