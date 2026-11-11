"""Connection leases, generations, heartbeats and delivery reads for one runtime. Part of DeliveryBroker."""
import json

from ..errors import LabError
from .envelope import identifier


class Connections:
    def listen(self,connection,loop,event):
        with self._listener_lock:self._listeners[connection]=(loop,event)

    def unlisten(self,connection):
        with self._listener_lock:self._listeners.pop(connection,None)

    def notify(self):
        with self._listener_lock:
            for loop,event in list(self._listeners.values()):
                if not loop.is_closed():loop.call_soon_threadsafe(event.set)

    def connect(self,owner,actors,runtime,cursors,connection):
        runtime=identifier(runtime,'runtimeId')
        if not isinstance(cursors,dict) or set(cursors)-set(actors):raise LabError('invalid_input','Invalid recovery cursors')
        generations,acks={},{}
        now=self.clock()
        with self.db.tx() as conn:
            for actor in actors:
                self.identities.owned(owner,actor)
                lease=conn.execute('SELECT * FROM delivery_leases WHERE actor=?',(actor,)).fetchone()
                if lease and lease['expires']>now and lease['runtime']!=runtime:
                    raise LabError('not_ready','Another local instance still owns the execution lease',status=409,recovery='migrate_local_state')
                conn.execute('INSERT OR IGNORE INTO delivery_mailboxes(actor) VALUES(?)',(actor,))
                mailbox=conn.execute('SELECT * FROM delivery_mailboxes WHERE actor=?',(actor,)).fetchone()
                cursor=cursors.get(actor,0)
                if not isinstance(cursor,int) or isinstance(cursor,bool) or not 0<=cursor<=mailbox['last_seq']:
                    raise LabError('invalid_input','Recovery cursor exceeds accepted history')
                retained=conn.execute('SELECT COUNT(*) FROM delivery_messages WHERE recipient=? AND seq>?',(actor,cursor)).fetchone()[0]
                if retained!=mailbox['last_seq']-cursor:
                    raise LabError('history_gap','Delivery history no longer covers the local cursor',status=409,recovery='restore_state')
                generation=(lease['generation'] if lease else 0)+1
                conn.execute('INSERT OR REPLACE INTO delivery_leases VALUES(?,?,?,?,?)',(actor,runtime,connection,generation,now+self.s.ws_lease_seconds))
                generations[actor],acks[actor]=generation,mailbox['ack_seq']
        self.notify()
        return {'connectionId':connection,'generations':generations,'cursors':acks,
                'heartbeatSeconds':self.s.ws_heartbeat_seconds,'leaseSeconds':self.s.ws_lease_seconds}

    def lease(self,conn,owner,actor,connection,generation):
        self.identities.owned(owner,actor)
        lease=conn.execute('SELECT * FROM delivery_leases WHERE actor=?',(actor,)).fetchone()
        if not lease or lease['connection']!=connection or lease['generation']!=generation or lease['expires']<=self.clock():
            raise LabError('stale_generation','The connection no longer owns execution',status=409,recovery='stop_executor')
        return lease

    def heartbeat(self,owner,actors,connection,generations):
        with self.db.tx() as conn:
            for actor in actors:
                self.lease(conn,owner,actor,connection,generations[actor])
                self.renew_in(conn,actor)

    def active_in(self,conn,actor):
        self.identities.agent(actor)
        row=conn.execute('SELECT expires FROM delivery_leases WHERE actor=?',(actor,)).fetchone()
        return bool(row and row['expires']>self.clock())

    def disconnect(self,connection):
        self.db.run('UPDATE delivery_leases SET expires=? WHERE connection=?',self.clock(),connection)
        self.unlisten(connection)

    def deliveries(self,owner,actor,connection,generation,after,limit=100):
        with self.db.lock:
            self.lease(self.db.conn,owner,actor,connection,generation)
            rows=self.db.conn.execute('SELECT * FROM delivery_messages WHERE recipient=? AND seq>? ORDER BY seq LIMIT ?',(actor,after,limit)).fetchall()
            return [{'type':'message.deliver','agentId':actor,'recipientSeq':r['seq'],'deliveryState':r['delivery_state'],
                     'message':json.loads(r['envelope']) if r['envelope'] else {'messageId':r['id'],'kind':r['kind'],'recipientSeq':r['seq'],'payloadAvailable':False}}
                    for r in rows]
