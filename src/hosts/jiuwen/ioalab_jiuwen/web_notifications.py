"""One owner-scoped durable queue for the optional original Jiuwen web extension."""
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from dotenv import dotenv_values
from ioalab_sdk.lease import Lease
from ioalab_sdk.runtime.files import private_json, read_json, restrict
from ioalab_sdk.runtime.host import BusinessTools, ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault, encode
from .web_client import WebClient, NotSent, body, fingerprint


class WebNotifications:
    def __init__(self, options, *, home, profile, base_dir):
        allowed = {'url', 'user_id', 'secret_env', 'credentials_file', 'ttl_seconds', 'max_pending', 'retry_seconds'}
        if not isinstance(options, dict) or set(options) - allowed or not {'url', 'user_id', 'secret_env'}.issubset(options):
            raise RuntimeFault('invalid_input', 'Configure the fixed web target, owner and credential name')
        if not isinstance(profile, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile):
            raise RuntimeFault('invalid_input', 'Select an owned local profile')
        name = options['secret_env']
        if not isinstance(name, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', name):
            raise RuntimeFault('invalid_input', 'Use a credential environment variable name')
        secret = os.environ.get(name)
        if secret is None and options.get('credentials_file'):
            path = Path(options['credentials_file']).expanduser()
            if not path.is_absolute(): path = Path(base_dir) / path
            secret = dotenv_values(path, interpolate=False).get(name)
        self.client = WebClient(options['url'], options['user_id'], secret)
        self.ttl = options.get('ttl_seconds', 7 * 86400)
        self.limit = options.get('max_pending', 256)
        self.retry = options.get('retry_seconds', 5)
        for value, lower, upper in ((self.ttl, 60, 30 * 86400), (self.limit, 1, 4096), (self.retry, 1, 300)):
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise RuntimeFault('invalid_input', 'Notification limits are outside their declared range')
        self.root = Path(home) / 'roles' / profile / 'business' / 'web-notifications'
        self.target = {'url': self.client.url, 'userId': self.client.user}
        self.db = self.lease = self.thread = None
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.error = None
        self.checked = False

    def start(self):
        with self.lock:
            if self.db is not None: return
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            restrict(self.root, directory=True)
            self.lease = Lease(self.root / 'queue.lock', timeout=0)
            try:
                database, marker = self.root / 'notifications.sqlite', self.root / 'queue.json'
                if database.exists() != marker.exists():
                    raise RuntimeFault('not_ready', 'Restore the original notification database and marker', 'restore_notifications')
                fresh = not database.exists()
                self.db = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
                self.db.row_factory = sqlite3.Row
                self.db.execute('PRAGMA journal_mode=WAL')
                if fresh:
                    self.db.executescript('''
                        CREATE TABLE metadata(k TEXT PRIMARY KEY,v TEXT NOT NULL);
                        CREATE TABLE notices(id TEXT PRIMARY KEY,digest TEXT NOT NULL,body TEXT NOT NULL,
                            context TEXT NOT NULL,state TEXT NOT NULL,expires REAL NOT NULL,result TEXT);
                    ''')
                    identity = {'queueId': uuid.uuid4().hex, **self.target}
                    self.db.execute('INSERT INTO metadata VALUES(?,?)', ('identity', encode(identity)))
                    private_json(marker, identity)
                identity = json.loads(self.db.execute("SELECT v FROM metadata WHERE k='identity'").fetchone()[0])
                if identity != read_json(marker) or {key: identity.get(key) for key in self.target} != self.target:
                    raise RuntimeFault('not_ready', 'Restore the original notification target and queue', 'migrate_notifications')
                restrict(database)
                self.stop.clear()
                self.thread = threading.Thread(target=self._run, name='ioa-web-notifications', daemon=True)
                self.thread.start()
            except BaseException:
                if self.db is not None: self.db.close(); self.db = None
                self.lease.close(); self.lease = None
                raise

    @staticmethod
    def source(context):
        required = ('operationId', 'agentId', 'messageId', 'interactionId', 'peerAgentId')
        if not isinstance(context, dict) or any(not isinstance(context.get(key), str) or not 1 <= len(context[key]) <= 512 for key in required):
            raise RuntimeFault('invalid_input', 'A fixed SDK identity, source message and interaction are required')
        return {key: context[key] for key in required}

    def _require_started(self):
        if self.db is None or self.stop.is_set():
            raise RuntimeFault('not_ready', 'The owning local runtime has not started the notification resource')

    def _result(self, row):
        value = {'notificationRef': row['id'], 'status': row['state'], 'read': 'unknown'}
        if row['result']:
            receipt = json.loads(row['result'])
            if receipt.get('sessionId'): value['sessionId'] = receipt['sessionId']
        return value

    def notify(self, args, context):
        source = self.source(context)
        if not isinstance(args, dict) or set(args) - {'text', 'title'} or 'text' not in args:
            raise RuntimeFault('invalid_input', 'Provide notification text and an optional title')
        notice = body({'messageId': uuid.uuid5(uuid.NAMESPACE_URL, encode([source['agentId'], source['messageId']])).hex,
                       'sender': source['peerAgentId'], 'title': args.get('title', 'IoA留言'), 'text': args['text'],
                       'group': uuid.uuid5(uuid.NAMESPACE_URL, encode([source['agentId'], source['interactionId']])).hex})
        signature = fingerprint(notice)
        scope = {key: source[key] for key in ('agentId', 'messageId', 'interactionId', 'peerAgentId')}
        with self.lock:
            self._require_started()
            old = self.db.execute('SELECT * FROM notices WHERE id=?', (notice['messageId'],)).fetchone()
            if old:
                if old['digest'] != signature or json.loads(old['context']) != scope:
                    raise RuntimeFault('id_conflict', 'Original notification content or source changed')
                return self._result(old)
            self._expire()
            count = self.db.execute("SELECT COUNT(*) FROM notices WHERE state IN ('queued','sending','unknown')").fetchone()[0]
            state = 'queued' if count < self.limit else 'rejected'
            self.db.execute('INSERT INTO notices VALUES(?,?,?,?,?,?,NULL)',
                            (notice['messageId'], signature, encode(notice), encode(scope), state, time.time() + self.ttl))
            row = self.db.execute('SELECT * FROM notices WHERE id=?', (notice['messageId'],)).fetchone()
            self.wake.set()
            return self._result(row)

    def query(self, args, context):
        source = self.source(context)
        if not isinstance(args, dict) or set(args) != {'notificationRef'} or not isinstance(args['notificationRef'], str):
            raise RuntimeFault('invalid_input', 'Use the original notificationRef')
        with self.lock:
            self._require_started()
            row = self.db.execute('SELECT * FROM notices WHERE id=?', (args['notificationRef'],)).fetchone()
            if row is None or any(json.loads(row['context']).get(key) != source[key] for key in ('agentId', 'interactionId', 'peerAgentId')):
                raise RuntimeFault('forbidden', 'Notification does not belong to this identity and interaction')
            return self._result(row)

    def status(self):
        with self.lock:
            counts = {} if self.db is None else {row[0]: row[1] for row in self.db.execute('SELECT state,COUNT(*) FROM notices GROUP BY state')}
            return {'state': 'running' if self.thread and self.thread.is_alive() else 'not_started',
                    'ready': self.checked and not self.stop.is_set() and self.error is None, 'counts': counts, 'error': self.error}

    def _expire(self):
        self.db.execute("UPDATE notices SET state='expired' WHERE state='queued' AND expires<=?", (time.time(),))

    def _save(self, identifier, state, result=None):
        with self.lock:
            self.db.execute('UPDATE notices SET state=?,result=? WHERE id=?', (state, encode(result) if result else None, identifier))

    def _send(self, row, notice):
        self._save(row['id'], 'sending')
        try:
            receipt = self.client.deliver(notice)
            if receipt['status'] not in ('session_received', 'unknown'):
                raise RuntimeFault('protocol_error', 'A write cannot return an absent receipt')
            self._save(row['id'], receipt['status'], receipt)
        except NotSent:
            # A new connection failure cannot erase an earlier uncertain attempt.
            if row['state'] == 'queued': self._save(row['id'], 'queued')
            raise

    def _tick(self):
        with self.lock:
            self._expire()
            rows = self.db.execute("SELECT * FROM notices WHERE state IN ('queued','sending','unknown') ORDER BY rowid LIMIT ?", (self.limit,)).fetchall()
        remote_store = self.client.probe()
        with self.lock:
            saved = self.db.execute("SELECT v FROM metadata WHERE k='receiptStoreId'").fetchone()
            if saved and saved[0] != remote_store:
                raise RuntimeFault('receipt_store_changed', 'Restore and reconcile the original notification receipt store')
            if not saved: self.db.execute('INSERT INTO metadata VALUES(?,?)', ('receiptStoreId', remote_store))
            self.client.store_id = remote_store
            self.checked = True
        for row in rows:
            if self.stop.is_set(): return
            notice = json.loads(row['body'])
            if row['state'] == 'queued': self._send(row, notice); continue
            receipt = self.client.get(notice)
            if receipt['status'] != 'absent': self._save(row['id'], receipt['status'], receipt)
            elif row['state'] == 'sending' and row['expires'] > time.time(): self._send(row, notice)
            elif row['state'] == 'sending': self._save(row['id'], 'unknown')
        with self.lock: self.error = None

    def _run(self):
        while not self.stop.is_set():
            self.wake.clear()
            try: self._tick()
            except Exception as exc:
                with self.lock:
                    self.checked = False
                    self.error = exc.code if isinstance(exc, RuntimeFault) else ('offline' if isinstance(exc, NotSent) else type(exc).__name__)
            self.wake.wait(self.retry)

    def close(self):
        self.stop.set(); self.wake.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive(): raise RuntimeFault('not_ready', 'Wait for the original web notification operation to stop')
        with self.lock:
            if self.db is not None: self.db.close(); self.db = None
            if self.lease is not None: self.lease.close(); self.lease = None
            self.thread = None


def factory(options, *, home, profile, base_dir):
    queue = WebNotifications(options, home=home, profile=profile, base_dir=base_dir)
    schema = {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 32768},
                                             'title': {'type': 'string', 'minLength': 1, 'maxLength': 120}},
              'required': ['text'], 'additionalProperties': False}
    query = {'type': 'object', 'properties': {'notificationRef': {'type': 'string', 'minLength': 1, 'maxLength': 64}},
             'required': ['notificationRef'], 'additionalProperties': False}
    return BusinessTools((
        ToolBinding('owner.notify', '把已获准内容排队转给主人九问网页。queued仅表示排队；session_received表示会话接收，均不表示主人已读。', schema, queue.notify, 'notification', '1'),
        ToolBinding('owner.notification_status', '查询本交互原通知状态，不重发，也不推断用户已读。', query, queue.query, revision='1'),
    ), close=queue.close, start=queue.start, status=queue.status)
