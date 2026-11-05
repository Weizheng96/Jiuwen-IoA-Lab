"""Native Windows ownership checks for a trusted persistent local deployment."""
import ctypes
from ctypes import wintypes

from ioalab_sdk.runtime.files import windows_sid
from ioalab_sdk.runtime.protocol import RuntimeFault


def owned(path):
    """Require this user's owner SID and no grants beyond this user and SYSTEM.

    Reading the ACL never repairs/adopts an existing file. SDK private writers
    remain responsible for protecting newly created participant state.
    """
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    security.GetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                                             ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer),
                                             pointer, ctypes.POINTER(pointer))
    security.ConvertSidToStringSidW.argtypes = (pointer, ctypes.POINTER(pointer))
    security.GetAce.argtypes = (pointer, wintypes.DWORD, ctypes.POINTER(pointer))
    kernel.LocalFree.argtypes = (pointer,)
    owner, acl, descriptor = pointer(), pointer(), pointer()
    result = security.GetNamedSecurityInfoW(str(path), 1, 0x1 | 0x4, ctypes.byref(owner), None,
                                           ctypes.byref(acl), None, ctypes.byref(descriptor))
    def reject():
        raise RuntimeFault('forbidden', 'Windows deployment data must belong only to its local user and SYSTEM')
    def sid(value):
        output = pointer()
        if not security.ConvertSidToStringSidW(value, ctypes.byref(output)):
            reject()
        try:
            return ctypes.wstring_at(output)
        finally:
            kernel.LocalFree(output)
    try:
        if result or not owner or not acl:
            reject()
        current = windows_sid()
        if sid(owner) != current:
            reject()
        # ACL header: revision byte, reserved byte, size ushort, count ushort.
        count = ctypes.c_ushort.from_address(acl.value + 4).value
        granted = False
        for index in range(count):
            ace = pointer()
            if not security.GetAce(acl, index, ctypes.byref(ace)):
                reject()
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind == 1:  # ACCESS_DENIED_ACE does not grant access.
                continue
            if kind != 0:  # Unknown/object/callback grants are not accepted.
                reject()
            mask = ctypes.c_ulong.from_address(ace.value + 4).value
            target = sid(pointer(ace.value + 8))
            if mask and target not in (current, 'S-1-5-18'):
                reject()
            granted = granted or bool(mask and target == current)
        if not granted:
            reject()
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
