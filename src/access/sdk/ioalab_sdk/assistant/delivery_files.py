"""Private, non-executing local copies of already verified delivery bytes."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import stat
import unicodedata
import uuid

from a2c.codec import digest_bytes
from ..runtime import restrict


def safe_names(names):
    seen=set()
    for name in names:
        if (not isinstance(name,str) or not name or name in ('.','..')
                or any(unicodedata.category(c)=='Cc' or c in '<>:"/\\|?*' for c in name)
                or name.endswith(('.', ' '))
                or re.fullmatch(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?',name,re.I)):
            raise ValueError('Unsafe local delivery filename')
        key=name.casefold() if os.name=='nt' else name
        if key in seen:raise ValueError('Delivery filenames collide on this filesystem')
        seen.add(key)


def _safe_stat(value,*,directory=False):
    if (stat.S_ISLNK(value.st_mode) or getattr(value,'st_file_attributes',0)&0x400
            or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
            or not directory and value.st_nlink!=1):
        raise ValueError('Delivery output contains a link, reparse point or non-regular target')


def _windows_open(path,*,directory=False):
    # OPEN_REPARSE_POINT checks the object itself; no DELETE share keeps each
    # directory pinned while its children are inspected/created.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                     wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    create.restype=wintypes.HANDLE
    handle=create(str(path),0 if directory else 0x80000000,3 if directory else 1,None,3,
                  0x00200000|(0x02000000 if directory else 0),None)
    if handle==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:
        class AttributeTag(ctypes.Structure):
            _fields_=[('attributes',wintypes.DWORD),('tag',wintypes.DWORD)]
        info=AttributeTag()
        query=kernel.GetFileInformationByHandleEx
        query.argtypes=(wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD)
        query.restype=wintypes.BOOL
        if not query(handle,9,ctypes.byref(info),ctypes.sizeof(info)):raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes&0x400 or bool(info.attributes&0x10)!=directory:
            raise ValueError('Delivery output contains a reparse point or wrong object type')
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
        handle=None
        return fd
    finally:
        if handle is not None:
            close=kernel.CloseHandle;close.argtypes=(wintypes.HANDLE,);close(handle)


class _Directory:
    def __init__(self,path,parent=None):
        self.path=Path(path)
        self.fd=None
        try:
            if parent is not None:
                try:
                    if os.name=='nt':self.path.mkdir(mode=0o700)
                    else:os.mkdir(self.path.name,0o700,dir_fd=parent.fd)
                except FileExistsError:pass
            if os.name=='nt':self.fd=_windows_open(self.path,directory=True)
            else:self.fd=os.open(self.path if parent is None else self.path.name,
                                 os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,
                                 **({} if parent is None else {'dir_fd':parent.fd}))
            if os.name=='nt':restrict(self.path,directory=True)
            else:
                _safe_stat(os.fstat(self.fd),directory=True)
                os.fchmod(self.fd,0o700)
        except BaseException:
            self.close();raise

    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None

    def __enter__(self):return self
    def __exit__(self,*_):self.close()

    def metadata(self,name):
        try:return os.lstat(self.path/name) if os.name=='nt' else os.stat(name,dir_fd=self.fd,follow_symlinks=False)
        except FileNotFoundError:return None

    def read(self,name):
        try:
            fd=_windows_open(self.path/name) if os.name=='nt' else os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=self.fd)
        except FileNotFoundError:return None
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno());_safe_stat(before)
            data=stream.read()
            after=os.fstat(stream.fileno());_safe_stat(after)
            current=self.metadata(name)
            if (current is None or (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino)
                    or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns)):
                raise ValueError('Delivery target changed while being checked')
            _safe_stat(current)
            if os.name=='nt':restrict(self.path/name)
            else:os.fchmod(stream.fileno(),0o600)
            return data

    def names(self):return os.listdir(self.path if os.name=='nt' else self.fd)

    def verify_location(self):
        current=os.lstat(self.path);_safe_stat(current,directory=True)
        if os.name!='nt':
            opened=os.fstat(self.fd)
            if (current.st_dev,current.st_ino)!=(opened.st_dev,opened.st_ino):
                raise ValueError('Delivery directory changed while saving files')

    def unlink(self,name):
        if os.name=='nt':os.unlink(self.path/name)
        else:os.unlink(name,dir_fd=self.fd)

    def commit(self,name,data):
        temporary='.delivery-'+uuid.uuid4().hex+'.tmp'
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
        fd=os.open(self.path/temporary if os.name=='nt' else temporary,flags,0o600,
                   **({} if os.name=='nt' else {'dir_fd':self.fd}))
        try:
            with os.fdopen(fd,'wb') as stream:
                if os.name=='nt':restrict(self.path/temporary)
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            try:
                if os.name=='nt':os.rename(self.path/temporary,self.path/name)
                else:os.link(temporary,name,src_dir_fd=self.fd,dst_dir_fd=self.fd,follow_symlinks=False)
            except FileExistsError:
                if self.read(name)!=data:raise ValueError('Existing delivery output differs from the original bytes')
                return True
            finally:
                if self.metadata(temporary) is not None:self.unlink(temporary)
            if os.name!='nt':os.fsync(self.fd)
            return False
        finally:
            if self.metadata(temporary) is not None:self.unlink(temporary)


def save_delivery(home,reference,period,files):
    """All input bytes have already passed original card/manifest validation."""
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',reference) or type(period) is not int or period<1:
        raise ValueError('Invalid delivery output identity')
    if not isinstance(files,dict) or not files or any(not isinstance(blob,bytes) for blob in files.values()):
        raise ValueError('Invalid delivery byte map')
    safe_names(files)
    home=Path(home).absolute()
    with ExitStack() as stack:
        folder=stack.enter_context(_Directory(home));directories=[folder]
        for name in ('deliveries',reference[7:],str(period)):
            folder=stack.enter_context(_Directory(folder.path/name,folder))
            directories.append(folder)
        actual=folder.names()
        if os.name=='nt':
            for name in files:
                if any(other!=name and other.casefold()==name.casefold() for other in actual):
                    raise ValueError('Existing delivery filename collides with the original name')
        present={}
        # Check every target before the first byte is emitted. In particular an
        # invalid second target must not produce a partial first output.
        for name,blob in files.items():
            metadata=folder.metadata(name)
            if metadata is not None:_safe_stat(metadata)
            saved=folder.read(name)
            if saved is not None and saved!=blob:raise ValueError('Existing delivery output differs from the original bytes')
            present[name]=saved is not None
        for name,blob in files.items():
            for directory in directories:directory.verify_location()
            if not present[name]:present[name]=folder.commit(name,blob)
        for name,blob in files.items():
            if folder.read(name)!=blob:raise ValueError('Delivery output failed final byte verification')
        for directory in directories:directory.verify_location()
        return {name:{'digest':digest_bytes(blob),'size':len(blob),'path':str(folder.path/name),
                      'alreadyPresent':present[name]} for name,blob in files.items()}
