"""Frozen contract authority and original task evidence, not participant execution."""
import copy
import json
from datetime import datetime,timezone

from a2c import objects
from a2c.card import card_digest,validate_card
from a2c.codec import parse_duration,parse_time
from a2c.common import Directory
from a2c.errors import A2CError
from a2c.jws import check_fresh,check_window,header_of
from a2c.slots import verify_card
from a2c.payment.terms import verify_quote
from ..errors import LabError




def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def denied(message):raise LabError('forbidden',message,status=403)


def evidence_extends(previous,current):
    for name in ('receipt','termination'):
        value=previous.get('fulfillment',{}).get(name)
        if value is not None and current.get('fulfillment',{}).get(name)!=value:return False
    for key,old in previous['signatures'].items():
        new=current['signatures'][key]
        if isinstance(old,list):
            if new[:len(old)]!=old:return False
        elif old and new!=old:return False
    return True

def current_card(facts):return json.loads(facts['progress'] or facts['card'])


class ContractAuthority:
    def __init__(self,db,identities,admissions,scopes,market,payments):
        self.db,self.identities,self.admissions,self.scopes,self.market,self.payments=db,identities,admissions,scopes,market,payments


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

    def evidence_in(self,conn,admission):
        """An isolated current original, never a display projection or a write."""
        facts=conn.execute('SELECT * FROM delegation_contracts WHERE admission=?',(admission,)).fetchone()
        if facts is None:return None
        value=dict(facts);card=current_card(facts)
        if facts['receipt']:
            receipt=json.loads(facts['receipt']);previous=card['fulfillment'].get('receipt')
            if previous is not None and previous!=receipt:denied('Original work receipts disagree')
            card['fulfillment']['receipt']=receipt
        value.update(card=card,initial=json.loads(facts['card']))
        return value

    def work_window(self,conn,row,facts):
        if facts['permit'] is not None:return
        template=self.original(conn,row)['template'];chain=verify_card(template,json.loads(facts['card']),self.keys(row))
        if self.admissions.clock()>chain.effective.timestamp()+parse_duration(template['deadlines']['startWithin']).total_seconds():
            raise LabError('expired','Original work.start window has ended',status=409)

    def unselected_offer_in(self,conn,row,card):
        """Absence of original selection, contract/start authority and payments."""
        owner=self.identities.agent(row['client'])['owner_id'];cd=card_digest(card)
        return (self.scopes.selected_card_in(conn,owner,row['client'],row['scope'])!=cd
                and self.evidence_in(conn,row['id']) is None
                and not self.payments.evidence_in(conn,cd))

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

    def bind_in(self,conn,owner,actor,payload):
        """Register actual formation independently of any payment or work.start."""
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Contract binding requires the owned application transaction')
        if set(payload)!={'admissionId','card'} or not isinstance(payload['admissionId'],str) or not isinstance(payload['card'],dict):
            raise LabError('invalid_input','Use the original admission and effective card')
        self.identities.owned(owner,actor)
        row=self.admissions.admission(conn,payload['admissionId'])
        if row['client']!=actor:denied('Only the original client can register this effective contract')
        card=payload['card']
        try:
            if card['fulfillment'] or any(card['signatures'][str(slot)] for slot in range(4,8)):raise ValueError()
        except (KeyError,TypeError,ValueError):denied('Register the initial effective card without importing execution progress')
        if row['state']!='committed':raise LabError('closed','The original contract admission is not active',status=409)
        facts=self.card_in(conn,row,card)
        return {'admissionId':row['id'],'cardDigest':facts['digest'],'card':json.loads(facts['card'])}

    def check_in(self,conn,value):
        payload=value['body'];op,body=payload['op'],payload['body']
        row=self.admissions.admission(conn,payload['admissionId'])
        forward=(value['sender'],value['recipient'])==(row['client'],row['service'])
        reverse=(value['sender'],value['recipient'])==(row['service'],row['client'])
        refund_checkout=reverse and op=='ap2.checkout' and body.get('purpose')=='termination'
        if (not forward and not refund_checkout) or value.get('interactionId')!=row['interaction']:
            denied('Contract request changed its admitted participants or direction')
        if row['state']!='committed':raise LabError('closed','The original contract admission is not active',status=409)
        original=self.original(conn,row)
        if op in ('work.start','acceptance.notify') or op=='ap2.checkout' and body.get('purpose') in ('deposit','installment'):
            facts=self.card_in(conn,row,body.get('card'))
        else:facts=self.facts(conn,row['id'])
        current=current_card(facts);termination=current['fulfillment'].get('termination')
        if 'taskId' in payload and payload['taskId']!=facts['task']:denied('Use this contract original delivery task')
        if termination and op in ('work.start','step.authorize','delivery.fetch','acceptance.notify','termination.quote'):
            raise LabError('closed','The original contract has terminated; new fulfillment is unavailable',status=409)
        if op=='work.start':
            if facts['start_message'] and facts['start_message']!=value['messageId']:raise LabError('id_conflict','Resume the original work.start message',status=409)
            self.work_window(conn,row,facts)
        elif op=='ap2.checkout':
            purpose=body.get('purpose')
            if purpose not in ('deposit','installment','termination'):denied('Unknown established contract checkout purpose')
            if purpose=='deposit':
                if termination:raise LabError('closed','Do not pay a new deposit after termination',status=409)
                self.work_window(conn,row,facts)
            if purpose=='termination':
                if set(body)!={'purpose','cardDigest'} or body['cardDigest']!=facts['digest']:denied('Checkout names another contract')
                try:balance=self.payments.termination_balance_in(conn,original['template'],current,keys=self.keys(row))
                except (A2CError,KeyError,TypeError,ValueError):denied('Checkout requires the original agreed termination and actual deposit')
                if (refund_checkout and balance>=0) or (not refund_checkout and balance<=0):denied('Checkout direction differs from the actual settlement')
        elif op=='termination.quote':
            if set(body)!={'cardDigest'} or body['cardDigest']!=facts['digest']:denied('Quote names another contract')
        elif op=='termination.notify':self.termination_card(conn,row,body)
        elif op=='step.authorize':self.answer_in(conn,row,facts,payload)
        elif op=='delivery.fetch':
            if body.get('cardDigest')!=facts['digest'] or payload.get('taskId')!=facts['task'] or not facts['task']:denied('Fetch on the original card and delivery task')
            card=self.market.card(facts['digest'])
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
        op=value['body']['op'];aid=value['body']['admissionId']
        if op=='work.start':
            conn.execute('UPDATE delegation_contracts SET start_message=? WHERE admission=?',(value['messageId'],aid))
        elif op=='step.authorize':
            body=value['body']['body']['stepAuthorization']
            old=conn.execute('SELECT message FROM delegation_step_answers WHERE admission=? AND request=?',(aid,body['stepRequestDigest'])).fetchone()
            if old and old['message']!=value['messageId']:raise LabError('id_conflict','Resume the original answer to this step request',status=409)
            if not old:conn.execute('INSERT INTO delegation_step_answers VALUES(?,?,?,?)',(aid,body['stepRequestDigest'],value['messageId'],encode(body)))
        elif op in ('acceptance.notify','termination.notify'):
            row=self.admissions.admission(conn,aid)
            card=self.termination_card(conn,row,value['body']['body']) if op=='termination.notify' else value['body']['body']['card']
            self.advance_in(conn,row,card)

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

    def anchored_prefix(self,facts,card,*,allow_empty=False):
        original=self.market.card(facts['digest']);count=len(card['signatures']['5'])
        if allow_empty and not count and not card['signatures']['4'] and not card['fulfillment'].get('periods') and (not original or not original['signatures']['5']):return
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
        row=self.admissions.admission(conn,aid);facts=self.facts(conn,row['id']);raw=original;source=self.original(conn,row)
        client_owner=self.identities.agent(row['client'])['owner_id'];service_owner=self.identities.agent(row['service'])['owner_id']
        expected_actor=row['service'] if payment.refund else row['client']
        expected_payer=service_owner if payment.refund else client_owner
        expected_payee=client_owner if payment.refund else service_owner
        if (actor!=expected_actor or self.identities.agent(actor)['owner_id']!=owner or payment.payer!=owner
                or (payment.payer,payment.payee)!=(expected_payer,expected_payee) or payment.ref!=facts['digest']):
            denied('Payment differs from the original effective contract, payer or admission')
        old=conn.execute('SELECT * FROM admission_payments WHERE payment_id=?',(payment.payment_id,)).fetchone()
        if old:
            if (old['admission'],old['fingerprint'])!=(aid,payment.fingerprint):raise LabError('id_conflict','Original contract payment changed',status=409)
            return False
        if (row['state']!='committed' or payment.provider!=source['template']['provider'] or payment.scope_digest!=row['scope']
                or payment.currency!=source['template']['pricing']['currency'] or json.loads(payment.client).get('id')!=row['client']):
            denied('New payment differs from the original contract or scope')
        if not isinstance(raw,dict):denied('Original AP2 contract evidence is required')
        self.card_in(conn,row,raw.get('card'))
        current=current_card(facts)
        if payment.purpose=='deposit':
            if current['fulfillment'].get('termination'):raise LabError('closed','No new deposit after termination',status=409)
            self.work_window(conn,row,facts)
        if payment.purpose=='installment':self.anchored_prefix(facts,raw['card'])
        if payment.purpose=='termination':
            term=current['fulfillment'].get('termination')
            if not term or term!=raw['card']['fulfillment'].get('termination'):denied('Settlement needs the actual original termination notification')
            self.anchored_prefix(facts,raw['card'],allow_empty=True)
        if not payment.expires:raise LabError('unknown_result','Restore the original payment admission association',status=409)
        conn.execute('INSERT INTO admission_payments VALUES(?,?,?)',(payment.payment_id,payment.fingerprint,aid))
        return True

    def market_in(self,conn,owner,actor,payload):
        row=self.admissions.admission(conn,payload['admissionId'])
        if row['service']!=actor or self.identities.agent(actor)['owner_id']!=owner:denied('Only the original service can anchor or close its contract')
        facts=self.card_in(conn,row,payload['body']['card']);card=payload['body']['card'];current=current_card(facts)
        if row['state']!='committed':raise LabError('not_ready','The original contract admission is not active',status=409)
        if payload['op']=='delivery.anchor':
            if facts['permit'] is None or current['fulfillment'].get('termination'):raise LabError('closed','This contract cannot begin new delivery',status=409)
            previous=self.market.card(facts['digest']);count=len(previous['signatures']['5']) if previous else 0
            if len(card['signatures']['5'])!=count or len(card['signatures']['4'])!=count+1 or previous and any(card['signatures'][key][:count]!=previous['signatures'][key] for key in ('4','5')):
                raise LabError('id_conflict','Recover the existing original anchor instead of replacing its period',status=409)
        elif payload['op']=='card.close':
            term=current['fulfillment'].get('termination')
            if term!=card['fulfillment'].get('termination'):denied('Closing cannot omit or replace the original termination')
            if not term and facts['permit'] is None:raise LabError('not_ready','Start or legitimately terminate this contract before closing',status=409)
            self.anchored_prefix(facts,card,allow_empty=bool(term))
        return row,self.original(conn,row)['template']


    def advance_in(self,conn,row,card):
        facts=self.card_in(conn,row,card);template=self.original(conn,row)['template']
        try:
            validate_card(template,card);verify_card(template,card,self.keys(row))
            previous=current_card(facts)
            if self.market.ruling_for(facts["digest"]):
                if evidence_extends(card,previous):return facts
                raise ValueError()
            if not evidence_extends(previous,card):
                if evidence_extends(card,previous):return facts
                raise ValueError()
            self.anchored_prefix(facts,card,allow_empty=True)
        except (A2CError,KeyError,TypeError,ValueError):denied('Contract progress must extend the verified original evidence')
        conn.execute('UPDATE delegation_contracts SET progress=? WHERE admission=?',(encode(card),row['id']))
        return self.facts(conn,row['id'])


    def termination_card(self,conn,row,body):
        facts=self.facts(conn,row['id']);card=current_card(facts)
        notice=body.get('termination')
        if set(body)!={'termination'} or not isinstance(notice,dict):denied('Use the original signed termination notice')
        previous=card['fulfillment'].get('termination')
        if previous:
            if previous['termination']!=notice:denied('Recover the original signed termination without replacing it')
            return card
        record={'termination':notice}
        if 'terminationQuoteDigest' in notice:
            saved=conn.execute('SELECT quote FROM delegation_termination_quotes WHERE admission=? AND digest=?',
                (row['id'],notice['terminationQuoteDigest'])).fetchone()
            if not saved:denied('The termination quote was not received from this original service')
            record['quote']=json.loads(saved['quote'])
        card=copy.deepcopy(card);card['fulfillment']['termination']=record
        try:
            verify_card(self.original(conn,row)['template'],card,self.keys(row))
            check_fresh(header_of(notice['signature']),datetime.fromtimestamp(self.admissions.clock(),timezone.utc))
        except (A2CError,KeyError,TypeError,ValueError):denied('Termination needs the original client, current period and signed agreement')
        return card


    def quote_result_in(self,conn,row,reply):
        quote=reply['body'].get('terminationQuote')
        if quote is None:return
        try:
            header=verify_quote(self.original(conn,row)['template'],current_card(self.facts(conn,row['id'])),quote,self.keys(row))
            check_fresh(header,datetime.fromtimestamp(self.admissions.clock(),timezone.utc))
        except (A2CError,KeyError,TypeError,ValueError):denied('The quote must be from the original service and current contract period')
        conn.execute('INSERT OR IGNORE INTO delegation_termination_quotes VALUES(?,?,?)',(row['id'],objects.object_digest(quote),encode(quote)))
