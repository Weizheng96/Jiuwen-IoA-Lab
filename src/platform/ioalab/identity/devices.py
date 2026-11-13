"""Revocable SDK device sessions; durable rotation receipts contain no raw credentials."""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

from ..errors import LabError


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


class DeviceSessions:
    access_seconds = 15 * 60
    session_seconds = 30 * 86400

    def __init__(self, db, auth, *, clock=None):
        self.db, self.auth, self.clock = db, auth, clock or time.time

    def _fingerprint(self, value):
        return hmac.new(self.auth.secret, b'device-credential\0' + value.encode(), hashlib.sha256).hexdigest()

    def _refresh(self, record):
        value = f"{record['sessionId']}:{record['generation']}"
        proof = hmac.new(self.auth.secret, b'device-refresh\0' + value.encode(), hashlib.sha256).hexdigest()
        return value + ':' + proof

    def _get(self, session, conn=None):
        conn = conn or self.db.conn
        row = conn.execute('SELECT body FROM device_sessions WHERE id=?', (session,)).fetchone()
        return json.loads(row['body']) if row else None

    def _save(self, conn, record):
        conn.execute('INSERT OR REPLACE INTO device_sessions VALUES(?,?,?,?)',
                     (record['sessionId'], record['owner'], record['deviceId'], json.dumps(record)))

    def _valid(self, record, conn=None):
        if not record or record.get('revokedAt') or record['expires'] <= self.clock():
            raise LabError('authenticate', 'Device session is expired or revoked', status=401, recovery='authenticate')
        row = (conn or self.db.conn).execute('SELECT pw_hash FROM users WHERE id=?', (record['owner'],)).fetchone()
        if not row or not hmac.compare_digest(record['credential'], self._fingerprint(row['pw_hash'])):
            raise LabError('authenticate', 'Account credentials changed', status=401, recovery='authenticate')
        return record

    def create_in(self, conn, user, credential, device_id, name):
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
            raise LabError('invalid_input', 'A bounded deviceId is required')
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise LabError('invalid_input', 'A bounded deviceName is required')
        now = int(self.clock())
        # A re-login for this installation replaces its old authorization, not its identities.
        for row in conn.execute('SELECT body FROM device_sessions WHERE owner=? AND device=?', (user['id'], device_id)).fetchall():
            previous = json.loads(row['body'])
            if not previous.get('revokedAt'):
                previous['revokedAt'] = now
                self._save(conn, previous)
        record = {'sessionId': str(uuid.uuid4()), 'owner': user['id'], 'deviceId': device_id,
                  'deviceName': name, 'credential': self._fingerprint(credential), 'created': now,
                  'expires': now + self.session_seconds, 'accessExpires': now + self.access_seconds,
                  'generation': 0, 'revokedAt': None, 'lastUsed': now}
        record['refreshDigest'] = hashlib.sha256(self._refresh(record).encode()).hexdigest()
        self._save(conn, record)
        return record['sessionId']

    def render(self, session, *, initial=False):
        with self.db.lock:
            record = self._valid(self._get(session))
            if initial and record['generation']:
                raise LabError('authenticate', 'Original login has already rotated; use the saved session', status=401)
            return {'id': record['owner'], 'sessionId': session, 'deviceId': record['deviceId'],
                    'token': self.auth.token('device:' + session, expires_at=record['accessExpires']),
                    'expiresAt': stamp(record['accessExpires']), 'refreshToken': self._refresh(record),
                    'sessionExpiresAt': stamp(record['expires'])}

    def check(self, session):
        with self.db.lock:
            return self._valid(self._get(session))['owner']

    def from_access(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            raise LabError('authenticate', 'Device access token is required', status=401)
        subject = self.auth.signed_subject(authorization[7:].strip())
        if not subject or not subject.startswith('device:'):
            raise LabError('authenticate', 'Log in to a device session', status=401, recovery='authenticate')
        session = subject.removeprefix('device:')
        return self.check(session), session

    def revoke_access(self, authorization, target):
        # A lost self-logout response can be retrieved with its original signed token.
        # This narrow receipt exception grants no access to any other resource.
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            raise LabError('authenticate', 'Device access token is required', status=401)
        subject = self.auth.signed_subject(authorization[7:].strip())
        if not subject or not subject.startswith('device:'):
            raise LabError('authenticate', 'Device access token is required', status=401)
        session = subject.removeprefix('device:')
        with self.db.lock:
            record = self._get(session)
            if record and session == target and record.get('revokedAt'):
                return {'sessionId': target, 'status': 'revoked'}
            self._valid(record)
            return self.revoke(record['owner'], target)

    def refresh(self, token, operation):
        if not isinstance(operation, str) or not 1 <= len(operation) <= 128:
            raise LabError('invalid_input', 'A stable refresh operationId is required')
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            raise LabError('authenticate', 'Refresh credential is required', status=401)
        session = token.split(':')[0]
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        with self.db.tx() as conn:
            record = self._valid(self._get(session, conn), conn)
            if (record.get('rotationOperation') == operation and
                    hmac.compare_digest(record.get('previousDigest', ''), fingerprint)):
                return {**self.render(session), 'credited': False}
            if record.get('rotationOperation') == operation:
                raise LabError('id_conflict', 'Refresh operation input changed', status=409)
            if not hmac.compare_digest(record['refreshDigest'], fingerprint):
                raise LabError('authenticate', 'Refresh credential was superseded', status=401, recovery='authenticate')
            record.update(previousDigest=fingerprint, rotationOperation=operation,
                          generation=record['generation'] + 1, lastUsed=int(self.clock()),
                          accessExpires=min(int(self.clock()) + self.access_seconds, record['expires']))
            record['refreshDigest'] = hashlib.sha256(self._refresh(record).encode()).hexdigest()
            self._save(conn, record)
            return {**self.render(session), 'credited': False}

    def list(self, owner, current=None):
        with self.db.lock:
            records = [json.loads(row['body']) for row in self.db.conn.execute(
                'SELECT body FROM device_sessions WHERE owner=? ORDER BY rowid DESC', (owner,))]
            credential=self.db.conn.execute('SELECT pw_hash FROM users WHERE id=?',(owner,)).fetchone()
            current_credential=self._fingerprint(credential['pw_hash']) if credential else ''
            return {'sessions': [{'sessionId': r['sessionId'], 'deviceId': r['deviceId'],
                    'deviceName': r['deviceName'], 'createdAt': stamp(r['created']), 'lastUsedAt': stamp(r['lastUsed']),
                    'expiresAt': stamp(r['expires']), 'current': r['sessionId'] == current,
                    'status': 'revoked' if r.get('revokedAt') or not hmac.compare_digest(r['credential'],current_credential) else 'expired' if r['expires'] <= self.clock() else 'active'}
                    for r in records]}

    def revoke(self, owner, session):
        with self.db.tx() as conn:
            record = self._get(session, conn)
            if not record or record['owner'] != owner:
                raise LabError('forbidden', 'Device session belongs to another account', status=403)
            if not record.get('revokedAt'):
                record['revokedAt'] = int(self.clock())
                self._save(conn, record)
        return {'sessionId': session, 'status': 'revoked'}
