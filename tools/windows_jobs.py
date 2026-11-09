"""Windows job objects for development tools that launch and must later prove release of a process tree.

Used by tools/smoke.py on a Windows development machine; not a deployment path.
"""
import ctypes
from ctypes import wintypes


class WindowsJobs:
    """Kernel job membership for processes this development tool launched itself."""
    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        self.api.OpenJobObjectW.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.api.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        self.jobs = {}

    def resume(self, pid):
        class Thread(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('tid', wintypes.DWORD),
                        ('pid', wintypes.DWORD), ('base', wintypes.LONG), ('delta', wintypes.LONG),
                        ('flags', wintypes.DWORD)]
        self.api.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(Thread))
        self.api.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(Thread))
        self.api.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.api.OpenThread.restype = wintypes.HANDLE
        self.api.ResumeThread.argtypes = (wintypes.HANDLE,)
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise RuntimeError('Cannot inspect the suspended owned process')
        try:
            row = Thread(); row.size = ctypes.sizeof(row); tids = []
            more = self.api.Thread32First(snapshot, ctypes.byref(row))
            while more:
                if row.pid == pid: tids.append(row.tid)
                more = self.api.Thread32Next(snapshot, ctypes.byref(row))
            if len(tids) != 1:
                raise RuntimeError('Owned suspended process does not have its original single thread')
            thread = self.api.OpenThread(2, False, tids[0])
            if not thread:
                raise RuntimeError('Cannot resume the owned local component')
            try:
                if self.api.ResumeThread(thread) != 1:
                    raise RuntimeError('Owned process suspension state changed')
            finally:
                self.api.CloseHandle(thread)
        finally:
            self.api.CloseHandle(snapshot)

    def assign(self, name, pid):
        if name in self.jobs:
            raise RuntimeError('This component already has an owned Windows job')
        ctypes.set_last_error(0)
        job = self.api.CreateJobObjectW(None, name)
        if not job or ctypes.get_last_error() == 183:
            if job: self.api.CloseHandle(job)
            raise RuntimeError('Windows component job is already owned; no process was released')
        process = self.api.OpenProcess(0x100 | 0x1, False, pid)  # SET_QUOTA | TERMINATE, assign permission only
        try:
            if not process or not self.api.AssignProcessToJobObject(job, process):
                self.api.CloseHandle(job)
                raise RuntimeError('Cannot record all native descendants in an owned Windows job')
        finally:
            if process: self.api.CloseHandle(process)
        # No KILL_ON_JOB_CLOSE or limits: persistent local data and processes are
        # not a trial sandbox. A job is only reliable OS-provided membership.
        self.jobs[name] = job

    def members(self, name):
        job = self.jobs.get(name)
        if not job:
            job = self.api.OpenJobObjectW(0x4, False, name)  # QUERY
            if not job:
                raise RuntimeError('Original Windows job membership is unknown; no process is considered released')
            self.jobs[name] = job
        capacity = 64
        while capacity <= 4096:
            # JOBOBJECT_BASIC_PROCESS_ID_LIST has two DWORDs then ULONG_PTR[].
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            if self.api.QueryInformationJobObject(job, 3, buffer, len(buffer), None):
                count = ctypes.c_ulong.from_buffer(buffer, 4).value
                values = (ctypes.c_size_t * count).from_buffer(buffer, 8)
                return list(values)
            if ctypes.get_last_error() != 234:
                raise RuntimeError('Cannot verify all component job members')
            capacity *= 2
        raise RuntimeError('Too many native job members; preserve and inspect the instance')
