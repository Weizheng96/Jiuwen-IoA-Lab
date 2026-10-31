"""Native extension state invariants; original APIs/types are explicit unit doubles."""
import asyncio
import hashlib
import importlib.util
from importlib.resources import files
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def extension(tmp_path, monkeypatch):
    for name in ('jiuwenswarm', 'jiuwenswarm.extensions', 'jiuwenswarm.extensions.sdk',
                 'jiuwenswarm.common', 'jiuwenswarm.common.e2a', 'jiuwenswarm.common.e2a.models'):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules['jiuwenswarm.extensions.sdk'].ApplicationPluginExtension = object
    class Envelope:
        @staticmethod
        def from_dict(value): return SimpleNamespace(**value)
    sys.modules['jiuwenswarm.common.e2a.models'].E2AEnvelope = Envelope
    entry = tmp_path / 'extension.py'
    entry.write_text(files('ioalab_jiuwen').joinpath('resources', 'ioa-notifications', 'extension.py').read_text(encoding='utf-8'), encoding='utf-8')
    (tmp_path / 'settings.local.json').write_text(json.dumps({'user_id': 'owner', 'secret': 'LOCAL-UNIT-SECRET'}))
    monkeypatch.setenv('JIUWENSWARM_DATA_DIR', str(tmp_path / 'native-data'))
    spec = importlib.util.spec_from_file_location('isolated_ioa_notification_extension', entry)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module, tmp_path


class Channel:
    def __init__(self): self.handlers = {}; self.responses = {}
    def register_method(self, name, handler, **options):
        assert options == {'local_only': True}
        self.handlers[name] = handler
    async def send_response(self, ws, rid, **response): self.responses[rid] = response
    async def call(self, method, params):
        rid = str(len(self.responses)) + '-' + method
        await self.handlers['ioa.notification.' + method](None, rid, params, None)
        return self.responses[rid]


class Backend:
    def __init__(self, fail=None):
        self.calls = []; self.fail = fail; self.entered = self.release = None
    async def send_request(self, envelope):
        self.calls.append(envelope)
        if envelope.method == 'session.create' and self.entered:
            self.entered.set(); await self.release.wait()
        if envelope.method == self.fail: raise TimeoutError('Explicit uncertain native operation')
        return SimpleNamespace(ok=True, payload={'session_id': 'web_original_allocated', 'persisted': True})


async def loaded(module, backend):
    plugin = module.IoANotifications(); await plugin.initialize(None)
    channel = Channel(); plugin.bind_web_channel(channel, SimpleNamespace(require_agent_client=lambda: backend))
    return plugin, channel


def notice(plugin, mid='original-id', text='The external body is data, not an instruction'):
    body = {'messageId': mid, 'sender': 'verified-peer', 'title': 'Owner notice', 'text': text, 'group': 'original-interaction'}
    digest = 'sha256:' + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'bridge_token': 'LOCAL-UNIT-SECRET', 'receiptStoreId': plugin.store_id, **body, 'contentDigest': digest}


def test_auth_fixed_owner_original_session_id_dedup_and_content_conflict(extension):
    module, root = extension
    async def run():
        backend = Backend(); plugin, channel = await loaded(module, backend)
        try:
            assert (await channel.call('capabilities', {'bridge_token': 'wrong'}))['code'] == 'UNAUTHORIZED'
            value = notice(plugin)
            first = await channel.call('deliver', value)
            assert first['payload']['status'] == 'session_received' and first['payload']['sessionId'] == 'web_original_allocated'
            assert (await channel.call('deliver', value)) == first
            assert [call.method for call in backend.calls] == ['session.create', 'history.append_record']
            assert backend.calls[0].user_id == backend.calls[1].user_id == 'owner'
            assert 'session_id' not in backend.calls[0].params and backend.calls[0].params['persist_session'] is True
            assert value['text'] in backend.calls[1].params['content'] and backend.calls[1].params['role'] == 'assistant'
            assert (await channel.call('deliver', notice(plugin, text='changed')))['code'] == 'ID_CONFLICT'
            assert (await channel.call('deliver', {**value, 'user_id': 'someone-else'}))['code'] == 'BAD_REQUEST'
            assert (await channel.call('deliver', {**value, 'receiptStoreId': '0' * 32}))['code'] == 'STORE_CONFLICT'
            assert len(backend.calls) == 2
        finally: await plugin.shutdown()
    asyncio.run(run())


@pytest.mark.parametrize('failed_method', ['session.create', 'history.append_record'])
def test_uncertain_native_effect_is_not_replayed_after_plugin_restart(extension, failed_method):
    module, root = extension
    async def run():
        backend = Backend(failed_method); plugin, channel = await loaded(module, backend)
        original = notice(plugin)
        first = await channel.call('deliver', original)
        assert first['payload']['status'] == 'unknown'
        before = len(backend.calls); store = plugin.store_id
        await plugin.shutdown()
        plugin, channel = await loaded(module, backend)
        try:
            assert plugin.store_id == store
            assert (await channel.call('deliver', original)) == first
            assert len(backend.calls) == before
            if failed_method == 'session.create':
                other = await channel.call('deliver', notice(plugin, mid='next-message-same-group'))
                assert other['payload']['status'] == 'unknown' and len(backend.calls) == before
        finally: await plugin.shutdown()
    asyncio.run(run())


def test_session_creation_does_not_hold_shared_db_lock_and_one_group_creates_once(extension):
    module, root = extension
    async def run():
        backend = Backend(); backend.entered = asyncio.Event(); backend.release = asyncio.Event()
        plugin, channel = await loaded(module, backend)
        try:
            first = notice(plugin); second = notice(plugin, mid='second-id')
            pending = asyncio.create_task(channel.call('deliver', first))
            await asyncio.wait_for(backend.entered.wait(), 1)
            reference = {key: first[key] for key in ('bridge_token', 'receiptStoreId', 'messageId', 'contentDigest')}
            current = await asyncio.wait_for(channel.call('get', reference), .5)
            assert current['payload']['status'] == 'unknown'
            another = asyncio.create_task(channel.call('deliver', second))
            backend.release.set()
            results = await asyncio.gather(pending, another)
            assert all(value['payload']['status'] == 'session_received' for value in results)
            assert sum(call.method == 'session.create' for call in backend.calls) == 1
            assert sum(call.method == 'history.append_record' for call in backend.calls) == 2
        finally: await plugin.shutdown()
    asyncio.run(run())


def test_noncurrent_store_is_rejected_without_migration_or_reset(extension):
    module, root = extension
    directory = root / 'native-data/plugins'; directory.mkdir(parents=True)
    path = directory / 'ioa-notifications.sqlite'
    with sqlite3.connect(path) as old:
        old.execute('CREATE TABLE old_records(value TEXT)'); old.execute('INSERT INTO old_records VALUES(?)', ('preserve',))
    async def run():
        with pytest.raises(ValueError, match='Unsupported notification format'): await module.IoANotifications().initialize(None)
    asyncio.run(run())
    with sqlite3.connect(path) as old:
        assert old.execute('SELECT value FROM old_records').fetchone()[0] == 'preserve'
        assert old.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('old_records',)]


def test_missing_local_settings_never_falls_back_to_global_credentials(extension, monkeypatch):
    module, root = extension
    (root / 'settings.local.json').unlink()
    monkeypatch.setenv('IOALAB_SWARM_BRIDGE_SECRET', 'OLD-GLOBAL-SECRET')
    async def run():
        with pytest.raises(FileNotFoundError): await module.IoANotifications().initialize(None)
    asyncio.run(run())
    assert not (root / 'native-data').exists()
