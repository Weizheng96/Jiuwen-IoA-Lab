"""Private setup control on the original SDK IPC; never a business transport."""
from pathlib import Path
import os
import threading
import time
from ioalab_sdk.runtime import RuntimeFault
from .native_process import NativeLaunchStore, native_cli_from_ancestors, windows_native_ancestors
from .desktop_context import DesktopContextStore


def capture(workspace, extension):
    """Only called after the original explicit SDK setup intent exists."""
    home = os.environ.get('CODEX_HOME')
    value = {'format': 'ioalab.codex-launch/1', 'projectDirectory': workspace,
             'cli': native_cli_from_ancestors(windows_native_ancestors()),
             'configDirectoryState': 'unset' if home is None else 'set', 'configDirectory': home,
             'extensionDirectory': str(Path(extension).resolve())}
    from .native_process import launch_digest
    launch_digest(value)
    return value


class NativeSetup:
    def __init__(self, *, host_ref, home, store=None, companion=None):
        self.host_ref = host_ref
        self.store = store or NativeLaunchStore(host_ref=host_ref, home=home)
        self.desktop_context = DesktopContextStore(self.store.home.parent)
        self.companion = companion  # Route A desktop submissions share this resource lifecycle.
        self.condition = threading.Condition(threading.RLock())
        self.closed, self.calls = False, 0

    def bind_entry_control(self, callback):
        self.store.bind_entry_control(callback)

    def start(self):
        pass  # SDK IPC owns the only listener; setup has no process or poller.

    def exchange(self, scope, body):
        if not isinstance(scope, dict) or scope.get('hostRef') != self.host_ref or scope.get('adapterId') != 'codex':
            raise RuntimeFault('identity_conflict', 'Use the original authenticated Codex control')
        if not isinstance(body, dict) or body.get('kind') not in ('entry_recover', 'entry_setup', 'entry_desktop'):
            raise RuntimeFault('invalid_input', 'Use the fixed Codex setup control envelope')
        with self.condition:
            if self.closed:
                raise RuntimeFault('closed', 'The original setup resource is closing')
            self.calls += 1
        try:
            if body['kind'] == 'entry_desktop':
                if set(body) != {'kind', 'capture'}:
                    raise RuntimeFault('invalid_input', 'Use the fixed desktop connection capture')
                original = self.store.read()
                if original is None:
                    raise RuntimeFault('not_ready', 'Complete the original native setup before refreshing the desktop connection', 'run_native_setup')
                if any(original['authority'][key] != scope.get(key) for key in ('hostRef', 'adapterId', 'accountBindingRef')):
                    raise RuntimeFault('identity_conflict', 'The desktop connection belongs to another account or host')
                return self.desktop_context.save(body['capture'])
            if body['kind'] == 'entry_recover':
                if set(body) != {'kind'}:
                    raise RuntimeFault('invalid_input', 'Recover only the original saved claim')
                return self.store.recover(scope)
            if set(body) != {'kind', 'entryRef', 'capture', 'workspace'}:
                raise RuntimeFault('invalid_input', 'Use the fixed setup capture with the original entryRef')
            return self.store.capture(scope, entry_ref=body['entryRef'], capture=body['capture'], workspace=body['workspace'])
        finally:
            with self.condition:
                self.calls -= 1
                self.condition.notify_all()

    def drain(self, timeout):
        """Resident close: refuse new setup control, then wait for original calls; False keeps ownership."""
        started = time.monotonic()
        with self.condition:
            self.closed = True
            if not self.condition.wait_for(lambda: self.calls == 0, max(0, timeout)):
                return False
        return self.companion is None or self.companion.drain(max(0, timeout-(time.monotonic()-started)))

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.wait_for(lambda: self.calls == 0)
        if self.companion is not None:
            self.companion.close()
