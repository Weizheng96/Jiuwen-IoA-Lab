"""One local database for reliable delivery and operation recovery, using short transactions."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .protocol import RuntimeFault, encode, instant, stamp


SCHEMA = '''
CREATE TABLE IF NOT EXISTS runtime_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_cursors(actor TEXT PRIMARY KEY,seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_inbox(actor TEXT NOT NULL,seq INTEGER NOT NULL,message TEXT NOT NULL UNIQUE,frame TEXT NOT NULL,PRIMARY KEY(actor,seq));
CREATE TABLE IF NOT EXISTS runtime_operations(seq INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,response TEXT,UNIQUE(actor,kind,id));
CREATE TABLE IF NOT EXISTS runtime_interactions(origin TEXT PRIMARY KEY,id TEXT UNIQUE,actor TEXT NOT NULL,peer TEXT NOT NULL,direction TEXT NOT NULL,name TEXT NOT NULL,revision INTEGER NOT NULL,details TEXT NOT NULL,session TEXT);
CREATE TABLE IF NOT EXISTS runtime_checkpoints(namespace TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(namespace,key));
'''


class RuntimeStore:
    def __init__(self, path, platform_url, actors, *, owner_id):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        if os.name!='nt':
            os.chmod(self.path,0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript(SCHEMA)
        if not isinstance(owner_id,str) or not owner_id or not actors or not all(isinstance(actor,str) for actor in actors):
            self.db.close()
            raise ValueError('A local account owner and its identities are required')
        binding = encode({'platform':platform_url.rstrip('/'), 'owner':owner_id})
        try:
            with self.transaction() as conn:
                existing = conn.execute("SELECT value FROM runtime_meta WHERE key='binding'").fetchone()
                if existing and existing['value']!=binding:
                    raise RuntimeFault('identity_conflict', 'The local database belongs to another platform or account')
                existing_actors = {row[0] for row in conn.execute('SELECT actor FROM runtime_cursors').fetchall()}
                if not existing_actors.issubset(set(actors)):
                    raise RuntimeFault('identity_conflict', 'Keep existing identities when adding another local role')
                conn.execute("INSERT OR IGNORE INTO runtime_meta VALUES('binding',?)", (binding,))
                conn.execute("INSERT OR IGNORE INTO runtime_meta VALUES('runtimeId',?)", (str(uuid.uuid4()),))
                self.runtime_id = conn.execute("SELECT value FROM runtime_meta WHERE key='runtimeId'").fetchone()[0]
                for actor in actors:
                    conn.execute('INSERT OR IGNORE INTO runtime_cursors VALUES(?,0)', (actor,))
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def cursors(self):
        with self.lock:
            return dict(self.db.execute('SELECT actor,seq FROM runtime_cursors').fetchall())

    def summary(self):
        with self.lock:
            return {'savedMessages':self.db.execute('SELECT COUNT(*) FROM runtime_inbox').fetchone()[0],
                    'pendingOperations':self.db.execute('SELECT COUNT(*) FROM runtime_operations WHERE response IS NULL').fetchone()[0],
                    'cursors':dict(self.db.execute('SELECT actor,seq FROM runtime_cursors').fetchall())}

    def _prepare(self, conn, actor, kind, payload, operation_id):
        if not conn.execute('SELECT 1 FROM runtime_cursors WHERE actor=?', (actor,)).fetchone():
            raise RuntimeFault('forbidden', 'Identity is outside this local instance')
        if not isinstance(operation_id,str) or not 1<=len(operation_id)<=128 or not isinstance(payload,dict):
            raise RuntimeFault('invalid_input', 'Operation identity and object payload are required')
        encoded = encode(payload)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        old = conn.execute('SELECT fingerprint FROM runtime_operations WHERE actor=? AND kind=? AND id=?',
                           (actor,kind,operation_id)).fetchone()
        if old and old['fingerprint']!=digest:
            raise RuntimeFault('id_conflict', 'Original operation parameters cannot change', operation_id=operation_id)
        conn.execute('INSERT OR IGNORE INTO runtime_operations(actor,kind,id,payload,fingerprint) VALUES(?,?,?,?,?)',
                     (actor,kind,operation_id,encoded,digest))
        return operation_id

    def prepare(self, actor, kind, payload, operation_id=None):
        with self.transaction() as conn:
            return self._prepare(conn,actor,kind,payload,operation_id or str(uuid.uuid4()))

    def pending(self):
        with self.lock:
            return [{**dict(row),'payload':json.loads(row['payload'])}
                    for row in self.db.execute('SELECT * FROM runtime_operations WHERE response IS NULL ORDER BY seq').fetchall()]

    def complete(self, frame):
        key = (frame.get('agentId'),frame.get('operation'),frame.get('operationId'))
        if not isinstance(frame.get('ok'),bool):
            raise RuntimeFault('protocol_error', 'Invalid operation response')
        with self.transaction() as conn:
            row = conn.execute('SELECT response FROM runtime_operations WHERE actor=? AND kind=? AND id=?',key).fetchone()
            if not row:
                raise RuntimeFault('protocol_error', 'Response does not match a saved operation')
            if row['response'] and row['response']!=encode(frame):
                raise RuntimeFault('id_conflict', 'Conflicting result for an original operation')
            conn.execute('UPDATE runtime_operations SET response=? WHERE actor=? AND kind=? AND id=?',(encode(frame),*key))

    def response(self, actor, kind, operation_id):
        with self.lock:
            row = self.db.execute('SELECT response FROM runtime_operations WHERE actor=? AND kind=? AND id=?',
                                  (actor,kind,operation_id)).fetchone()
            return json.loads(row['response']) if row and row['response'] else None

    def receive(self, frame):
        """Save the message and continuous cursor before producing an ACK operation."""
        actor, seq, message = frame.get('agentId'),frame.get('recipientSeq'),frame.get('message')
        if not isinstance(message,dict) or not isinstance(seq,int) or isinstance(seq,bool) or seq<1:
            raise RuntimeFault('protocol_error', 'Invalid delivery frame')
        if not isinstance(message.get('messageId'),str) or message.get('recipientSeq')!=seq or message.get('recipient',actor)!=actor:
            raise RuntimeFault('protocol_error', 'Delivery identity does not match its envelope')
        with self.transaction() as conn:
            cursor = conn.execute('SELECT seq FROM runtime_cursors WHERE actor=?',(actor,)).fetchone()
            if not cursor:
                raise RuntimeFault('forbidden', 'Delivery is outside this local instance')
            old = conn.execute('SELECT frame,message FROM runtime_inbox WHERE actor=? AND seq=?',(actor,seq)).fetchone()
            if old:
                previous = json.loads(old['frame'])['message']
                if old['message']!=message['messageId'] or (previous!=message and message.get('payloadAvailable') is not False):
                    raise RuntimeFault('id_conflict', 'A stored delivery cannot be replaced')
            else:
                conn.execute('INSERT INTO runtime_inbox VALUES(?,?,?,?)',(actor,seq,message['messageId'],encode(frame)))
                if message.get('kind')=='interaction.opened' and message.get('payloadAvailable') is not False:
                    self._link(conn,actor,message)
            through = cursor['seq']
            while conn.execute('SELECT 1 FROM runtime_inbox WHERE actor=? AND seq=?',(actor,through+1)).fetchone():
                through += 1
            conn.execute('UPDATE runtime_cursors SET seq=? WHERE actor=?',(through,actor))
            if through:
                last = conn.execute('SELECT message FROM runtime_inbox WHERE actor=? AND seq=?',(actor,through)).fetchone()[0]
                operation = str(uuid.uuid5(uuid.NAMESPACE_URL,encode(['ioa-ack',actor,through,last])))
                self._prepare(conn,actor,'message.ack',{'messageId':last,'through':through},operation)
            return through

    def _link(self, conn, actor, message):
        origin = message.get('originMessageId')
        old = conn.execute('SELECT * FROM runtime_interactions WHERE origin=?',(origin,)).fetchone()
        if old:
            if (old['id'],old['actor'],old['peer'])!=(message.get('interactionId'),actor,message.get('sender')):
                raise RuntimeFault('id_conflict', 'An established interaction cannot change its participants')
            return
        rows = conn.execute("SELECT payload FROM runtime_operations WHERE actor=? AND kind='message.send'",(actor,)).fetchall()
        original = next((json.loads(row[0]) for row in rows if json.loads(row[0]).get('messageId')==origin),None)
        if not original or original['recipient']!=message.get('sender'):
            raise RuntimeFault('protocol_error', 'Interaction is not bound to an original outbound request')
        body = original['body']
        if (message['body']['interfaceName'],message['body']['revision'])!=(body['interfaceName'],body['revision']):
            raise RuntimeFault('protocol_error', 'Interaction changed the requested interface')
        conn.execute('INSERT INTO runtime_interactions VALUES(?,?,?,?,?,?,?,?,NULL)',
                     (origin,message['interactionId'],actor,message['sender'],'outbound',body['interfaceName'],body['revision'],encode(message['body'])))

    def prepare_open(self, actor, origin, *, deadline=None, max_requests=8):
        """Create the receiver UUID and its original outbox operation in one transaction."""
        with self.transaction() as conn:
            row = conn.execute('SELECT frame FROM runtime_inbox WHERE actor=? AND message=?',(actor,origin)).fetchone()
            if not row:
                raise RuntimeFault('not_ready', 'Original request has not been durably received')
            request = json.loads(row['frame'])['message']
            if request.get('kind')!='message.request':
                raise RuntimeFault('invalid_input', 'Only an inbound business request opens an interaction')
            old = conn.execute('SELECT * FROM runtime_interactions WHERE origin=?',(origin,)).fetchone()
            if old:
                operation = json.loads(old['details'])['openOperationId']
                return {'interactionId':old['id'],'operationId':operation}
            if instant(request['expiresAt'])<=time.time():
                raise RuntimeFault('expired', 'Original request expired before local processing')
            if not isinstance(max_requests,int) or isinstance(max_requests,bool) or not 1<=max_requests<=8:
                raise RuntimeFault('invalid_input', 'max_requests must be between one and eight')
            until = deadline or request['expiresAt']
            if not time.time()<instant(until)<=instant(request['expiresAt']):
                raise RuntimeFault('invalid_input', 'Interaction deadline must fit the original request')
            iid, mid, operation = (str(uuid.uuid4()) for _ in range(3))
            body = {'interactionType':'messaging','interfaceName':request['body']['interfaceName'],
                    'revision':request['body']['revision'],'deadline':until,'maxRequests':max_requests}
            opened = {'messageId':mid,'kind':'interaction.opened','recipient':request['sender'],
                      'interactionId':iid,'originMessageId':origin,'expiresAt':stamp(time.time()+86400),'body':body}
            details = {**body,'openOperationId':operation}
            conn.execute('INSERT INTO runtime_interactions VALUES(?,?,?,?,?,?,?,?,NULL)',
                         (origin,iid,actor,request['sender'],'inbound',body['interfaceName'],body['revision'],encode(details)))
            self._prepare(conn,actor,'message.send',opened,operation)
            return {'interactionId':iid,'operationId':operation}

    def interaction(self, *, origin=None, interaction_id=None):
        with self.lock:
            row = self.db.execute('SELECT * FROM runtime_interactions WHERE '+('id=?' if interaction_id else 'origin=?'),
                                  (interaction_id or origin,)).fetchone()
            return {**dict(row),'details':json.loads(row['details'])} if row else None

    def recover_link(self, actor, relation):
        if relation.get('interactionId'):
            with self.transaction() as conn:
                if relation.get('sender')!=actor:
                    raise RuntimeFault('forbidden','Only original outbound relations can be recovered here')
                self._link(conn,actor,{'originMessageId':relation['originMessageId'],'interactionId':relation['interactionId'],
                                     'sender':relation['recipient'],'body':{'interfaceName':relation['interfaceName'],
                                     'revision':relation['revision'],'deadline':relation['deadline'],'maxRequests':relation['maxRequests']}})
        return relation

    def messages(self, actor):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT frame FROM runtime_inbox WHERE actor=? ORDER BY seq',(actor,)).fetchall()]

    def close(self):
        with self.lock:
            self.db.close()
