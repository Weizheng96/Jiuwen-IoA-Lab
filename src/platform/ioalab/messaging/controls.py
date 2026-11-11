"""Operation receipts, this module's control kinds and accepted-activity recording. Part of DeliveryBroker."""
import hashlib
import json
import logging

from ..errors import LabError
from .envelope import encode

log=logging.getLogger(__package__+'.delivery')


class Controls:
    def _operation(self,conn,actor,kind,operation,payload,apply):
        if not isinstance(operation,str) or not 1<=len(operation)<=128:raise LabError('invalid_input','A stable operationId is required')
        fingerprint=hashlib.sha256(encode(payload).encode()).hexdigest()
        old=conn.execute('SELECT * FROM delivery_operations WHERE actor=? AND kind=? AND id=?',(actor,kind,operation)).fetchone()
        if old:
            if old['fingerprint']!=fingerprint:raise LabError('id_conflict','Operation parameters changed',status=409)
            return json.loads(old['result'])
        key=hashlib.sha256(encode([actor,kind,operation]).encode()).hexdigest()
        if conn.execute('SELECT 1 FROM delivery_operation_ids WHERE digest=?',(key,)).fetchone():
            raise LabError('history_gap','Operation receipt has left retained history',status=409,recovery='restore_state')
        result=apply()
        conn.execute('INSERT INTO delivery_operation_ids VALUES(?)',(key,))
        conn.execute('INSERT INTO delivery_operations VALUES(?,?,?,?,?,?,?)',
                     (actor,kind,operation,fingerprint,encode(result),self.clock(),self.clock()+self.s.message_metadata_days*86400))
        return result

    def publish(self,owner,operation,payload):
        actor=self.identities.assistant(owner)
        with self.db.tx() as conn:
            result=self._operation(conn,actor,'interface.publish',operation,payload,lambda:self.interfaces.publish(conn,owner,payload))
        return result

    def operation(self,owner,actor,kind,operation):
        """Original result of an operation this module accepted (its control kinds and interface publication)."""
        self.identities.owned(owner,actor)
        if not isinstance(kind,str) or not isinstance(operation,str):
            raise LabError('invalid_input','Operation type and identity must be strings')
        row=self.db.one('SELECT result FROM delivery_operations WHERE actor=? AND kind=? AND id=?',actor,kind,operation)
        if not row:
            key=hashlib.sha256(encode([actor,kind,operation]).encode()).hexdigest()
            if self.db.one('SELECT 1 FROM delivery_operation_ids WHERE digest=?',key):
                raise LabError('history_gap','Operation receipt has left retained history',status=409,recovery='restore_state')
            raise LabError('not_ready','Operation has no accepted result',status=404,recovery='retry_original')
        return json.loads(row['result'])

    @staticmethod
    def control_frame(generations,frame):
        """Check the frame's agent and generation against this connection; return (actor,kind,payload)."""
        actor,kind=frame.get('agentId'),frame.get('type')
        if not isinstance(kind,str):raise LabError('invalid_input','Control operation type must be a string')
        if not isinstance(actor,str) or actor not in generations or not isinstance(frame.get('generation'),int) or isinstance(frame['generation'],bool) or frame['generation']!=generations[actor]:
            raise LabError('stale_generation','Invalid connection identity or generation',status=409,recovery='stop_executor')
        payload=frame.get('payload',{})
        if not isinstance(payload,dict):raise LabError('invalid_input','Control payload must be an object')
        return actor,kind,payload

    def renew_in(self,conn,actor):
        """An accepted control operation renews the execution lease inside its transaction."""
        conn.execute('UPDATE delivery_leases SET expires=? WHERE actor=?',(self.clock()+self.s.ws_lease_seconds,actor))

    def control_in(self,conn,actor,kind,operation,payload,generation):
        """Commit one of CONTROLS after the caller rechecked the lease; returns (result, activity facts or None)."""
        handlers={'message.send':self._send,'message.ack':self._ack,'request.start':self._start,'interaction.close':self._close,'contract.bind':self._bind_contract}
        fresh=not conn.execute('SELECT 1 FROM delivery_operations WHERE actor=? AND kind=? AND id=?',(actor,kind,operation)).fetchone()
        prior=conn.execute('SELECT 1 FROM delivery_messages WHERE id=?',(payload.get('messageId') if isinstance(payload.get('messageId'),str) else None,)).fetchone() if kind=='message.send' else None
        result=self._operation(conn,actor,kind,operation,payload,lambda:handlers[kind](conn,actor,payload,generation))
        if kind=='message.send':return result,{'inserted':not prior and result.get('messageId')==payload.get('messageId')}
        return result,({} if kind=='request.start' and fresh else None)

    def activity_in(self,conn,frame,result,**facts):
        """Record accepted-control activity with the operation; publish_activity runs after commit."""
        if not self.observe_in:return []
        activity=self.observe_in(conn,frame,result,**facts)
        if activity and not self.record_in:raise RuntimeError('Activity history requires its owned transaction writer')
        return [self.record_in(conn,**event) for event in activity]

    def publish_activity(self,recorded):
        if not self.publish_event:return
        for event in recorded:
            try:self.publish_event(event)
            except Exception:
                # The accepted operation, original response and event are already
                # durable. A display/file/subscriber fault must not undo acceptance
                # or cause a caller to execute the business a second time.
                log.exception('Activity is persisted; live publication failed for event %s',event['seq'])
