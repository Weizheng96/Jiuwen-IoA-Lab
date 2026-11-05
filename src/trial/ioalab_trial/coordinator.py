"""Atomic leases; revocation is durable before stopping any sandbox."""
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
import threading


class Denied(ValueError):
    pass


class Coordinator:
    def __init__(self, store, *, audience, admission_key, lease_seconds=1200,
                 grace_seconds=90, clock=time.time, confirm_audience=None):
        if len(admission_key) < 32 or not 1 <= grace_seconds < lease_seconds:
            raise ValueError('Use a dedicated high entropy key and bounded lease/grace')
        self.store, self.audience, self.key = store, audience.rstrip('/'), admission_key
        self.confirm_audience = confirm_audience.rstrip('/') if confirm_audience else None
        self.lease, self.grace, self.clock = lease_seconds, grace_seconds, clock
        self.listeners = set()
        self.stopping = threading.Event()

    def begin_shutdown(self):
        """Fence browser access before draining; preserve every epoch and volume."""
        self.stopping.set()
        with self.store.transaction() as db:
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM queue')
            db.execute("UPDATE seats SET state='revoking',reason='deployment_stopped' WHERE epoch IS NOT NULL AND state!='quarantined'")
        for row in self.store.seats():
            for listener in tuple(self.listeners):listener(row['id'])

    def shutdown(self,sandbox):
        self.shutdown_verified=False
        self.begin_shutdown()
        failed=[]
        for row in self.store.seats():
            if not row['epoch']:continue
            try:sandbox.stop(row['id'],row['epoch'])
            except Exception:
                self._state(row['id'],'quarantined');failed.append(row['id'])
        if failed:raise RuntimeError('Trial shutdown is unverified; original epochs were preserved')
        self.shutdown_verified=True

    def consume(self, ticket, *, audience=None):
        if self.stopping.is_set():raise Denied('Trial deployment is stopping')
        audience = audience or self.audience
        if audience not in (self.audience,self.confirm_audience):
            raise Denied('Unknown admission audience')
        purpose = 'confirm' if audience == self.confirm_audience else 'native'
        try:
            payload, signature = ticket.split('.')
            if len(ticket) > 4096 or not hmac.compare_digest(
                signature, hmac.new(self.key.encode(), payload.encode(), hashlib.sha256).hexdigest()):
                raise Denied('Invalid admission')
            data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            now = self.clock()
            if (set(data) != {'v','owner','aud','iat','exp','jti'} or data['v'] != 1
                    or data['aud'] != audience or not isinstance(data['owner'], str)
                    or not 1 <= len(data['owner']) <= 256 or not isinstance(data['jti'],str)
                    or not 1 <= len(data['jti']) <= 256
                    or type(data['iat']) not in (int,float) or type(data['exp']) not in (int,float)
                    or not now - 60 <= data['iat'] <= now or not now < data['exp'] <= data['iat'] + 60):
                raise Denied('Expired or mismatched admission')
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise Denied('Invalid admission') from exc
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            if self.stopping.is_set():raise Denied('Trial deployment is stopping')
            bound = None
            if purpose == 'confirm':
                bound = db.execute('SELECT * FROM seats WHERE owner=?',(data['owner'],)).fetchone()
                if (not bound or bound['state'] not in ('leased','reconnect_grace')
                        or bound['expires'] <= now or bound['seen'] + self.grace <= now):
                    raise Denied('请先取得试用席位，再从正式平台打开确认入口')
            if db.execute('SELECT 1 FROM tickets WHERE id=?', (data['jti'],)).fetchone():
                raise Denied('Admission already consumed')
            db.execute('DELETE FROM tickets WHERE expires<?', (now - 60,))
            db.execute('INSERT INTO tickets VALUES(?,?)', (data['jti'], data['exp']))
            db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
            db.execute('DELETE FROM sessions WHERE owner=? AND audience=?', (data['owner'],audience))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), data['owner'], csrf, now + self.lease + self.grace,
                        audience,purpose,bound['id'] if bound else None,bound['epoch'] if bound else None))
        return token, csrf

    def session(self, token, *, audience=None):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM sessions WHERE token=? AND expires>?',
                (hashlib.sha256(token.encode()).hexdigest(), self.clock())).fetchone()
        if not row or (audience is not None and row['audience'] != audience):
            raise Denied('试用访问已失效，请返回正式平台重新打开试用入口')
        return dict(row)

    def enqueue(self, owner):
        if self.stopping.is_set():raise Denied('Trial deployment is stopping')
        with self.store.transaction() as db:
            if self.stopping.is_set():raise Denied('Trial deployment is stopping')
            if not db.execute('SELECT 1 FROM seats WHERE owner=?', (owner,)).fetchone():
                db.execute('INSERT OR IGNORE INTO queue VALUES(?,?)', (owner, self.clock()))
            self._assign(db)
        return self.status(owner)

    def _assign(self, db):
        if self.stopping.is_set():return
        for seat in db.execute("SELECT id FROM seats WHERE state='ready' ORDER BY id").fetchall():
            owner = db.execute('SELECT owner FROM queue ORDER BY created,owner LIMIT 1').fetchone()
            if not owner:
                break
            now = self.clock()
            db.execute("UPDATE seats SET state='leased',owner=?,expires=?,seen=? WHERE id=?",
                       (owner['owner'], now + self.lease, now, seat['id']))
            db.execute('DELETE FROM queue WHERE owner=?', (owner['owner'],))

    def status(self, owner):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM seats WHERE owner=?', (owner,)).fetchone()
            queue = [item['owner'] for item in self.store.db.execute('SELECT owner FROM queue ORDER BY created,owner')]
        if row:
            return {**{key: row[key] for key in ('id','state','epoch','expires')},'capacity':self.store.capacity}
        return {'state':'queued' if owner in queue else 'idle',
                'capacity':self.store.capacity,
                'position':queue.index(owner) + 1 if owner in queue else None}

    def access(self, owner, epoch, *, touch=False):
        if self.stopping.is_set():raise Denied('Trial deployment is stopping')
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM seats WHERE owner=?', (owner,)).fetchone()
            now = self.clock()
            if (not row or row['epoch'] != epoch or row['state'] not in ('leased','reconnect_grace')
                    or row['expires'] <= now or row['seen'] + self.grace <= now):
                raise Denied('本轮试用已结束或超时，请返回正式平台重新申请')
            if touch:
                db.execute("UPDATE seats SET seen=?,state='leased' WHERE id=?", (now,row['id']))
            return dict(row)

    def revoke(self, seat, reason):
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM seats WHERE id=?', (seat,)).fetchone()
            if row['state'] in ('leased','reconnect_grace','ready'):
                db.execute("UPDATE seats SET state='revoking',reason=? WHERE id=?", (reason,seat))
                db.execute('DELETE FROM sessions WHERE owner=?', (row['owner'],))
        for listener in tuple(self.listeners):
            listener(seat)

    def end(self, owner):
        status = self.status(owner)
        with self.store.transaction() as db:
            db.execute('DELETE FROM queue WHERE owner=?', (owner,))
        if 'id' in status:
            self.revoke(status['id'], 'ended')

    def tick(self):
        for row in self.store.seats():
            if row['state'] in ('leased','reconnect_grace'):
                if self.clock() >= min(row['expires'],row['seen'] + self.grace):
                    self.revoke(row['id'], 'expired')
                elif self.clock() > row['seen'] + min(10,self.grace / 2):
                    with self.store.transaction() as db:
                        db.execute("UPDATE seats SET state='reconnect_grace' WHERE id=? AND state='leased'", (row['id'],))

    def reconcile(self, sandbox):
        """One coordinator worker calls this serially. No user request runs Docker."""
        if self.stopping.is_set():return
        self.tick()
        for row in self.store.seats():
            if self.stopping.is_set():return
            seat, epoch = row['id'], row['epoch']
            try:
                state = row['state']
                if state in ('revoking','stopping','wiping'):
                    self._state(seat,'stopping')
                    sandbox.stop(seat,epoch)
                    if self.stopping.is_set():return
                    self._state(seat,'wiping')
                    sandbox.wipe(seat,epoch)
                    self._state(seat,'preparing',clear=True)
                    state = 'preparing'
                if state == 'preparing':
                    # Epoch is durable before the first external resource operation.
                    if not epoch or row['state'] != 'preparing':
                        epoch = uuid.uuid4().hex
                        with self.store.transaction() as db:
                            db.execute('UPDATE seats SET epoch=? WHERE id=?', (epoch,seat))
                    sandbox.prepare(seat,epoch)
                    if self.stopping.is_set():return
                    sandbox.healthy(seat,epoch)
                    with self.store.transaction() as db:
                        if self.stopping.is_set():return
                        db.execute("UPDATE seats SET state='ready',owner=NULL,reason=NULL WHERE id=?", (seat,))
                        self._assign(db)
            except Exception:
                # No upstream output: Docker/runtime exceptions can contain secrets.
                self._state(seat,'quarantined')

    def _state(self, seat, state, *, clear=False):
        with self.store.transaction() as db:
            db.execute('UPDATE seats SET state=? WHERE id=?', (state,seat))
            if clear:
                db.execute('UPDATE seats SET owner=NULL,expires=NULL,seen=NULL,epoch=NULL WHERE id=?', (seat,))
