"""Capture accepted metadata inside its transaction; emit only after commit."""
import json

from a2c.card import card_digest
from a2c.errors import A2CError
from .contracts import encoded,fingerprint


class ActivityViews:
    def __init__(self,contracts):self.contracts=contracts

    def snapshot_in(self,conn,admission,card):
        if not isinstance(card,dict) or not admission:return {}
        row=conn.execute('SELECT * FROM observation_contracts WHERE admission=?',(admission,)).fetchone()
        if not row or fingerprint(encoded(card))!=row['card_hash'] or fingerprint(row['card'])!=row['card_hash']:return {}
        original=self.contracts.original_in(conn,admission)
        ended=self.contracts.market.market.store.get('ledger',row['digest'])
        facts=conn.execute('SELECT receipt,step FROM delegation_contracts WHERE admission=?',(admission,)).fetchone()
        state='signed' if row['effective'] else 'offered'
        if facts and facts['receipt']:state='started'
        if card['signatures']['5']:state='delivered'
        if card['signatures']['7']:state='accepted'
        if ended:state=ended['outcome']
        return {'assistant':original['client'],'provider':original['service'],'evidenceTrust':'verified',
            'signatures':{k:[bool(s) for s in v] if isinstance(v,list) else bool(v) for k,v in card['signatures'].items()},
            'state':state,'total':str(card['derived']['total']),'periods':len(card['derived']['periods'])}

    def capture_in(self,conn,frame,result,*,inserted=False,fresh=False):
        kind=frame['type'];payload=frame.get('payload',{});actor=frame['agentId']
        if kind=='message.send' and inserted:
            row=conn.execute('SELECT * FROM delivery_messages WHERE id=?',(result['messageId'],)).fetchone()
            value=json.loads(row['envelope']);body=value['body']
            event={'kind':'message','op':'message.queued','sender':actor,'receiver':row['recipient'],
                'outcome':'queued','detail':{'source':'resident','trust':'verified','messageId':row['id'],
                'interactionId':row['interaction'],'kind':row['kind']}}
            if row['kind']=='a2c.request':
                event.update(kind='a2c',op=body['op'])
                event['detail']['protocolOp']=body['op'];content=body['body']
                admission=body['admissionId']
            elif row['kind']=='a2c.result':
                call=conn.execute('SELECT * FROM delegation_protocol_calls WHERE message=?',(row['in_reply'],)).fetchone()
                event.update(kind='a2c',op=call['op']+'.result',outcome=body['reply'].get('state') or 'completed')
                event['detail']['protocolOp']=call['op'];content=body['reply']['body'];admission=call['admission']
            elif row['kind']=='message.result':
                event.update(op='message.'+body['outcome'],outcome=body['outcome'])
                event['detail']['messageId']=row['in_reply']
                return [event]
            else:
                if row['kind']=='message.request':event['detail']['interface']=body.get('interfaceName')
                elif row['kind'].startswith('delegation.'):
                    event.update(kind='delegation',op=row['kind'])
                    if row['kind']=='delegation.result':event['outcome']=body['outcome']
                else:event.update(kind='delivery',op='interaction.opened')
                return [event]
            card=content.get('card')
            if isinstance(card,dict):
                try:event['card_digest']=card_digest(card)
                except (A2CError,KeyError,TypeError,ValueError):pass
                event['detail'].update(self.snapshot_in(conn,admission,card))
            event['period']=content.get('period')
            return [event]
        if kind=='request.start' and result.get('resumed') is False:
            row=conn.execute('SELECT * FROM delivery_messages WHERE id=?',(payload['messageId'],)).fetchone()
            if row['kind']=='message.request':
                return [{'kind':'message','op':'message.processing','sender':row['sender'],'receiver':actor,'outcome':'processing',
                    'detail':{'source':'resident','trust':'verified','messageId':row['id'],'interactionId':row['interaction']}}]
        if kind=='a2c.market' and fresh:
            row=self.contracts.original_in(conn,payload['admissionId'])
            card=result.get('card') or payload['body']['card']
            detail={'source':'resident','trust':'verified','protocolOp':payload['op'],'interactionId':row['interaction']}
            detail.update(self.snapshot_in(conn,row['id'],card))
            return [{'kind':'a2c','op':payload['op'],'sender':actor,'receiver':self.contracts.market.id,
                'outcome':'completed','card_digest':card_digest(card),'period':len(card['signatures']['5']) or None,'detail':detail}]
        return []
