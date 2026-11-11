"""Retention windows and the periodic expiry and cleanup pass. Part of DeliveryBroker."""


class Retention:
    def _retain(self,conn,interaction,origin,sender,recipient):
        conn.execute('UPDATE delivery_messages SET retain_until=MAX(retain_until,?) WHERE interaction=? OR id=? OR origin=?',
                     (self.clock()+self.s.message_retention_days*86400,interaction,origin,origin))
        conn.execute('UPDATE delivery_operations SET retain_until=MAX(retain_until,?) WHERE actor IN (?,?)',
                     (self.clock()+self.s.message_metadata_days*86400,sender,recipient))

    def cleanup(self):
        now=self.clock();changed=False
        with self.db.tx() as conn:
            conn.execute("UPDATE delivery_messages SET request_state='expired',retain_until=MAX(retain_until,?) WHERE kind IN ('message.request','delegation.intent') AND started IS NULL AND expires<=? AND request_state='pending'",
                         (now+self.s.message_retention_days*86400,now))
            conn.execute("UPDATE delivery_messages SET delivery_state='expired' WHERE expires<=? AND delivery_state='queued'",(now,))
            if self.admissions is not None and self.protocol is not None:
                for admission in self.admissions.expired_proposals_in(conn):
                    interaction=conn.execute('SELECT * FROM delivery_interactions WHERE id=?',(admission['interaction'],)).fetchone()
                    if interaction is None or interaction['state']!='open' or not self.protocol.proposal_finished_in(conn,admission['id'],self.protocol_history_in):continue
                    self.admissions.finish_in(conn,admission['id'])
                    self._end(conn,interaction,'proposal-expired');changed=True
            for interaction in conn.execute("SELECT * FROM delivery_interactions WHERE state='open' AND deadline<=?",(now,)).fetchall():
                if interaction['kind']=='delegation':
                    if not self.admissions.can_close_in(conn,interaction['origin']):continue
                    self.admissions.close_in(conn,interaction['origin'])
                self._end(conn,interaction,'deadline');changed=True
            protected="COALESCE(interaction IN (SELECT id FROM delivery_interactions WHERE state='open'),0) OR COALESCE(interaction IN (SELECT interaction FROM delivery_messages WHERE kind='message.request' AND request_state IN ('processing','unknown')),0) OR (kind='message.request' AND request_state IN ('processing','unknown'))"
            conn.execute(f'UPDATE delivery_messages SET envelope=NULL,result=NULL WHERE retain_until<=? AND NOT ({protected})',(now,))
            cutoff=now-self.s.message_metadata_days*86400
            # Retain the complete relation while any request may still have external effects.
            active="SELECT sender FROM delivery_messages WHERE "+protected+" UNION SELECT recipient FROM delivery_messages WHERE "+protected
            conn.execute(f'DELETE FROM delivery_operations WHERE retain_until<? AND actor NOT IN ({active})',(now,))
            eligible=f'MAX(created,retain_until-?)<? AND NOT ({protected})'
            # Only remove a continuous prefix. A retained earlier sequence is a barrier,
            # including when its body is already redacted or its execution is unknown.
            conn.execute(f'''WITH barriers AS (
                SELECT recipient,MIN(seq) AS first_kept FROM delivery_messages
                WHERE NOT ({eligible}) GROUP BY recipient
            ) DELETE FROM delivery_messages WHERE {eligible}
              AND seq<COALESCE((SELECT first_kept FROM barriers WHERE barriers.recipient=delivery_messages.recipient),9223372036854775807)''',
                         (self.s.message_retention_days*86400,cutoff,self.s.message_retention_days*86400,cutoff))
            conn.execute('DELETE FROM delivery_closed_origins WHERE closed_at<? AND origin NOT IN (SELECT id FROM delivery_messages)',(cutoff,))
            conn.execute("DELETE FROM delivery_interactions WHERE state='closed' AND closed_at<? AND id NOT IN (SELECT interaction FROM delivery_messages WHERE interaction IS NOT NULL)",(cutoff,))
        if changed:self.notify()
