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
CREATE INDEX IF NOT EXISTS runtime_inbox_reply ON runtime_inbox(actor,json_extract(frame,'$.message.inReplyTo'),seq);
CREATE INDEX IF NOT EXISTS runtime_inbox_origin ON runtime_inbox(actor,json_extract(frame,'$.message.originMessageId'),seq);
CREATE INDEX IF NOT EXISTS runtime_inbox_interaction ON runtime_inbox(actor,json_extract(frame,'$.message.interactionId'),seq);
CREATE TABLE IF NOT EXISTS runtime_operations(seq INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,response TEXT,UNIQUE(actor,kind,id));
CREATE TABLE IF NOT EXISTS runtime_interactions(origin TEXT PRIMARY KEY,id TEXT UNIQUE,actor TEXT NOT NULL,peer TEXT NOT NULL,direction TEXT NOT NULL,name TEXT NOT NULL,revision INTEGER NOT NULL,details TEXT NOT NULL,session TEXT);
CREATE TABLE IF NOT EXISTS runtime_checkpoints(namespace TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(namespace,key));
CREATE INDEX IF NOT EXISTS runtime_active_checkpoints ON runtime_checkpoints(namespace,key) WHERE json_extract(value,'$.active')=1;
'''


class RuntimeStore:
    @staticmethod
    def migrate_host(path, host_ref, *, platform, username):
        """Called only under the original runtime lease, before replacing old settings.

        Bind first: if settings replacement fails, retry retains the original host.
        No business operation is executed and no new runtime identity is allocated.
        """
        path=Path(path)
        if not path.exists():return
        account_path=path.parent/'account.sqlite'
        if not account_path.is_file():
            raise RuntimeFault('identity_conflict','Restore the original account binding before migrating host sessions')
        with sqlite3.connect(account_path.as_uri()+'?mode=ro',uri=True) as account:
            row=account.execute("SELECT v FROM documents WHERE tbl='meta' AND k='binding'").fetchone()
            owner=account.execute("SELECT v FROM documents WHERE tbl='meta' AND k='owner_id'").fetchone()
        if not row or json.loads(row[0])!={'platform':platform.rstrip('/'),'username':username} or not owner:
            raise RuntimeFault('identity_conflict','Host migration requires the original local account binding')
        with sqlite3.connect(path) as conn:
            existing=conn.execute("SELECT value FROM runtime_meta WHERE key='binding'").fetchone()
            if not existing or json.loads(existing[0])!={'platform':platform.rstrip('/'),'owner':json.loads(owner[0])}:
                raise RuntimeFault('identity_conflict','Runtime sessions belong to another platform or account')
            for actor, interaction, session in conn.execute('SELECT actor,id,session FROM runtime_interactions'):
                key=encode([actor,interaction])
                row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-bindings',key)).fetchone()
                if row:
                    value=json.loads(row[0])
                    if value['hostRef']!=host_ref or value['sessionId']!=session:
                        raise RuntimeFault('identity_conflict','Original host migration conflicts with its saved session')
                    continue
                conn.execute('INSERT INTO runtime_checkpoints VALUES(?,?,?)',('host-bindings',key,encode(
                    {'hostRef':host_ref,'sessionId':session,'workspace':None,'source':'external'})))
            for actor,payload in conn.execute("SELECT actor,payload FROM runtime_operations WHERE kind='message.send'"):
                message=json.loads(payload)
                origin=message.get('messageId')
                if message.get('kind') not in ('message.request','delegation.intent') or not origin:continue
                if conn.execute('SELECT 1 FROM runtime_interactions WHERE actor=? AND origin=?',(actor,origin)).fetchone():continue
                key=encode([actor,origin])
                old=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-origins',key)).fetchone()
                if old and json.loads(old[0])['hostRef']!=host_ref:
                    raise RuntimeFault('identity_conflict','Original outbound business belongs to another host')
                conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',('host-origins',key,encode(
                    {'hostRef':host_ref,'sessionId':None,'workspace':None,'source':'external'})))

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

    def checkpoint(self,namespace,key):
        with self.lock:
            row=self.db.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(namespace,key)).fetchone()
            return json.loads(row[0]) if row else None

    def checkpoint_set(self,namespace,key,value):
        with self.transaction() as conn:
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',(namespace,key,encode(value)))

    def checkpoints(self,namespace):
        with self.lock:
            return {row[0]:json.loads(row[1]) for row in self.db.execute('SELECT key,value FROM runtime_checkpoints WHERE namespace=?',(namespace,)).fetchall()}

    def checkpoint_update(self,namespace,values,*,remove=()):
        with self.transaction() as conn:
            for key,value in values.items():
                conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',(namespace,key,encode(value)))
            for key in remove:conn.execute('DELETE FROM runtime_checkpoints WHERE namespace=? AND key=?',(namespace,key))

    def checkpoint_page(self,namespace,*,after='',limit=32,active_only=False):
        if type(limit) is not int or not 1<=limit<=64:raise RuntimeFault('invalid_input','Use a bounded checkpoint page')
        if type(active_only) is not bool:raise RuntimeFault('invalid_input','Use an explicit active checkpoint selector')
        clause=" AND json_extract(value,'$.active')=1" if active_only else ''
        with self.lock:
            return [(row['key'],json.loads(row['value'])) for row in self.db.execute(
                'SELECT key,value FROM runtime_checkpoints WHERE namespace=? AND key>?'+clause+' ORDER BY key LIMIT ?',
                (namespace,after,limit)).fetchall()]

    def request_evidence(self,actor,message,interaction=None):
        """Indexed original request facts; no unbounded inbox materialization."""
        with self.lock:
            if not self.db.execute('SELECT 1 FROM runtime_cursors WHERE actor=?',(actor,)).fetchone():
                raise RuntimeFault('forbidden','Inbox actor is outside this account runtime')
            reply=self.db.execute("SELECT frame FROM runtime_inbox WHERE actor=? AND json_extract(frame,'$.message.inReplyTo')=? "
                                  "AND json_extract(frame,'$.message.kind')='message.result' AND json_extract(frame,'$.message.payloadAvailable') IS NOT 0 "
                                  "ORDER BY seq DESC LIMIT 1",(actor,message)).fetchone()
            closed=self.db.execute("SELECT 1 FROM runtime_inbox WHERE actor=? AND json_extract(frame,'$.message.originMessageId')=? "
                                   "AND json_extract(frame,'$.message.kind')='interaction.closed' LIMIT 1",(actor,message)).fetchone()
            if not closed and interaction:
                closed=self.db.execute("SELECT 1 FROM runtime_inbox WHERE actor=? AND json_extract(frame,'$.message.interactionId')=? "
                                       "AND json_extract(frame,'$.message.kind')='interaction.closed' LIMIT 1",(actor,interaction)).fetchone()
            return {'reply':json.loads(reply['frame'])['message'] if reply else None,'closed':bool(closed)}

    def messages_after(self,actor,after,*,limit=32):
        if type(limit) is not int or not 1<=limit<=64:raise RuntimeFault('invalid_input','Use a bounded inbox page')
        with self.lock:
            if not self.db.execute('SELECT 1 FROM runtime_cursors WHERE actor=?',(actor,)).fetchone():
                raise RuntimeFault('forbidden','Inbox actor is outside this account runtime')
            return [json.loads(row['frame']) for row in self.db.execute(
                'SELECT frame FROM runtime_inbox WHERE actor=? AND seq>? ORDER BY seq LIMIT ?',
                (actor,after,limit)).fetchall()]

    def checkpoint_delete(self,namespace,key):
        with self.transaction() as conn:conn.execute('DELETE FROM runtime_checkpoints WHERE namespace=? AND key=?',(namespace,key))

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

    def prepare_checkpointed(self, actor, kind, payload, operation_id, namespace, values):
        """Save an outbound operation and its role checkpoints in one local transaction."""
        with self.transaction() as conn:
            self._prepare(conn,actor,kind,payload,operation_id)
            for key,value in values.items():
                conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',(namespace,key,encode(value)))
        return operation_id

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

    def operation(self, actor, kind, operation_id):
        """Owned outbox evidence for local adapters, never an arbitrary IPC read."""
        with self.lock:
            row = self.db.execute('SELECT actor,kind,id,payload,fingerprint,response FROM runtime_operations '
                                  'WHERE actor=? AND kind=? AND id=?', (actor,kind,operation_id)).fetchone()
            if row is None:return None
            return {**dict(row),'payload':json.loads(row['payload']),
                    'response':json.loads(row['response']) if row['response'] else None}

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
        if original.get('kind')=='delegation.intent':
            if message['body'].get('interactionType')!='delegation' or message['body'].get('bindingDigest')!=body['bindingDigest']:
                raise RuntimeFault('protocol_error','Interaction changed the requested delegation binding')
            name,revision='delegation',0
        else:
            if (message['body']['interfaceName'],message['body']['revision'])!=(body['interfaceName'],body['revision']):
                raise RuntimeFault('protocol_error', 'Interaction changed the requested interface')
            name,revision=body['interfaceName'],body['revision']
        conn.execute('INSERT INTO runtime_interactions VALUES(?,?,?,?,?,?,?,?,NULL)',
                     (origin,message['interactionId'],actor,message['sender'],'outbound',name,revision,encode(message['body'])))
        self._bind_host_origin(conn,actor,origin,message['interactionId'])

    def prepare_open(self, actor, origin, *, deadline=None, max_requests=8):
        """Create the receiver UUID and its original outbox operation in one transaction."""
        with self.transaction() as conn:
            row = conn.execute('SELECT frame FROM runtime_inbox WHERE actor=? AND message=?',(actor,origin)).fetchone()
            if not row:
                raise RuntimeFault('not_ready', 'Original request has not been durably received')
            request = json.loads(row['frame'])['message']
            if request.get('kind') not in ('message.request','delegation.intent'):
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
            if request['kind']=='delegation.intent':
                body={'interactionType':'delegation','bindingDigest':request['body']['bindingDigest'],'deadline':until}
                name,revision='delegation',0
            else:
                body = {'interactionType':'messaging','interfaceName':request['body']['interfaceName'],
                        'revision':request['body']['revision'],'deadline':until,'maxRequests':max_requests}
                name,revision=request['body']['interfaceName'],request['body']['revision']
            opened = {'messageId':mid,'kind':'interaction.opened','recipient':request['sender'],
                      'interactionId':iid,'originMessageId':origin,'expiresAt':stamp(time.time()+86400),'body':body}
            details = {**body,'openOperationId':operation}
            conn.execute('INSERT INTO runtime_interactions VALUES(?,?,?,?,?,?,?,?,NULL)',
                         (origin,iid,actor,request['sender'],'inbound',name,revision,encode(details)))
            self._prepare(conn,actor,'message.send',opened,operation)
            return {'interactionId':iid,'operationId':operation}

    def interaction(self, *, origin=None, interaction_id=None):
        with self.lock:
            row = self.db.execute('SELECT * FROM runtime_interactions WHERE '+('id=?' if interaction_id else 'origin=?'),
                                  (interaction_id or origin,)).fetchone()
            return {**dict(row),'details':json.loads(row['details'])} if row else None

    def bind_session(self,interaction_id,session,definition_digest):
        with self.transaction() as conn:
            row=conn.execute('SELECT session,details FROM runtime_interactions WHERE id=?',(interaction_id,)).fetchone()
            if not row:raise RuntimeFault('not_ready','Local interaction has not been established')
            details=json.loads(row['details'])
            if (row['session'] and row['session']!=session) or details.get('definitionDigest',definition_digest)!=definition_digest:
                raise RuntimeFault('identity_conflict','Interaction changed its host session or definition')
            details['definitionDigest']=definition_digest
            conn.execute('UPDATE runtime_interactions SET session=?,details=? WHERE id=?',(session,encode(details),interaction_id))

    def session_scope(self, actor, interaction, session):
        event=self.checkpoint('native-events',encode([actor,interaction]))
        binding=event['context'] if event else self.checkpoint('host-bindings',encode([actor,interaction]))
        actual=session or (binding.get('sessionId') if binding else None)
        return (binding['hostRef'] if binding else None,actual) if actual else None

    def record_host_origin(self,actor,origin,value):
        if not isinstance(origin,str) or not origin:raise RuntimeFault('invalid_input','Use the original outbound message identifier')
        key=encode([actor,origin])
        with self.transaction() as conn:
            if not conn.execute('SELECT 1 FROM runtime_cursors WHERE actor=?',(actor,)).fetchone():
                raise RuntimeFault('forbidden','Source identity is outside this account runtime')
            old=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-origins',key)).fetchone()
            if old and old['value']!=encode(value):
                raise RuntimeFault('identity_conflict','The original operation cannot change its native source')
            if not old:
                sent=conn.execute("SELECT payload FROM runtime_operations WHERE actor=? AND kind='message.send'",(actor,)).fetchall()
                if any(json.loads(row['payload']).get('messageId')==origin for row in sent):
                    raise RuntimeFault('identity_conflict','Record the verified source before the original message enters the outbox')
            conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',('host-origins',key,encode(value)))

    def _bind_host_origin(self,conn,actor,origin,interaction):
        saved=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-origins',encode([actor,origin]))).fetchone()
        if not saved:return
        key=encode([actor,interaction])
        old=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-bindings',key)).fetchone()
        if old and old['value']!=saved['value']:
            raise RuntimeFault('identity_conflict','The established interaction changed its original host source')
        conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',('host-bindings',key,saved['value']))

    def recover_link(self, actor, relation):
        if relation.get('interactionId'):
            with self.transaction() as conn:
                if relation.get('sender')!=actor:
                    raise RuntimeFault('forbidden','Only original outbound relations can be recovered here')
                body={'interactionType':'delegation','bindingDigest':relation['bindingDigest'],'deadline':relation['deadline']} if relation.get('interactionType')=='delegation' else {'interfaceName':relation['interfaceName'],'revision':relation['revision'],'deadline':relation['deadline'],'maxRequests':relation['maxRequests']}
                self._link(conn,actor,{'originMessageId':relation['originMessageId'],'interactionId':relation['interactionId'],
                                     'sender':relation['recipient'],'body':body})
        return relation

    def messages(self, actor):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT frame FROM runtime_inbox WHERE actor=? ORDER BY seq',(actor,)).fetchall()]

    def message(self,actor,message_id):
        """Original role recovery by its exact inbox reference, without history scans."""
        with self.lock:
            if not self.db.execute('SELECT 1 FROM runtime_cursors WHERE actor=?',(actor,)).fetchone():
                raise RuntimeFault('forbidden','Inbox identity is outside this instance')
            row=self.db.execute('SELECT frame FROM runtime_inbox WHERE actor=? AND message=?',(actor,message_id)).fetchone()
            return json.loads(row['frame']) if row else None

    def close(self):
        with self.lock:
            self.db.close()
