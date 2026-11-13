"""Owner-scoped native extension: original session/history APIs, own receipts only."""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from jiuwenswarm.extensions.sdk import ApplicationPluginExtension
from jiuwenswarm.common.e2a.models import E2AEnvelope


class IoANotifications(ApplicationPluginExtension):
    plugin_id = 'ioa-notifications'

    async def initialize(self, config):
        settings = Path(__file__).parent / 'settings.local.json'
        local = json.loads(settings.read_text(encoding='utf-8'))
        if (not isinstance(local, dict) or set(local) != {'user_id', 'secret'} or
            not isinstance(local['user_id'], str) or not 1 <= len(local['user_id']) <= 128 or
            not isinstance(local['secret'], str) or not local['secret']):
            raise ValueError('Configure the fixed owner and local notification credential')
        self.user, self.secret = local['user_id'], local['secret']
        self.lock, self.group_locks = asyncio.Lock(), {}
        root = Path(os.environ['JIUWENSWARM_DATA_DIR']) / 'plugins'
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        database = root / 'ioa-notifications.sqlite'
        fresh = not database.exists()
        self.db = sqlite3.connect(database, isolation_level=None)
        try:
            if not fresh and self.db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('Unsupported notification format; preserve the original store')
            self.db.execute('PRAGMA journal_mode=WAL')
            if fresh:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE receipts(id TEXT PRIMARY KEY,state TEXT NOT NULL,result TEXT,fingerprint TEXT NOT NULL)')
                self.db.execute('CREATE TABLE metadata(k TEXT PRIMARY KEY,v TEXT NOT NULL)')
                self.db.execute('CREATE TABLE groups(id TEXT PRIMARY KEY,state TEXT,session TEXT,token TEXT)')
                self.db.executemany('INSERT INTO metadata VALUES(?,?)', [('store', uuid.uuid4().hex), ('owner', self.user)])
                self.db.execute('PRAGMA user_version=1')
                self.db.execute('COMMIT')
            self.store_id = self.db.execute("SELECT v FROM metadata WHERE k='store'").fetchone()[0]
            if self.db.execute("SELECT v FROM metadata WHERE k='owner'").fetchone()[0] != self.user:
                raise ValueError('Restore the original notification owner before loading the extension')
        except BaseException:
            self.db.close()
            raise

    async def shutdown(self): self.db.close()

    def envelope(self, method, request_id, params, session=''):
        return E2AEnvelope.from_dict({'protocol_version': '1.0', 'request_id': request_id,
            'session_id': session, 'channel': 'web', 'method': method, 'identity_origin': 'service',
            'user_id': self.user, 'params': params})

    async def native_session(self, client, group, title):
        # A per-source lock orders its creation; SQLite/shared locks never span RPC.
        async with self.group_locks.setdefault(group, asyncio.Lock()):
            async with self.lock:
                old = self.db.execute('SELECT state,session,token FROM groups WHERE id=?', (group,)).fetchone()
                if old:
                    if old[0] == 'ready': return old[1]
                    raise RuntimeError('Native session creation requires reconciliation')
                token = uuid.uuid5(uuid.NAMESPACE_URL, self.user + '|' + group).hex
                self.db.execute('INSERT INTO groups VALUES(?,?,NULL,?)', (group, 'creating', token))
            try:
                response = await client.send_request(self.envelope('session.create', 'ioa-session-' + token,
                    {'create_token': token, 'persist_session': True, 'mode': 'agent', 'title': 'IoA通知 · ' + title}))
                if not response.ok: raise RuntimeError('Native session was not confirmed')
                sid = response.payload.get('session_id') or response.payload.get('sessionId')
                if not isinstance(sid, str) or not sid: raise RuntimeError('Native session identifier is missing')
                async with self.lock:
                    self.db.execute('UPDATE groups SET state=?,session=? WHERE id=?', ('ready', sid, group))
                return sid
            except Exception:
                async with self.lock: self.db.execute('UPDATE groups SET state=? WHERE id=?', ('unknown', group))
                raise

    def bind_web_channel(self, channel, services):
        client = services.require_agent_client()

        async def authorized(ws, rid, params):
            if (not self.secret or not isinstance(params, dict) or
                not hmac.compare_digest(str(params.get('bridge_token', '')).encode(), self.secret.encode())):
                await channel.send_response(ws, rid, ok=False, error='IoA bridge authorization required', code='UNAUTHORIZED')
                return False
            if params.get('receiptStoreId', self.store_id) != self.store_id:
                await channel.send_response(ws, rid, ok=False, error='Restore the original notification receipt store', code='STORE_CONFLICT')
                return False
            return True

        def receipt(mid, digest, state, **extra):
            return {'messageId': mid, 'contentDigest': digest, 'userId': self.user,
                    'receiptStoreId': self.store_id, 'status': state, 'read': 'unknown', **extra}

        def prior(mid, digest):
            old = self.db.execute('SELECT state,result,fingerprint FROM receipts WHERE id=?', (mid,)).fetchone()
            if not old: return receipt(mid, digest, 'absent')
            if old[2] != digest: raise ValueError('ID_CONFLICT')
            return json.loads(old[1]) if old[1] else receipt(mid, digest, 'unknown')

        async def conflict(ws, rid):
            await channel.send_response(ws, rid, ok=False, error='Original notification content changed', code='ID_CONFLICT')

        async def capabilities(ws, rid, params, session_id):
            if await authorized(ws, rid, params):
                await channel.send_response(ws, rid, ok=True, payload={
                    'protocol': 'ioa.web-notifications/1', 'userId': self.user, 'receiptStoreId': self.store_id})

        async def get(ws, rid, params, session_id):
            if not await authorized(ws, rid, params): return
            mid, digest = params.get('messageId'), params.get('contentDigest')
            if (set(params) - {'bridge_token', 'receiptStoreId', 'messageId', 'contentDigest'} or 'receiptStoreId' not in params or
                not isinstance(mid, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', mid) or
                not isinstance(digest, str) or not re.fullmatch('sha256:[a-f0-9]{64}', digest)):
                await channel.send_response(ws, rid, ok=False, error='Invalid original notification reference', code='BAD_REQUEST')
                return
            try:
                async with self.lock: result = prior(mid, digest)
            except ValueError: await conflict(ws, rid); return
            await channel.send_response(ws, rid, ok=True, payload=result)

        async def deliver(ws, rid, params, session_id):
            if not await authorized(ws, rid, params): return
            if set(params) != {'bridge_token', 'receiptStoreId', 'messageId', 'sender', 'title', 'text', 'group', 'contentDigest'}:
                await channel.send_response(ws, rid, ok=False, error='Only notification data is accepted', code='BAD_REQUEST'); return
            mid, title = params['messageId'], params['title']
            notice = {'messageId': mid, 'sender': params.get('sender'), 'title': title,
                      'text': params['text'], 'group': params['group']}
            limits = {'messageId': 64, 'sender': 256, 'title': 120, 'text': 32768, 'group': 512}
            if (any(not isinstance(notice[key], str) or not notice[key] or len(notice[key].encode()) > limit for key, limit in limits.items()) or
                not re.fullmatch('[A-Za-z0-9_-]{1,64}', mid) or any(char in title for char in '\r\n\x00')):
                await channel.send_response(ws, rid, ok=False, error='Invalid notification data', code='BAD_REQUEST'); return
            digest = 'sha256:' + hashlib.sha256(json.dumps(notice, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if params['contentDigest'] != digest:
                await channel.send_response(ws, rid, ok=False, error='Notification digest changed', code='BAD_REQUEST'); return
            try:
                async with self.lock:
                    old = prior(mid, digest)
                    if old['status'] == 'absent':
                        self.db.execute('INSERT INTO receipts(id,state,result,fingerprint) VALUES(?,?,NULL,?)', (mid, 'sending', digest))
            except ValueError: await conflict(ws, rid); return
            if old['status'] != 'absent':
                await channel.send_response(ws, rid, ok=True, payload=old); return
            result = receipt(mid, digest, 'unknown')
            try:
                sid = await self.native_session(client, notice['group'], title)
                params = {'session_id': sid, 'request_id': 'ioa-' + mid, 'role': 'assistant', 'event_type': 'chat.final',
                          'mode': 'agent', 'title': 'IoA通知 · ' + title,
                          'content': '【IoA通知 · 仅供阅读】\n来源：' + notice['sender'] + '\n通知编号：' + mid + '\n\n' + notice['text']}
                response = await client.send_request(self.envelope('history.append_record', 'ioa-notify-' + mid, params, sid))
                if not response.ok or not response.payload.get('persisted'): raise RuntimeError('Native history was not confirmed')
                result = receipt(mid, digest, 'session_received', sessionId=sid)
            except Exception: pass  # Original create/write may have happened; never replay it.
            async with self.lock:
                self.db.execute('UPDATE receipts SET state=?,result=? WHERE id=?', (result['status'], json.dumps(result, ensure_ascii=False), mid))
            await channel.send_response(ws, rid, ok=True, payload=result)

        for name, handler in [('capabilities', capabilities), ('get', get), ('deliver', deliver)]:
            channel.register_method('ioa.notification.' + name, handler, local_only=True)


async def register_extensions(registry):
    plugin = IoANotifications(); registry.register_application_plugin(plugin)
    return [plugin]
