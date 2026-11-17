"""Codex-owned explicit launch receipt; no model, daemon or second writer.

The OS ancestry walk uses limited process image/time queries, never command lines.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
from ioalab_sdk.runtime import RuntimeFault, private_json, restrict

def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')


def launch_digest(capture):
    if (not isinstance(capture,dict) or set(capture)!={'format','projectDirectory','cli','configDirectoryState','configDirectory','extensionDirectory'} or
        capture['format']!='ioalab.codex-launch/1'):
        raise RuntimeFault('invalid_input','Use the fixed native launch capture')
    for key in ('projectDirectory','cli','extensionDirectory'):
        value=capture[key]
        if not isinstance(value,str) or not 1<=len(value)<=8192 or not Path(value).is_absolute():
            raise RuntimeFault('invalid_input','Use actual absolute native launch locations')
    state,config=capture['configDirectoryState'],capture['configDirectory']
    if state=='unset':
        if config is not None:raise RuntimeFault('invalid_input','The unset native directory must remain unset')
    elif state=='set':
        if not isinstance(config,str) or not config or len(config)>8192 or not Path(config).is_absolute():
            raise RuntimeFault('invalid_input','Use the actual native configuration directory pointer')
    else:raise RuntimeFault('invalid_input','Use an explicit native directory state')
    return hashlib.sha256(_canonical(capture)).hexdigest()


class NativeLaunchStore:
    """One launch selection, not a second account/configuration authority."""
    def __init__(self,*,host_ref,home):
        self.host_ref,self.home=host_ref,Path(home)
        self.path=self.home/'launch-context.json'
        self.lock=threading.RLock()
        self.entry_control=None

    def bind_entry_control(self,callback):
        if not callable(callback):raise RuntimeFault('invalid_input','Bind the SDK setup delegate')
        with self.lock:
            if self.entry_control is not None and self.entry_control is not callback:
                raise RuntimeFault('identity_conflict','Preserve the original SDK setup delegate')
            self.entry_control=callback

    @contextmanager
    def _owned_lock(self):
        with self.lock:
            if any(path.is_symlink() for path in (self.home,*self.home.parents)):
                raise RuntimeFault('forbidden','Native launch storage must remain inside its owned directory')
            self.home.mkdir(mode=0o700,parents=True,exist_ok=True)
            restrict(self.home,directory=True)
            lock_path=self.home/'launch-context.lock'
            if lock_path.is_symlink():raise RuntimeFault('forbidden','Restore the original launch lock')
            with lock_path.open('a+b') as stream:
                restrict(lock_path)
                if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
                stream.seek(0)
                try:
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError:
                    raise RuntimeFault('busy','The original native launch save is still executing') from None
                try:yield
                finally:
                    stream.seek(0)
                    if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                    else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def read(self):
        if not self.path.exists():return None
        if self.path.is_symlink():raise RuntimeFault('forbidden','Restore the original native launch receipt')
        try:record=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise RuntimeFault('not_ready','Restore the original native launch receipt','restore_native_launch_receipt') from None
        try:
            if (set(record)!={'format','hostRef','authority','capture','claim','savedReceiptRef'} or
                record['format']!='ioalab.codex-launch-receipt/1' or record['hostRef']!=self.host_ref or
                set(record['authority'])!={'hostRef','adapterId','installationRef','accountBindingRef'} or
                record['authority']['hostRef']!=self.host_ref or record['authority']['adapterId']!='codex' or
                set(record['claim'])!={'operationId','claimRef','targetDigest'} or
                record['claim']['targetDigest']!=launch_digest(record['capture']) or
                any(not isinstance(value,str) or not value for value in record['claim'].values())):raise ValueError()
            original={key:value for key,value in record.items() if key!='savedReceiptRef'}
            if record['savedReceiptRef']!='codex-launch:'+hashlib.sha256(_canonical(original)).hexdigest():raise ValueError()
        except (ValueError,TypeError,KeyError):raise RuntimeFault('identity_conflict','The original native launch receipt changed') from None
        return record

    def _ack(self,scope,record):
        if any(record['authority'][key]!=scope.get(key) for key in ('hostRef','adapterId','accountBindingRef')):
            raise RuntimeFault('identity_conflict','The native launch receipt belongs to another account or host')
        if self.entry_control is None:raise RuntimeFault('not_ready','Bind the original SDK setup delegate','bind_native_entry_control')
        return self.entry_control(scope,{'purpose':'sdk.entry','action':'complete',
            'claimRef':record['claim']['claimRef'],'targetDigest':record['claim']['targetDigest'],
            'savedReceiptRef':record['savedReceiptRef']})

    def recover(self,scope):
        record=self.read()
        return None if record is None else self._ack(scope,record)

    def capture(self,scope,*,entry_ref,capture,workspace):
        digest=launch_digest(capture)
        if Path(capture['projectDirectory']).resolve()!=Path(workspace).resolve():
            raise RuntimeFault('identity_conflict','The capture must use the actual native API workspace')
        if self.entry_control is None:raise RuntimeFault('not_ready','Bind the original SDK setup delegate','bind_native_entry_control')
        with self._owned_lock():
            original=self.read()
            if original is not None:return self._ack(scope,original)
            pending=self.entry_control(scope,{'purpose':'sdk.entry','action':'pending','entryRef':entry_ref})
            if pending is None:raise RuntimeFault('not_ready','Use the original explicit SDK setup operation','recover_original_native_setup')
            if not isinstance(pending,dict) or set(pending)!={'operationId','intentRef'}:
                raise RuntimeFault('identity_conflict','Use the original SDK setup intent')
            claim=self.entry_control(scope,{'purpose':'sdk.entry','action':'claim','entryRef':entry_ref,
                'intentRef':pending['intentRef'],'targetDigest':digest})
            if (not isinstance(claim,dict) or set(claim)!={'operationId','claimRef','targetDigest'} or
                claim['operationId']!=pending['operationId'] or claim['targetDigest']!=digest or
                not isinstance(claim['claimRef'],str) or not claim['claimRef']):
                raise RuntimeFault('identity_conflict','The SDK did not grant the original native target')
            record={'format':'ioalab.codex-launch-receipt/1','hostRef':self.host_ref,
                'authority':{key:scope[key] for key in ('hostRef','adapterId','installationRef','accountBindingRef')},
                'capture':capture,'claim':claim}
            record['savedReceiptRef']='codex-launch:'+hashlib.sha256(_canonical(record)).hexdigest()
            private_json(self.path,record)
        # No SDK transaction spans the OS save; ACK never grants a new write.
        return self._ack(scope,record)


def native_cli_from_ancestors(ancestors):
    previous=None
    seen=set()
    for item in ancestors:
        pid,parent,created,image=item['pid'],item['parent'],item['created'],item['image']
        if (pid in seen or not isinstance(pid,int) or pid<=0 or not isinstance(parent,int) or
            not isinstance(created,int) or created<=0 or previous is not None and
            (previous['parent']!=pid or created>previous['created'])):
            raise RuntimeFault('not_ready','The native helper ancestor generation could not be verified','capture_original_native_cli')
        seen.add(pid)
        # The first row is this helper, not the native parent. Never substitute
        # an unrelated PATH executable or assume a direct parent is Claude.
        if previous is not None and isinstance(image,str) and Path(image).name.lower()=='codex.exe' and Path(image).is_absolute():
            return image
        previous=item
    raise RuntimeFault('not_ready','The original running native CLI image is unavailable','capture_original_native_cli')


def windows_native_ancestors():
    if os.name!='nt':raise RuntimeFault('not_ready','Native CLI ancestor capture is not implemented on this system','capture_original_native_cli')
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class ProcessEntry(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),
            ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
            ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype=wintypes.BOOL
    kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
    kernel.Process32NextW.restype=wintypes.BOOL
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype=wintypes.BOOL
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    kernel.GetProcessTimes.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    pid=os.getpid();seen=set()
    for _ in range(32):
        if not pid or pid in seen:return
        seen.add(pid)
        snapshot=kernel.CreateToolhelp32Snapshot(2,0)
        if snapshot in (None,ctypes.c_void_p(-1).value):
            raise RuntimeFault('not_ready','Cannot read the original native helper ancestor','capture_original_native_cli')
        parent=None
        try:
            entry=ProcessEntry();entry.dwSize=ctypes.sizeof(entry)
            found=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
            while found:
                if entry.th32ProcessID==pid:parent=int(entry.th32ParentProcessID);break
                found=kernel.Process32NextW(snapshot,ctypes.byref(entry))
        finally:kernel.CloseHandle(snapshot)
        process=kernel.OpenProcess(0x1000,False,pid)
        if parent is None or not process:
            raise RuntimeFault('not_ready','The original native helper ancestor ended or is unreadable','capture_original_native_cli')
        try:
            size=wintypes.DWORD(32768);image=ctypes.create_unicode_buffer(size.value)
            created,exit_time,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
            if (not kernel.QueryFullProcessImageNameW(process,0,image,ctypes.byref(size)) or
                not kernel.GetProcessTimes(process,ctypes.byref(created),ctypes.byref(exit_time),ctypes.byref(kernel_time),ctypes.byref(user_time))):
                raise RuntimeFault('not_ready','The original native helper generation is unreadable','capture_original_native_cli')
            item={'pid':pid,'parent':parent,'created':(created.dwHighDateTime<<32)|created.dwLowDateTime,'image':image.value}
        finally:kernel.CloseHandle(process)
        yield item
        pid=parent
