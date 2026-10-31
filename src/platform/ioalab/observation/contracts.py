"""Verified display copies; no contract execution, signing, payment or model calls."""
import hashlib
import json
import time

from a2c.card import card_digest,validate_card
from a2c.codec import parse_time
from a2c.common import Directory
from a2c.errors import A2CError
from a2c.jws import header_of
from a2c.slots import verify_card

from ..payments import money


SCHEMA='''CREATE TABLE IF NOT EXISTS observation_contracts(
 admission TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,card TEXT NOT NULL,card_hash TEXT NOT NULL,
 effective INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);'''


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def fingerprint(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()


def extends(previous,current):
    for key,old in previous['signatures'].items():
        new=current['signatures'][key]
        if isinstance(old,list):
            if new[:len(old)]!=old:return False
        elif old and new!=old:return False
    return True


class ContractProjection:
    def __init__(self,db,identities,market,*,clock=time.time):
        self.db,self.identities,self.market,self.clock=db,identities,market,clock
        with db.lock:db.conn.executescript(SCHEMA)

    def original_in(self,conn,admission):
        return conn.execute('''SELECT a.*,i.details FROM delegation_admissions a
            JOIN delegation_intents i ON i.origin=a.origin WHERE a.id=?''',(admission,)).fetchone()

    def verified_in(self,conn,row,card):
        template=json.loads(row['details'])['template']
        client=self.identities.agent(row['client']);meta=json.loads(client['meta'])
        party=self.identities.service_party(row['service'])
        if (card.get('templateDigest')!=row['template'] or card.get('provider')!=party['id']
                or card.get('client',{}).get('id')!=row['client']
                or card['client']['jwk']!=meta.get('clientJwk')
                or card.get('user',{}).get('jwk')!=meta.get('userJwk')):return None
        source=conn.execute('SELECT request FROM delegation_signed_requests WHERE admission=?',(row['id'],)).fetchone()
        request=json.loads(source['request']) if source else None
        validate_card(template,card,**({'request':request} if request else {}))
        cd=card_digest(card)
        if template['formation']=='proposal':
            offer=conn.execute('SELECT result FROM delegation_offers WHERE admission=?',(row['id'],)).fetchone()
            if not offer or card.get('requestDigest')!=row['binding']:return None
            issued=json.loads(offer['result'])['card']
            if cd!=card_digest(issued) or any(card['signatures'][key]!=issued['signatures'][key] for key in ('2','3')):return None
        elif cd!=row['binding']:return None
        keys=Directory();keys.add(party['id'],{'keys':[party['jwk']]})
        keys.add(self.market.id,{'keys':[self.market.signer.jwk]})
        kwargs={'request_ts':parse_time(header_of(request['signature'])['a2c-ts'])} if request else {}
        return cd,verify_card(template,card,keys,**kwargs)

    def record_in(self,conn,admission,card):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Observation copy requires its original application transaction')
        row=self.original_in(conn,admission)
        if not row or not isinstance(card,dict):return False
        try:
            verified=self.verified_in(conn,row,card)
            if verified is None:return False
            cd,chain=verified;raw=encoded(card);checksum=fingerprint(raw)
            old=conn.execute('SELECT * FROM observation_contracts WHERE admission=?',(admission,)).fetchone()
            if old:
                if old['digest']!=cd or old['card_hash']!=fingerprint(old['card']):return False
                if old['card_hash']==checksum:return False
                if not extends(json.loads(old['card']),card):return False
        except (A2CError,KeyError,TypeError,ValueError):return False
        now=self.clock()
        conn.execute('''INSERT INTO observation_contracts VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(admission) DO UPDATE SET card=excluded.card,card_hash=excluded.card_hash,
                effective=excluded.effective,updated=excluded.updated''',
            (admission,cd,raw,checksum,int(chain.effective is not None),row['created'],now))
        return True

    def rebuild(self):
        """Derive display copies from retained originals; never invent a final card."""
        changed=0
        for row in self.db.rows('SELECT id,interaction FROM delegation_admissions ORDER BY created'):
            cards=[]
            offer=self.db.one('SELECT result FROM delegation_offers WHERE admission=?',row['id'])
            if offer:cards.append(json.loads(offer['result']).get('card'))
            original=self.db.one('SELECT card FROM delegation_contracts WHERE admission=?',row['id'])
            if original:cards.append(json.loads(original['card']))
            for message in self.db.rows("SELECT kind,envelope FROM delivery_messages WHERE interaction=? AND kind IN ('a2c.request','a2c.result') AND envelope IS NOT NULL ORDER BY created,seq",row['interaction']):
                body=json.loads(message['envelope'])['body']
                content=body.get('body',{}) if message['kind']=='a2c.request' else body.get('reply',{}).get('body',{})
                if isinstance(content.get('card'),dict):cards.append(content['card'])
            for card in tuple(cards):
                if card:
                    try:cd=card_digest(card)
                    except (A2CError,KeyError,TypeError,ValueError):continue
                    anchored=self.market.market.store.get('cards',cd)
                    if anchored:cards.append(anchored)
            for card in cards:
                with self.db.tx() as conn:changed+=self.record_in(conn,row['id'],card)
        return changed

    def income(self,owner):
        records=self.db.rows("""SELECT a.service,p.purpose,p.amount FROM admission_payments link
            JOIN payment_records p ON p.payment_id=link.payment_id JOIN delegation_admissions a ON a.id=link.admission
            JOIN delegation_intents i ON i.origin=a.origin JOIN agents c ON c.id=a.client JOIN agents s ON s.id=a.service
            LEFT JOIN delegation_contracts f ON f.admission=a.id
            WHERE s.owner_id=? AND p.payee_id=s.owner_id AND p.payer_id=c.owner_id
            AND p.provider=json_extract(s.meta,'$.protocolId') AND p.currency='CNY'
            AND p.currency=json_extract(i.details,'$.template.pricing.currency')
            AND ((p.purpose='proposal-fee' AND p.ref=a.binding)
                OR (p.purpose IN ('deposit','installment') AND p.ref=f.digest))""",owner)
        totals={}
        for row in records:
            value=totals.setdefault(row['service'],{'earned':money(0),'commissionEarned':money(0),'proposalFeesEarned':money(0)})
            value['earned']+=money(row['amount'])
            value['proposalFeesEarned' if row['purpose']=='proposal-fee' else 'commissionEarned']+=money(row['amount'])
        return {actor:{key:str(amount) for key,amount in value.items()} for actor,value in totals.items()}

    def service_completions(self,owner):
        ledger=self.market.market.store.all('ledger');counts={}
        rows=self.db.rows("""SELECT a.service,f.digest FROM delegation_contracts f
            JOIN delegation_admissions a ON a.id=f.admission JOIN agents s ON s.id=a.service WHERE s.owner_id=?""",owner)
        for row in rows:
            if ledger.get(row['digest'],{}).get('outcome')=='completed':counts[row['service']]=counts.get(row['service'],0)+1
        return counts

    def intents(self,owner=None,*,limit=200):
        where='v.admission IS NULL AND f.admission IS NULL';values=[]
        if owner is not None:where+=' AND (c.owner_id=? OR s.owner_id=?)';values.extend((owner,owner))
        rows=self.db.rows("""SELECT i.origin,i.closed,i.expires,i.details,i.binding,a.id AS admission,a.state AS admission_state,
            a.hold_until,rel.id AS interaction,c.id AS client,s.id AS service,c.owner_id,s.owner_id AS service_owner_id,
            c.name AS client_name,s.name AS service_name,m.created,m.delivery_state,m.request_state,
            NULL AS digest FROM delegation_intents i JOIN agents c ON c.id=i.sender JOIN agents s ON s.id=i.service
            LEFT JOIN delivery_messages m ON m.id=i.origin LEFT JOIN delivery_interactions rel ON rel.origin=i.origin
            LEFT JOIN delegation_admissions a ON a.origin=i.origin LEFT JOIN observation_contracts v ON v.admission=a.id
            LEFT JOIN delegation_contracts f ON f.admission=a.id WHERE """+where+' ORDER BY COALESCE(m.created,a.created,0) DESC LIMIT ?',*values,limit)
        result=[]
        for row in rows:
            template=json.loads(row['details'])['template'];request=row['request_state'] or 'unavailable'
            phase='等待服务方接收' if row['delivery_state']=='queued' else '服务方正在处理意向'
            if row['admission_state']=='held':phase='已预留接单名额'
            if row['admission_state']=='committed':phase='等待正式提案' if template['formation']=='proposal' else '等待服务方复签'
            if row['expires']<=self.clock() and not row['admission']:phase='意向已过期'
            if row['admission_state']=='held' and row['hold_until']<=self.clock():phase='预留期限已过'
            if row['closed'] or row['admission_state']=='released':phase='意向已结束，未形成合同'
            if request in ('rejected','failed'):phase='服务方未接受意向'
            if request=='expired':phase='意向已过期'
            unknown=self.unknown(row['admission']) if row['admission'] else False
            if unknown:phase='拟约结果待核对'
            fee=sum((money(p['amount']) for p in self.payments(row) if p['purpose']=='proposal-fee'),money(0)) if row['admission'] else money(0)
            result.append({'originMessageId':row['origin'],'admissionId':row['admission'],'interactionId':row['interaction'],
                'owner_id':row['owner_id'],'assistant_id':row['client'],'serviceAgentId':row['service'],
                'clientName':row['client_name'],'providerName':row['service_name'],'provider_id':template['provider'],'created':row['created'],
                'deliveryState':row['delivery_state'],'requestState':request,'admissionState':row['admission_state'],
                'executionState':'unknown' if unknown else None,'phase':phase,'proposalFeePaid':str(fee)})
        return result

    def unknown(self,admission):
        row=self.db.one("""SELECT d.envelope FROM delegation_protocol_calls p JOIN delivery_messages d ON d.id=p.last_result
            WHERE p.admission=? AND p.state IN ('working','auth-required','processing') ORDER BY d.created DESC LIMIT 1""",admission)
        return bool(row and row['envelope'] and json.loads(row['envelope'])['body'].get('executionError'))

    def rows(self,owner=None,*,limit=200,digest=None):
        clauses=[];values=[]
        if owner is not None:clauses.append('(c.owner_id=? OR s.owner_id=?)');values.extend((owner,owner))
        if digest is not None:clauses.append('v.digest=?');values.append(digest)
        where='WHERE '+' AND '.join(clauses) if clauses else ''
        return self.db.rows('''SELECT v.*,a.client,a.service,a.interaction,a.state AS admission_state,a.binding,
            i.details,c.owner_id,s.owner_id AS service_owner_id,c.name AS assistant_name,s.name AS provider_name
            FROM observation_contracts v JOIN delegation_admissions a ON a.id=v.admission
            JOIN delegation_intents i ON i.origin=a.origin JOIN agents c ON c.id=a.client
            JOIN agents s ON s.id=a.service '''+where+' ORDER BY v.updated DESC LIMIT ?',*values,limit)

    def payments(self,row):
        template=json.loads(row['details'])['template']
        records=self.db.rows('''SELECT p.* FROM admission_payments a JOIN payment_records p ON p.payment_id=a.payment_id
            WHERE a.admission=? AND p.payer_id=? AND p.payee_id=? AND p.provider=? AND p.currency=?
            ORDER BY p.created,p.period''',row['admission'],row['owner_id'],row['service_owner_id'],template['provider'],template['pricing']['currency'])
        return [{'at':r['created'],'amount':r['amount'],'purpose':r['purpose'],'period':r['period'],
                 'payment_id':r['payment_id'],'currency':r['currency']} for r in records
                if r['purpose'] in ('deposit','installment','proposal-fee')
                and r['ref']==(row['binding'] if r['purpose']=='proposal-fee' else row['digest'])]

    def view(self,row,*,private=False):
        available=fingerprint(row['card'])==row['card_hash']
        card=json.loads(row['card']) if available else None
        base={'card_digest':row['digest'],'admissionId':row['admission'],'interactionId':row['interaction'],
            'owner_id':row['owner_id'],'assistant_id':row['client'],'serviceAgentId':row['service'],
            'assistant_name':row['assistant_name'],'provider_name':row['provider_name'],'providerName':row['provider_name'],
            'clientName':row['assistant_name'],'created':row['created'],'updated':row['updated'],
            'evidenceStatus':'verified' if available else 'unavailable','effective':bool(row['effective'])}
        if not card:
            payments=self.payments(row);template=json.loads(row['details'])['template']
            ending=self.market.market.store.get('ledger',row['digest'])
            return {**base,'state':ending['outcome'] if ending else 'unknown','phase':'合同副本待恢复','signatures':{},'payments':payments,
                'paid':str(sum((money(p['amount']) for p in payments if p['purpose']!='proposal-fee'),money(0))),
                'proposalFeePaid':str(sum((money(p['amount']) for p in payments if p['purpose']=='proposal-fee'),money(0))),
                'earned':str(sum((money(p['amount']) for p in payments),money(0))),
                'total':None,'provider_id':template['provider'],'formation':template['formation']}
        template=json.loads(row['details'])['template']
        signatures={key:[bool(s) for s in value] if isinstance(value,list) else bool(value) for key,value in card['signatures'].items()}
        payments=self.payments(row);commission=[p for p in payments if p['purpose']!='proposal-fee']
        facts=self.db.one('SELECT receipt,step FROM delegation_contracts WHERE admission=?',row['admission']) or {}
        ending=self.market.market.store.get('ledger',row['digest'])
        state='signed' if row['effective'] else 'offered'
        if facts.get('receipt'):state='started'
        if signatures['5']:state='delivered'
        if signatures['7']:state='accepted'
        if ending:state=ending['outcome']
        phase={'signed':'已成约','offered':'待确认合同','started':'履约中','delivered':'等待验收','accepted':'已签验收',
               'completed':'已结清','client-terminated':'已解除','arbitrated':'已裁定'}.get(state,state)
        if not row['effective']:phase='等待服务方复签' if signatures['1'] else '等待用户确认合同'
        waiting=json.loads(facts['step']) if facts.get('step') else None
        if waiting and not ending:phase='等待用户授权'
        result={**base,'provider_id':card['provider'],'template_digest':card['templateDigest'],
            'formation':template['formation'],'total':str(card['derived']['total']),'state':state,'phase':phase,
            'signatures':signatures,'periods':len(card['derived']['periods']),'payments':payments,
            'paid':str(sum((money(p['amount']) for p in commission),money(0))),
            'proposalFeePaid':str(sum((money(p['amount']) for p in payments if p['purpose']=='proposal-fee'),money(0))),
            'earned':str(sum((money(p['amount']) for p in payments),money(0)))}
        if ending and ending.get('outcome')=='completed' and len(signatures['7'])<len(card['derived']['periods']):
            result.update(evidenceStatus='partial',phase='已结清 · 部分历史证据缺失')
        result['executionState']='unknown' if not ending and self.unknown(row['admission']) else None
        if result['executionState']=='unknown':result['phase']='执行结果待核对'
        if private:result.update(card=card,detail={'card':card,'trust':'verified'},stepRequest=waiting)
        return result

    def list(self,owner=None,*,private=False,limit=200):
        if private and owner is None:raise ValueError('Private contract views require an account')
        return [self.view(row,private=private) for row in self.rows(owner,limit=limit)]
