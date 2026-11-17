"""Route A desktop location and S02 health; reads process images, never command lines.

The setup receipt holds the actual native CLI image that ran the hook, i.e. the
desktop's own bundled server. Its install location identifies desktop server
processes of that installation and CODEX_HOME across desktop updates: for a
packaged app the package family below WindowsApps (version removed), otherwise
the directory above the versioned folder. Health is host-wide: a loaded or
unloaded target chat does not matter, and a busy chat is expressed by S01
queueing, never by S02.
"""
import os
from pathlib import Path
import re
import time

PRESENT = 'desktop server present'
ABSENT = 'desktop app not running; queued work cannot be consumed'


class EnumerationFailed(Exception):
    pass


_PACKAGE = re.compile(r'(?P<name>[^_]+)_(?P<version>[0-9.]+)_(?P<arch>[^_]*)__(?P<publisher>[a-z0-9]+)', re.I)


def _key(text):
    return os.path.normcase(os.path.normpath(text))


def install_location(cli):
    """Version-independent location of a desktop server image; ValueError if not one."""
    path = Path(cli)
    if not path.is_absolute() or path.name.lower() != 'codex.exe':
        raise ValueError('Use the actual native CLI image captured by setup')
    parts = path.parts
    for index in range(1, len(parts)):
        match = _PACKAGE.fullmatch(parts[index])
        if match and parts[index-1].lower() == 'windowsapps':
            return ('package', _key(str(Path(*parts[:index]))), match['name'].lower(), match['arch'].lower(),
                    match['publisher'].lower(), tuple(part.lower() for part in parts[index+1:]))
    return ('directory', _key(str(path.parent.parent)), 'codex.exe')


def desktop_servers(processes, location, *, owner=None):
    """Desktop server rows of ``location``; children of this adapter process are excluded."""
    owner = os.getpid() if owner is None else owner
    found = []
    for item in processes:
        image = item.get('image')
        if not isinstance(image, str) or not image or item.get('parent') == owner:
            continue
        try:
            if install_location(image) == location:
                found.append(item)
        except ValueError:
            continue
    return found


def current_cli(cli, processes, *, owner=None):
    """The captured image, or after a desktop update the running server of the same location."""
    if Path(cli).is_file():
        return cli
    running = desktop_servers(processes, install_location(cli), owner=owner)
    images = {item['image'] for item in running if Path(item['image']).is_file()}
    return images.pop() if len(images) == 1 else None


def windows_processes():
    """(pid, parent, image) for readable processes; a failed snapshot raises."""
    if os.name != 'nt':
        raise EnumerationFailed('Desktop enumeration is implemented for Windows only')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG), ('dwFlags', wintypes.DWORD),
                    ('szExeFile', wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise EnumerationFailed('Process snapshot is unavailable')
    rows = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            # Cheap name filter first; the full image is read only for codex.exe rows.
            if entry.szExeFile.lower() == 'codex.exe':
                rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    result = []
    for pid, parent in rows:
        process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            continue  # Ended or not readable: not evidence either way.
        try:
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(size)):
                result.append({'pid': pid, 'parent': parent, 'image': image.value})
        finally:
            kernel.CloseHandle(process)
    return result


class DesktopHealth:
    """Bounded S02 sampling; unavailable only after every sample confirms absence."""

    def __init__(self, locate, *, enumerate_processes=windows_processes, samples=3, interval=.5,
                 sleep=time.sleep, owner=None):
        self.locate, self.enumerate = locate, enumerate_processes
        self.samples, self.interval, self.sleep, self.owner = max(1, samples), interval, sleep, owner

    def check(self):
        try:
            cli = self.locate()
        except Exception:
            cli = None
        if cli is None:
            return {'state': 'unknown', 'evidence': 'Desktop install location is not captured by the original setup'}
        try:
            location = install_location(cli)
        except ValueError:
            return {'state': 'unknown', 'evidence': 'Captured native CLI is not a desktop server image'}
        for index in range(self.samples):
            try:
                rows = self.enumerate()
            except Exception:
                return {'state': 'unknown', 'evidence': 'Desktop server processes could not be enumerated'}
            if desktop_servers(rows, location, owner=self.owner):
                return {'state': 'available', 'evidence': PRESENT}
            if index+1 < self.samples:
                self.sleep(self.interval)
        return {'state': 'unavailable', 'evidence': ABSENT}
