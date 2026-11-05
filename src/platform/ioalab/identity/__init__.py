"""Ownership queries and one-use connection credentials; no participant execution."""
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from ..errors import LabError



class IdentityDirectory:
    def __init__(self,db,auth,settings,*,clock=time.time):
        self.db,self.auth,self.settings,self.clock=db,auth,settings,clock

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

    def user_key(self,owner,actor):
        """Public ownership view used by user-signed IoA authorization records."""
        row=self.owned(owner,actor)
        if row['role']!='assistant':raise LabError('forbidden','User authorization requires a personal assistant identity',status=403)
        key=json.loads(row['meta']).get('userJwk')
        if not isinstance(key,dict):raise LabError('not_ready','The original user signing key is not bound',status=409)
        return key

    def bound_service(self,protocol):
        """Stable enrolled services use the unified admission path, including before publication."""
        row=self.db.one("SELECT meta FROM agents WHERE role='service' AND json_extract(meta,'$.protocolId')=?",protocol)
        return bool(row)

    def service_party(self,actor):
        """Bound public protocol identity, without fetching participant URLs."""
        row=self.agent(actor)
        meta=json.loads(row['meta'])
        if row['role']!='service' or not isinstance(meta.get('providerJwk'),dict) or not meta.get('protocolId'):
            raise LabError('not_ready','The service public signing identity is not bound',status=409)
        return {'id':meta['protocolId'],'jwk':meta['providerJwk']}

    def internal_service(self,actor):
        """Eligibility for deployment publication only; no contract or execution authority."""
        row=self.agent(actor)
        self.service_party(actor)
        account=self.db.one('SELECT kind FROM users WHERE id=?',row['owner_id'])
        if not account or account['kind']!='isp':
            raise LabError('forbidden','Deployment publication requires an internal ISP service identity',status=403)
        return row

    def matches_party(self,owner,party):
        """Historical payment ownership; disabling new execution does not erase a binding."""
        if not isinstance(party,dict) or not isinstance(party.get('id'),str):
            return False
        row=self.db.one('SELECT owner_id,meta FROM agents WHERE id=?',party['id'])
        return bool(row and row['owner_id']==owner and json.loads(row['meta']).get('clientJwk')==party.get('jwk'))

    def payment_party(self,protocol):
        """Historical bound party and AP2 key; independent of wallet or role direction."""
        if not isinstance(protocol,str):raise LabError('invalid_input','Use an original protocol identity')
        rows=self.db.rows("SELECT * FROM agents WHERE id=? OR json_extract(meta,'$.protocolId')=? OR json_extract(meta,'$.url')=?",protocol,protocol,protocol)
        if len(rows)!=1:raise LabError('not_ready','Payment party identity is unavailable',status=409)
        row=rows[0];meta=json.loads(row['meta'])
        identity=row['id'] if row['role']=='assistant' else meta.get('protocolId',meta.get('url'))
        key=meta.get('payerJwk') if row['role']=='assistant' else meta.get('ap2_jwk')
        if identity!=protocol or not isinstance(key,dict):raise LabError('not_ready','The original AP2 identity is not bound',status=409)
        return {'agentId':row['id'],'protocolId':identity,'owner':row['owner_id'],'jwk':key}

    def saved_keys(self):
        keys,owners=[],{}
        for row in self.db.rows('SELECT id,owner_id,meta FROM agents'):
            meta=json.loads(row['meta'])
            owners[row['id']]=row['owner_id']
            if meta.get('url'):owners[meta['url']]=row['owner_id']
            for name in ('clientJwk','userJwk','payerJwk','providerJwk','ap2_jwk'):
                if isinstance(meta.get(name),dict):keys.append((row['owner_id'],meta[name]))
        return keys,owners

    def list_owned(self,owner):
        result=[]
        for row in self.db.rows('SELECT id,role,name,status,meta FROM agents WHERE owner_id=?',owner):
            meta=json.loads(row.pop('meta'))
            result.append({**row,'protocolId':meta.get('protocolId',meta.get('url',row['id']))})
        return result

    def search_actor(self,owner):
        """Preserve the discovery audit actor, including historical identities."""
        row=self.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'",owner)
        return row['id'] if row else owner

    def services_owned(self,owner):
        """Owner-only provider metadata in its original HTTP representation."""
        return self.db.rows("SELECT id,name,status,meta FROM agents WHERE owner_id=? AND role='service'",owner)

    def bearer(self,authorization):
        if not isinstance(authorization,str) or not authorization.startswith('Bearer '):
            raise LabError('forbidden','SDK Bearer authentication is required',status=401)
        subject=self.auth.verify(authorization[7:].strip())
        if not isinstance(subject,str) or not subject.startswith('assistant:'):
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
