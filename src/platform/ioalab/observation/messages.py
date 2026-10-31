"""Account-authorized read views over the reliable message center, not its receiver."""
import json
import time


class MessageViews:
    def __init__(self,db,*,clock=time.time):self.db,self.clock=db,clock

    def rows(self,owner=None,*,message_id=None,limit=200):
        where="m.kind='message.request'";values=[]
        if owner is not None:
            where+=' AND (sa.owner_id=? OR ra.owner_id=?)';values.extend((owner,owner))
        if message_id is not None:where+=' AND m.id=?';values.append(message_id)
        return self.db.rows('''SELECT m.*,sa.owner_id AS sender_owner,ra.owner_id AS receiver_owner,
            su.username AS sender_username,su.display_name AS sender_name,
            ru.username AS receiver_username,ru.display_name AS receiver_name,
            i.interface AS interface_name,i.revision AS interface_revision
            FROM delivery_messages m JOIN agents sa ON sa.id=m.sender JOIN agents ra ON ra.id=m.recipient
            JOIN users su ON su.id=sa.owner_id JOIN users ru ON ru.id=ra.owner_id
            LEFT JOIN delivery_interactions i ON i.id=m.interaction
            WHERE '''+where+' ORDER BY m.created DESC,m.seq DESC LIMIT ?',*values,limit)

    def view(self,row,*,private=False):
        envelope=json.loads(row['envelope']) if row['envelope'] else None
        body=envelope['body'] if envelope else {}
        request=row['request_state'];delivery=row['delivery_state']
        if row['expires']<=self.clock():
            if request=='pending' and row['started'] is None:request='expired'
            if delivery=='queued':delivery='expired'
        value={'id':row['id'],'messageId':row['id'],'interactionId':row['interaction'],
            'sender':row['sender_owner'],'receiver':row['receiver_owner'],
            'senderAgentId':row['sender'],'receiverAgentId':row['recipient'],
            'senderUsername':row['sender_username'],'receiverUsername':row['receiver_username'],
            'senderDisplayName':row['sender_name'],'receiverDisplayName':row['receiver_name'],
            'interface':body.get('interfaceName',row['interface_name']),
            'revision':body.get('revision',row['interface_revision']),
            'created':row['created'],'expires':row['expires'],'deliveryState':delivery,
            'requestState':request,'state':request,'readState':'unknown','payloadAvailable':envelope is not None}
        if private:
            value.update(payload=body.get('input') if envelope else None,
                         result=json.loads(row['result']) if row['result'] else None)
        return value

    def list(self,owner=None):return [self.view(row,private=owner is not None) for row in self.rows(owner)]

    def detail(self,message_id):
        rows=self.rows(message_id=message_id,limit=1)
        return self.view(rows[0],private=True) if rows else None
