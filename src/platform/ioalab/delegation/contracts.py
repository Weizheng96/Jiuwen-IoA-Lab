"""Frozen contract authority and original task evidence, not participant execution."""
import json
from datetime import datetime,timezone

from a2c import objects
from a2c.card import card_digest,validate_card
from a2c.codec import parse_duration,parse_time
from a2c.common import Directory
from a2c.errors import A2CError
from a2c.jws import check_fresh,check_window,header_of
from a2c.slots import verify_card
from ..errors import LabError


SCHEMA='''CREATE TABLE IF NOT EXISTS delegation_contracts(
 admission TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,card TEXT NOT NULL,task TEXT,
 start_message TEXT,permit REAL,receipt TEXT,step TEXT);
CREATE TABLE IF NOT EXISTS delegation_step_answers(
 admission TEXT NOT NULL,request TEXT NOT NULL,message TEXT NOT NULL,answer TEXT NOT NULL,
 PRIMARY KEY(admission,request));'''


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def denied(message):raise LabError('forbidden',message,status=403)


class ContractAuthority:
    def __init__(self,db,identities,admissions,scopes,market):
        self.db,self.identities,self.admissions,self.scopes,self.market=db,identities,admissions,scopes,market
        with db.lock:db.conn.executescript(SCHEMA)

    def facts(self,conn,admission):
        row=conn.execute('SELECT * FROM delegation_contracts WHERE admission=?',(admission,)).fetchone()
        if not row:raise LabError('not_ready','Register the original effective card before this contract action',status=409)
        return row

    def keys(self,row):
        party=self.identities.service_party(row['service']);keys=Directory()
        keys.add(party['id'],{'keys':[party['jwk']]})
        keys.add(self.market.id,{'keys':[self.market.signer.jwk]})
        return keys

    def original(self,conn,row):return json.loads(self.admissions.intent(conn,row['origin'])['details'])

    def work_window(self,conn,row,facts):
        if facts['permit'] is not None:return
        template=self.original(conn,row)['template'];chain=verify_card(template,json.loads(facts['card']),self.keys(row))
        if self.admissions.clock()>chain.effective.timestamp()+parse_duration(template['deadlines']['startWithin']).total_seconds():
            raise LabError('expired','Original work.start window has ended',status=409)

    def card_in(self,conn,row,card):
        original=self.original(conn,row);template=original['template']
        saved=conn.execute('SELECT * FROM delegation_contracts WHERE admission=?',(row['id'],)).fetchone()
        try:
            validate_card(template,card)
            cd=card_digest(card)
            if saved:
                fixed=json.loads(saved['card'])
                if cd!=saved['digest'] or any(card['signatures'][key]!=fixed['signatures'][key] for key in ('1','2','3')):raise ValueError()
                return saved
            # A first observation is the effective original card, before any
            # execution. Later progress cannot introduce a different first chain.
            if row['state']!='committed' or card['fulfillment'] or any(card['signatures'][str(i)] for i in range(4,8)):raise ValueError()
            party=self.identities.service_party(row['service'])
            if card['provider']!=party['id'] or card['paymentProvider']!=self.admissions.payment_provider:raise ValueError()
            selection=self.scopes.contract_in(conn,self.identities.agent(row['client'])['owner_id'],row['client'],row['scope'],card)
            if card.get('user')!=selection['user']:raise ValueError()
            if template['formation']=='proposal':
                source=conn.execute('SELECT request FROM delegation_signed_requests WHERE admission=?',(row['id'],)).fetchone()
                offer=conn.execute('SELECT result FROM delegation_offers WHERE admission=?',(row['id'],)).fetchone()
                if not source or not offer:raise ValueError()
                validate_card(template,card,request=json.loads(source['request']))
                issued=json.loads(offer['result'])['card']
                if cd!=card_digest(issued) or any(card['signatures'][key]!=issued['signatures'][key] for key in ('2','3')):raise ValueError()
            elif cd!=row['binding']:raise ValueError()
            chain=verify_card(template,card,self.keys(row))
            if chain.effective is None or chain.effective>parse_time(template['validUntil']):raise ValueError()
        except (A2CError,KeyError,TypeError,ValueError):denied('The effective contract differs from its admitted original offer, selected value or signatures')
        conn.execute('INSERT INTO delegation_contracts(admission,digest,card) VALUES(?,?,?)',(row['id'],cd,encode(card)))
        return self.facts(conn,row['id'])

    def check_in(self,conn,value):
        payload=value['body'];op,body=payload['op'],payload['body']
        row=self.admissions.admission(conn,payload['admissionId'])
        if (value['sender'],value['recipient'],value.get('interactionId'))!=(row['client'],row['service'],row['interaction']):denied('Contract request changed its admitted participants')
        if row['state']!='committed':raise LabError('closed','The original contract admission is not active',status=409)
        original=self.original(conn,row)
        if op in ('work.start','acceptance.notify') or op=='ap2.checkout' and body.get('purpose') in ('deposit','installment'):
            facts=self.card_in(conn,row,body.get('card'))
        else:facts=self.facts(conn,row['id'])
        if 'taskId' in payload and payload['taskId']!=facts['task']:denied('Use this contract original delivery task')
        if op=='work.start':
            if facts['start_message'] and facts['start_message']!=value['messageId']:raise LabError('id_conflict','Resume the original work.start message',status=409)
            self.work_window(conn,row,facts)
        elif op=='ap2.checkout':
            if body.get('purpose') not in ('deposit','installment','termination'):denied('Unknown established contract checkout purpose')
            if body.get('purpose')=='deposit':self.work_window(conn,row,facts)
            if body.get('purpose')=='termination' and body.get('cardDigest')!=facts['digest']:denied('Checkout names another contract')
        elif op=='step.authorize':self.answer_in(conn,row,facts,payload)
        elif op=='delivery.fetch':
            if body.get('cardDigest')!=facts['digest'] or payload.get('taskId')!=facts['task'] or not facts['task']:denied('Fetch on the original card and delivery task')
            card=self.market.market.store.get('cards',facts['digest'])
            if type(body.get('period')) is not int or not card or not 1<=body['period']<=len(card['signatures']['5']):denied('This period has not actually been anchored')
        elif op=='acceptance.notify':self.anchored_prefix(facts,body['card'])
        elif op=='payment.notify':
            voucher=body.get('voucher')
            try:
                objects.verify_signed(voucher,self.market.keys.jwk(self.admissions.payment_provider['id'],header_of(voucher['signature']).get('kid')),signer=self.admissions.payment_provider['id'],expect_kid=True)
                if voucher.get('cardDigest')!=facts['digest']:raise ValueError()
            except (A2CError,KeyError,TypeError,ValueError):denied('Payment notice must carry this contract actual payment voucher')
        else:raise LabError('not_ready','This contract method has not been enabled',status=409)
        return row,original

    def accepted_in(self,conn,value):
        if value['body']['op']=='work.start':
            conn.execute('UPDATE delegation_contracts SET start_message=? WHERE admission=?',(value['messageId'],value['body']['admissionId']))
        elif value['body']['op']=='step.authorize':
            body=value['body']['body']['stepAuthorization'];aid=value['body']['admissionId']
            old=conn.execute('SELECT message FROM delegation_step_answers WHERE admission=? AND request=?',(aid,body['stepRequestDigest'])).fetchone()
            if old and old['message']!=value['messageId']:raise LabError('id_conflict','Resume the original answer to this step request',status=409)
            if not old:conn.execute('INSERT INTO delegation_step_answers VALUES(?,?,?,?)',(aid,body['stepRequestDigest'],value['messageId'],encode(body)))

    def started_in(self,conn,value):
        if value['body']['op']=='work.start':conn.execute('UPDATE delegation_contracts SET permit=COALESCE(permit,?) WHERE admission=?',(self.admissions.clock(),value['body']['admissionId']))

    def answer_in(self,conn,row,facts,payload):
        try:
            if not facts['step'] or not facts['task'] or payload.get('taskId')!=facts['task']:raise ValueError()
            request=json.loads(facts['step']);answer=payload['body']['stepAuthorization'];user=json.loads(facts['card'])['user']
            objects.verify_signed(answer,user['jwk'],signer=user['id'])
            now=datetime.fromtimestamp(self.admissions.clock(),timezone.utc)
            check_fresh(header_of(answer['signature']),now)
            check_window(header_of(answer['signature']),now,not_before=parse_time(header_of(request['signature'])['a2c-ts']))
            if answer.get('kind')!='step-authorization' or answer.get('jwk')!={key:value for key,value in user['jwk'].items() if key!='kid'} or type(answer.get('granted')) is not bool or answer.get('stepRequestDigest')!=objects.object_digest(request):raise ValueError()
            template=self.original(conn,row)['template']
            if self.admissions.clock()>parse_time(header_of(request['signature'])['a2c-ts']).timestamp()+parse_duration(template['deadlines']['authorizeWithin']).total_seconds():raise ValueError()
        except (A2CError,KeyError,TypeError,ValueError):denied('Step answer needs the bound original user, request and delivery task')

    def anchored_prefix(self,facts,card):
        original=self.market.market.store.get('cards',facts['digest'])
        count=len(card['signatures']['5'])
        if not original or not count or len(card['signatures']['4'])!=count or any(card['signatures'][key]!=original['signatures'][key][:count] for key in ('4','5')) or [p.get('manifest') for p in card['fulfillment'].get('periods',[])]!=[p.get('manifest') for p in original['fulfillment'].get('periods',[])[:count]]:
            denied('Use the actual immutable anchored delivery chain')

    def result_in(self,conn,row,reply):
        facts=self.facts(conn,row['id']);snapshot=reply.get('task');task=reply.get('taskId')
        if snapshot:
            if facts['task'] and task!=facts['task']:denied('Progress names a foreign delivery task')
            if snapshot['op']!='work.start':denied('Preserve the original delivery task creation method')
            foreign=conn.execute('SELECT 1 FROM delegation_contracts c JOIN delegation_admissions a ON a.id=c.admission WHERE a.service=? AND c.task=? AND c.admission!=?',(row['service'],task,row['id'])).fetchone()
            if foreign:denied('This service task already belongs to another contract')
            request=(snapshot.get('status') or {}).get('stepRequest') if snapshot['state']=='auth-required' else None
            if request:
                try:
                    party=self.identities.service_party(row['service'])
                    objects.verify_signed(request,party['jwk'],signer=party['id'],expect_kid=True)
                    template=self.original(conn,row)['template']
                    if request.get('kind')!='step-request' or request.get('cardDigest')!=facts['digest'] or type(request.get('period')) is not int or not 1<=request['period']<=len(template['deliverables']) or request.get('step') not in [step['name'] for step in template.get('authorizableSteps',[])]:raise ValueError()
                except (A2CError,KeyError,TypeError,ValueError):denied('Step request differs from the admitted contract and service')
            conn.execute('UPDATE delegation_contracts SET task=?,step=? WHERE admission=?',(task,encode(request) if request else None,row['id']))
        receipt=reply['body'].get('receipt')
        if receipt:
            try:
                party=self.identities.service_party(row['service'])
                objects.verify_signed(receipt,party['jwk'],signer=party['id'],expect_kid=True)
                if receipt.get('cardDigest')!=facts['digest']:raise ValueError()
            except (A2CError,KeyError,TypeError,ValueError):denied('Work receipt differs from this original contract')
            conn.execute('UPDATE delegation_contracts SET receipt=? WHERE admission=?',(encode(receipt),row['id']))

    def payment_in(self,conn,owner,actor,aid,payment,*,original):
        row=self.admissions.admission(conn,aid);facts=self.facts(conn,row['id'])
        raw=original;source=self.original(conn,row)
        if row['client']!=actor or self.identities.agent(actor)['owner_id']!=owner or payment.payer!=owner or payment.ref!=facts['digest']:
            denied('Payment differs from the original effective contract and admission')
        old=conn.execute('SELECT * FROM admission_payments WHERE payment_id=?',(payment.payment_id,)).fetchone()
        if old:
            if (old['admission'],old['fingerprint'])!=(aid,payment.fingerprint):raise LabError('id_conflict','Original contract payment changed',status=409)
            return False
        if row['state']!='committed' or payment.provider!=source['template']['provider'] or payment.scope_digest!=row['scope'] or payment.currency!=source['template']['pricing']['currency'] or json.loads(payment.client).get('id')!=actor:
            denied('New payment differs from the original contract or scope')
        if not isinstance(raw,dict):denied('Original AP2 contract evidence is required')
        self.card_in(conn,row,raw.get('card'))
        if payment.purpose=='deposit':self.work_window(conn,row,facts)
        if payment.purpose=='installment':self.anchored_prefix(facts,raw['card'])
        if not payment.expires:raise LabError('unknown_result','Restore the original payment admission association',status=409)
        conn.execute('INSERT INTO admission_payments VALUES(?,?,?)',(payment.payment_id,payment.fingerprint,aid))
        return True

    def market_in(self,conn,owner,actor,payload):
        row=self.admissions.admission(conn,payload['admissionId'])
        if row['service']!=actor or self.identities.agent(actor)['owner_id']!=owner:denied('Only the original service can anchor or close its contract')
        facts=self.card_in(conn,row,payload['body']['card']);card=payload['body']['card']
        if row['state']!='committed' or facts['permit'] is None:raise LabError('not_ready','Start the original effective contract first',status=409)
        if payload['op']=='delivery.anchor':
            previous=self.market.market.store.get('cards',facts['digest'])
            count=len(previous['signatures']['5']) if previous else 0
            if len(card['signatures']['5'])!=count or len(card['signatures']['4'])!=count+1 or previous and any(card['signatures'][key][:count]!=previous['signatures'][key] for key in ('4','5')):
                raise LabError('id_conflict','Recover the existing original anchor instead of replacing its period',status=409)
        elif payload['op']=='card.close':self.anchored_prefix(facts,card)
        return row,self.original(conn,row)['template']
