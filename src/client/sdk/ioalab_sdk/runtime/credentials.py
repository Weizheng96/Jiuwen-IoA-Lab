"""Atomic credential records encrypted by a system-held key; no plaintext fallback."""
import hashlib
import json
import os
from pathlib import Path
import stat

from cryptography.fernet import Fernet, InvalidToken

from .files import private_text, restrict
from .lease import Lease
from .protocol import RuntimeFault


class CredentialVault:
    def __init__(self, home, binding):
        self.home = Path(home).resolve()
        self.binding = binding
        self.reference = hashlib.sha256(json.dumps([str(self.home), binding], sort_keys=True).encode()).hexdigest()
        self.path = self.home / 'credentials.enc'
        self.home.mkdir(parents=True, mode=0o700, exist_ok=True)
        restrict(self.home, directory=True)

    def lock(self):
        return Lease(self.home / 'credentials.lock')

    def _key(self):
        try:
            directory = os.environ.get('CREDENTIALS_DIRECTORY')
            if directory:
                # Explicit service-manager credential mount; never search config/data for keys.
                path = Path(directory) / 'ioalab-vault-key'
                resolved = path.resolve(strict=True)
                if path.is_symlink() or resolved.is_relative_to(self.home):
                    raise ValueError('Credential mount must be separate from client data')
                info = resolved.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size > 128:
                    raise ValueError('Invalid credential mount')
                if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                    raise ValueError('Credential mount must belong only to its owner')
                if os.name=='nt':private_windows_file(resolved)
                key = resolved.read_bytes().strip()
            else:
                import keyring
                backend = keyring.get_keyring()
                allowed = ('keyring.backends.macOS', 'keyring.backends.Windows',
                           'keyring.backends.SecretService', 'keyring.backends.kwallet')
                if type(backend).__module__ == 'keyring.backends.chainer':
                    backend = next((b for b in backend.backends if type(b).__module__ in allowed), None)
                if backend is None or type(backend).__module__ not in allowed or backend.priority <= 0:
                    raise ValueError('A supported system credential store is required')
                key = backend.get_password('IoALab.device-vault', self.reference)
                if key is None:
                    if self.path.exists():
                        raise ValueError('Original credential key is unavailable')
                    key = Fernet.generate_key().decode()
                    backend.set_password('IoALab.device-vault', self.reference, key)
                    if backend.get_password('IoALab.device-vault', self.reference) != key:
                        raise ValueError('Credential store did not retain the key')
                key = key.encode() if isinstance(key, str) else key
            return Fernet(key)
        except Exception:
            raise RuntimeFault('credential_store_unavailable',
                'Unlock/configure the system credential store or the service credential mount; original data is preserved',
                'configure_credential_store') from None

    def read(self):
        cipher = self._key()
        if not self.path.exists(): return {}
        try:
            if self.path.stat().st_size > 1024 * 1024:raise ValueError('Credential record too large')
            envelope = json.loads(cipher.decrypt(self.path.read_bytes()))
            if not isinstance(envelope.get('records'),dict):raise ValueError('Invalid credential record')
            if envelope['binding'] != self.binding or envelope['reference'] != self.reference:
                raise ValueError('Credential binding changed')
            return envelope['records']
        except (OSError, ValueError, KeyError, TypeError, InvalidToken):
            raise RuntimeFault('credential_store_unavailable', 'Original encrypted credentials cannot be opened; preserve them',
                               'restore_credential_store') from None

    def write(self, records):
        envelope = {'binding': self.binding, 'reference': self.reference, 'records': records}
        cipher = self._key()
        private_text(self.path, cipher.encrypt(json.dumps(envelope, ensure_ascii=False).encode()).decode())

    def update(self, **changes):
        with self.lock():
            records = self.read()
            for key, value in changes.items():
                if value is None: records.pop(key, None)
                else: records[key] = value
            self.write(records)
            return records


def private_windows_file(path):
    """Check an externally supplied key's owner and DACL without changing either."""
    import ctypes
    from ctypes import wintypes
    from .files import windows_sid
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    ptr=ctypes.c_void_p
    api.GetNamedSecurityInfoW.argtypes=(wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ptr),ptr,ctypes.POINTER(ptr),ptr,ctypes.POINTER(ptr))
    api.ConvertSidToStringSidW.argtypes=(ptr,ctypes.POINTER(ptr))
    api.GetAce.argtypes=(ptr,wintypes.DWORD,ctypes.POINTER(ptr))
    kernel.LocalFree.argtypes=(ptr,)
    owner,acl,descriptor=ptr(),ptr(),ptr()
    result=api.GetNamedSecurityInfoW(str(path),1,5,ctypes.byref(owner),None,ctypes.byref(acl),None,ctypes.byref(descriptor))
    def sid(pointer):
        output=ptr()
        if not api.ConvertSidToStringSidW(pointer,ctypes.byref(output)):raise ValueError('Invalid key owner')
        try:return ctypes.wstring_at(output)
        finally:kernel.LocalFree(output)
    try:
        current=windows_sid()
        if result or not owner or not acl or sid(owner)!=current:raise ValueError('Invalid credential ownership')
        granted=False
        for index in range(ctypes.c_ushort.from_address(acl.value+4).value):
            ace=ptr()
            if not api.GetAce(acl,index,ctypes.byref(ace)):raise ValueError('Invalid credential ACL')
            kind=ctypes.c_ubyte.from_address(ace.value).value
            if kind==1:continue
            if kind!=0:raise ValueError('Unsupported credential ACL')
            mask=ctypes.c_ulong.from_address(ace.value+4).value
            target=sid(ptr(ace.value+8))
            if mask and target not in (current,'S-1-5-18'):raise ValueError('Credential shared with another user')
            granted=granted or bool(mask and target==current)
        if not granted:raise ValueError('Credential is not readable by its owner')
    finally:
        if descriptor:kernel.LocalFree(descriptor)
