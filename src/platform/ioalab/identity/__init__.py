"""Ownership queries and one-use connection credentials; no participant execution."""
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from ..errors import LabError

SCHEMA='''
CREATE TABLE IF NOT EXISTS connection_tickets(
 digest TEXT PRIMARY KEY,owner TEXT NOT NULL,actors TEXT NOT NULL,
 expires REAL NOT NULL,consumed INTEGER NOT NULL DEFAULT 0);
'''


class IdentityDirectory:
    def __init__(self,db,auth,settings,*,clock=time.time):
        self.db,self.auth,self.settings,self.clock=db,auth,settings,clock
        with db.lock:db.conn.executescript(SCHEMA)

    def agent(self,agent_id):
        row=self.db.one('SELECT * FROM agents WHERE id=?',agent_id)
        if not row or row['status']=='disabled':
            raise LabError('forbidden','Agent is unavailable',status=403,recovery='check_identity')
        return row

    def owned(self,owner,agent_id):
        row=self.agent(agent_id)
        if row['owner_id']!=owner:
            raise LabError('forbidden','Agent does not belong to this account',status=403)
        return row

    def assistant(self,owner):
        row=self.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant' AND status!='disabled'",owner)
        if not row:raise LabError('not_ready','Connect a personal assistant identity first',status=409)
        return row['id']

    def matches_party(self,owner,party):
        """Historical payment ownership; disabling new execution does not erase a binding."""
        if not isinstance(party,dict) or not isinstance(party.get('id'),str):
            return False
        row=self.db.one('SELECT owner_id,meta FROM agents WHERE id=?',party['id'])
        return bool(row and row['owner_id']==owner and json.loads(row['meta']).get('clientJwk')==party.get('jwk'))

    def bearer(self,authorization):
        if not isinstance(authorization,str) or not authorization.startswith('Bearer '):
            raise LabError('forbidden','SDK Bearer authentication is required',status=401)
        subject=self.auth.verify(authorization[7:].strip())
        if not subject or subject=='admin':
            raise LabError('forbidden','SDK authentication expired or is invalid',status=401,recovery='authenticate')
        owner=subject.removeprefix('assistant:')
        if not self.db.one('SELECT id FROM users WHERE id=?',owner):
            raise LabError('forbidden','Account is unavailable',status=401)
        return owner

    def issue(self,owner,actors):
        if not isinstance(actors,list) or not 1<=len(actors)<=64 or not all(isinstance(a,str) and 1<=len(a)<=256 for a in actors) or len(set(actors))!=len(actors):
            raise LabError('invalid_input','agentIds must contain 1 to 64 distinct identities')
        for actor in actors:self.owned(owner,actor)
        ticket=secrets.token_urlsafe(32)
        expires=self.clock()+self.settings.connection_ticket_seconds
        with self.db.tx() as conn:
            conn.execute('DELETE FROM connection_tickets WHERE expires<?',(self.clock()-300,))
            conn.execute('INSERT INTO connection_tickets(digest,owner,actors,expires) VALUES(?,?,?,?)',
                         (hashlib.sha256(ticket.encode()).hexdigest(),owner,json.dumps(actors),expires))
        return {'ticket':ticket,'expiresAt':datetime.fromtimestamp(expires,timezone.utc).isoformat().replace('+00:00','Z')}

    def consume(self,ticket):
        if not isinstance(ticket,str) or len(ticket)>256:
            raise LabError('forbidden','Invalid connection ticket',status=401)
        with self.db.tx() as conn:
            row=conn.execute('SELECT * FROM connection_tickets WHERE digest=?',(hashlib.sha256(ticket.encode()).hexdigest(),)).fetchone()
            if not row or row['consumed'] or row['expires']<=self.clock():
                raise LabError('forbidden','Connection ticket is expired or already used',status=401,recovery='new_ticket')
            actors=json.loads(row['actors'])
            for actor in actors:self.owned(row['owner'],actor)
            conn.execute('UPDATE connection_tickets SET consumed=1 WHERE digest=?',(row['digest'],))
            return row['owner'],actors
