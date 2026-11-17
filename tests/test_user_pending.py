"""P36 background reminder over the original confirmation rows; only the OS browser launch is a double."""
import json
import threading
import time
import uuid

import pytest

from conftest import wait_for
from ioalab_sdk.runtime import RuntimeFault, RuntimeStore
from ioalab_sdk.runtime.confirmations import Confirmations
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.ipc import IpcServer, LocalClient
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.user_pending import UserPending
from ioalab_sdk.runtime.user_ui import UserUi
from test_resident_runtime import configuration


@pytest.fixture
def confirmations(tmp_path):
    store = RuntimeStore(tmp_path / 'runtime.sqlite', 'http://local', ['actor'], owner_id='owner')
    yield Confirmations(store)
    store.close()


def binding(actor='actor'):
    return {'actor': actor, 'operationId': str(uuid.uuid4()), 'subject': {'digest': 'sha256:' + 'a' * 64},
            'stage': 'rules.publish', 'action': 'interface.publish', 'preview': {'title': '后台创建的待确认事项', 'tools': []}}


class Opener:
    """Explicit OS-launch boundary: records request times, optionally failing first."""
    def __init__(self, failures=0):
        self.calls, self.failures, self.lock = [], failures, threading.Lock()

    def __call__(self):
        with self.lock:
            self.calls.append(time.monotonic())
            if len(self.calls) <= self.failures:
                raise RuntimeFault('not_ready', 'The local user browser could not be opened', 'configure_browser')
        return True

    def count(self):
        with self.lock:
            return len(self.calls)


def reminder(confirmations, opener, **timing):
    values = {'coalesce': .1, 'interval': 1., 'retry': 2., 'poll': .05, **timing}
    return UserPending(confirmations, opener, **values).start()


def test_new_pending_rows_coalesce_into_one_open_and_respect_the_interval(confirmations):
    opener = Opener()
    pending = reminder(confirmations, opener)
    try:
        refs = [confirmations.ensure(binding())['reference'] for _ in range(3)]
        wait_for(lambda: opener.count() == 1, timeout=3)
        time.sleep(.3)
        assert opener.count() == 1
        refs.append(confirmations.ensure(binding())['reference'])
        time.sleep(.3)
        assert opener.count() == 1  # merged into the next open after the interval
        wait_for(lambda: opener.count() == 2, timeout=3)
        assert opener.calls[1] - opener.calls[0] >= 1.
        time.sleep(.3)
        assert opener.count() == 2
        assert all(confirmations.get(ref)['status'] == 'pending' for ref in refs)
        assert pending.status() == {'pendingCount': 4, 'lastAttempt': {'status': 'open_requested'}}
    finally:
        pending.close()


def test_failed_open_keeps_pending_and_retries_after_the_retry_delay(confirmations):
    opener = Opener(failures=1)
    pending = reminder(confirmations, opener)
    try:
        ref = confirmations.ensure(binding())['reference']
        wait_for(lambda: opener.count() == 1, timeout=3)
        assert pending.status() == {'pendingCount': 1, 'lastAttempt': {'status': 'failed', 'code': 'not_ready'}}
        time.sleep(1.3)
        assert opener.count() == 1  # not even the 1s open interval; failures wait for retry
        wait_for(lambda: opener.count() == 2, timeout=3)
        assert opener.calls[1] - opener.calls[0] >= 2.
        assert confirmations.get(ref)['status'] == 'pending'
        status = pending.status()
        assert status['lastAttempt'] == {'status': 'open_requested'}
        assert ref not in json.dumps(status) and '/user' not in json.dumps(status)
    finally:
        pending.close()


def test_restart_reminds_once_for_rows_that_are_still_pending(confirmations):
    decided = confirmations.ensure(binding())
    confirmations.decide(decided['reference'], decided['bindingDigest'], 'approve',
                         authority=confirmations.user_channel(), source='isolated-user-event')
    quiet = Opener()
    pending = reminder(confirmations, quiet)
    time.sleep(.4)
    pending.close()
    assert quiet.count() == 0  # decided rows never trigger a reminder
    confirmations.ensure(binding())
    for _ in range(2):  # each new process (restart) reminds once, then stays quiet
        opener = Opener()
        pending = reminder(confirmations, opener)
        try:
            wait_for(lambda: opener.count() == 1, timeout=3)
            time.sleep(1.3)
            assert opener.count() == 1
        finally:
            pending.close()


def test_manual_open_is_not_rate_limited_and_covers_rows_already_shown(confirmations, tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launched.append(url) or True)
    ui = UserUi(confirmations)
    server = IpcServer(tmp_path, 'pending-reminder', lambda *_: {}, user_ui=ui).start()
    ui.reminders = UserPending(confirmations, lambda: ui.open(), coalesce=.5, interval=.5, retry=1., poll=.05).start()
    try:
        confirmations.ensure(binding())
        assert ui.open() is True and len(launched) == 1
        time.sleep(1.2)
        assert len(launched) == 1  # the manual page already showed it
        assert ui.open() is True and len(launched) == 2  # manual opens are not limited
        confirmations.ensure(binding())
        wait_for(lambda: len(launched) == 3, timeout=3)
        assert all(url.startswith(ui.origin + '/user#') for url in launched)
    finally:
        ui.reminders.close()
        server.close()
    confirmations.ensure(binding())
    time.sleep(.8)
    assert len(launched) == 3  # stopped and joined: no opening after close


def test_resident_reminds_background_confirmations_and_restart_once(platform, tmp_path, monkeypatch):
    path, _ = configuration(platform, tmp_path, service=False)
    for restart in (False, True):
        runtime = ResidentRuntime(RuntimeConfig.load(path)).start()
        opened = []
        monkeypatch.setattr(runtime.user_ui, 'open', lambda: opened.append(True) or True)
        try:
            if not restart:
                # Created directly like a background job/recovery, with no model open_ui call.
                identity = runtime.identities[0]
                reference = runtime.confirmations.ensure(binding(identity.agent_id))['reference']
            wait_for(lambda: opened, timeout=5)
            state = LocalClient(runtime.config.home).call('runtime.status')['data']['userPending']
            assert state == {'pendingCount': 1, 'lastAttempt': {'status': 'open_requested'}}
            assert runtime.confirmations.get(reference)['status'] == 'pending'
            time.sleep(.5)
            assert len(opened) == 1
        finally:
            runtime.close()
