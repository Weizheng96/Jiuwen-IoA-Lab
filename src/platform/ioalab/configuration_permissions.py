"""OS protection for the platform's single private configuration file."""
import ctypes
import csv
import os
from pathlib import Path
import re
import stat
import subprocess

from .errors import LabError


def _reject():
    raise LabError('forbidden','Platform configuration ownership or private permissions are invalid',status=403)


def owner_sid():
    result=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,timeout=5,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    rows=list(csv.reader(result.stdout.decode('utf-8',errors='replace').splitlines()))
    sid=rows[0][1] if rows and len(rows[0])>1 else ''
    if not re.fullmatch(r'S-[0-9-]+',sid):_reject()
    return sid


def private(path):
    path=Path(path)
    if not path.is_absolute() or path!=path.resolve() or any(item.is_symlink() for item in (path,*path.parents)):_reject()
    value=path.stat()
    if not stat.S_ISREG(value.st_mode) and not stat.S_ISDIR(value.st_mode):_reject()
    if stat.S_ISREG(value.st_mode) and value.st_nlink!=1:_reject()
    if os.name!='nt':
        if value.st_uid!=os.geteuid() or stat.S_IMODE(value.st_mode)&0o077:_reject()
        return
    from ctypes import wintypes
    api=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    ptr=ctypes.c_void_p
    api.GetNamedSecurityInfoW.argtypes=(wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ptr),ptr,ctypes.POINTER(ptr),ptr,ctypes.POINTER(ptr))
    api.ConvertSidToStringSidW.argtypes=(ptr,ctypes.POINTER(ptr));api.GetAce.argtypes=(ptr,wintypes.DWORD,ctypes.POINTER(ptr))
    kernel.LocalFree.argtypes=(ptr,)
    owner,acl,descriptor=ptr(),ptr(),ptr()
    result=api.GetNamedSecurityInfoW(str(path),1,5,ctypes.byref(owner),None,ctypes.byref(acl),None,ctypes.byref(descriptor))
    def sid(pointer):
        output=ptr()
        if not api.ConvertSidToStringSidW(pointer,ctypes.byref(output)):_reject()
        try:return ctypes.wstring_at(output)
        finally:kernel.LocalFree(output)
    try:
        current=owner_sid()
        if result or not owner or not acl or sid(owner)!=current:_reject()
        granted=False
        for index in range(ctypes.c_ushort.from_address(acl.value+4).value):
            ace=ptr()
            if not api.GetAce(acl,index,ctypes.byref(ace)):_reject()
            kind=ctypes.c_ubyte.from_address(ace.value).value
            if kind==1:continue
            if kind!=0:_reject()
            mask=ctypes.c_ulong.from_address(ace.value+4).value; target=sid(ptr(ace.value+8))
            if mask and target not in (current,'S-1-5-18'):_reject()
            granted=granted or bool(mask and target==current)
        if not granted:_reject()
    finally:
        if descriptor:kernel.LocalFree(descriptor)


def protect_new(path,*,directory=False):
    """Only for newly created platform-owned entries; never adopt existing inputs."""
    if os.name!='nt':os.chmod(path,0o700 if directory else 0o600)
    else:
        permissions='(OI)(CI)F' if directory else 'F'
        result=subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r','*'+owner_sid()+':'+permissions],
            capture_output=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:_reject()
    private(Path(path).absolute())
