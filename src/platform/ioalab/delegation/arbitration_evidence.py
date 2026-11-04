"""Read original dispute evidence; never adjudicate, execute or change a contract."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json

from a2c import objects
from a2c.card import card_digest, request_digest, validate_card, validate_request, verify_request
from a2c.codec import digest, fmt_time, parse_time, parse_duration
from a2c.errors import A2CError
from a2c.jws import header_of
from a2c.payment.terms import termination_balance
from a2c.slots import verify_card, verify_template
from a2c.template import acceptance_of, deliverable_of
from a2c.timing import StepWindow, start_deadline, deliver_deadline, accept_deadline

from ..errors import LabError


CLIENT={'acceptance-failed','start-overdue','deliver-overdue','proposal-overdue','fetch-failed','termination-refund'}
PROVIDER={'accept-overdue','payment-overdue','authorize-overdue','authorize-refused','termination-compensation'}
GROUNDS=CLIENT|PROVIDER|{'declaration-mismatch'}
NO_PERIOD={'start-overdue','proposal-overdue'}


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def corrupt(message):raise LabError('history_gap',message,status=409,recovery='reconcile_original')


def public_evidence(basis):
    value=json.loads(basis) if isinstance(basis,str) else json.loads(encoded(basis))
    history=value.pop('protocolHistory')
    value['protocolCalls']=[{'messageId':item['request']['messageId'],'op':item['request']['body']['op'],
        'state':item['state'],'acceptedAt':item['request']['acceptedAt'],
        'resultStates':[(r['body']['reply'].get('state') or 'completed') for r in item['results']]} for item in history]
    return value


@dataclass(frozen=True)
class EvidenceSnapshot:
    basis: str
    assessed_at: str
    deadline: str|None
    eligible: bool
    route: str
    reason: str
    required: tuple=()

    def public(self):
        value=public_evidence(self.basis)
        return {'subject':value['subject'],'ground':value['ground'],'period':value['period'],
            'assessedAt':self.assessed_at,'deadline':self.deadline,'eligible':self.eligible,'route':self.route,
            'reason':self.reason,'basisDigest':digest(json.loads(self.basis)),'evidence':value,'requiredEvidence':list(self.required)}


class ArbitrationEvidence:
    def __init__(self,db,identities,admissions,contracts,payments,relay,messages,*,clock):
        self.db,self.identities,self.admissions,self.contracts=db,identities,admissions,contracts
        self.payments,self.relay,self.messages,self.clock=payments,relay,messages,clock

    def preview(self,owner,payload):return self.capture(owner,payload).public()

    def capture(self,owner,payload):return self._read(owner,payload,assess=True)

    def current(self,owner,payload):return self._read(owner,payload,assess=False)

    def _read(self,owner,payload,*,assess):
        if (not isinstance(payload,dict) or set(payload)-{'agentId','admissionId','ground','period'}
                or not {'agentId','admissionId','ground'}<=set(payload)
                or not all(isinstance(payload[k],str) and payload[k] for k in ('agentId','admissionId','ground'))):
            raise LabError('invalid_input','Use an original actor, admission and supported dispute ground')
        ground,period=payload['ground'],payload.get('period')
        if ground not in GROUNDS or (ground in NO_PERIOD and 'period' in payload) or (ground not in NO_PERIOD and (type(period) is not int or period<1)):
            raise LabError('invalid_input','Use the documented ground and its original positive period')
        with self.db.lock:
            conn=self.db.conn;actor=payload['agentId'];self.identities.owned(owner,actor)
            source=self.admissions.evidence_in(conn,payload['admissionId']);row=source['admission']
            if actor not in (row['client'],row['service']):raise LabError('forbidden','Dispute belongs to another delegation',status=403)
            if (ground in CLIENT and actor!=row['client']) or (ground in PROVIDER and actor!=row['service']):
                raise LabError('forbidden','This dispute ground belongs to the other original party',status=403)
            try:
                basis,chain=self._collect(conn,source,ground,period)
                return self._assess(conn,source,basis,chain) if assess else basis
            except (A2CError,KeyError,IndexError,TypeError,ValueError) as exc:
                raise LabError('history_gap','Original dispute evidence failed validation; reconcile it before adjudication',
                    status=409,recovery='reconcile_original') from exc

    def _collect(self,conn,source,ground,period):
        row,template=source['admission'],source['template'];keys=self.contracts.keys(row)
        party=self.identities.service_party(row['service'])
        metadata=json.loads(self.identities.agent(row['client'])['meta'])
        if (template['templateDigest']!=row['template'] or template['provider']!=party['id']
                or header_of(template['signatures']['market'])['a2c-signer']!=self.contracts.market.id):
            corrupt('Original template differs from the bound service and market')
        verify_template(template,keys)
        history=self.relay.evidence_in(conn,row['id'],self.messages.protocol_history_in)
        chain=None
        facts=self.contracts.evidence_in(conn,row['id'])
        card=facts['card'] if facts else None
        request=source['signedRequest'];request_ts=None
        if request is not None:
            validate_request(template,request);request_ts=parse_time(verify_request(request)['a2c-ts'])
            if (request_digest(request)!=row['binding'] or request['client']['id']!=row['client']
                    or request['client']['jwk']!=metadata.get('clientJwk') or request['user']['jwk']!=metadata.get('userJwk')
                    or request['delegationScopeDigest']!=row['scope']):
                corrupt('Original signed request differs from its admitted subject')
        if ground=='proposal-overdue':
            if template['formation']!='proposal':raise LabError('invalid_input','Proposal expiry needs an original proposal request')
            subject={'kind':'proposal-request','digest':row['binding']}
        elif card is None:raise LabError('not_ready','This ground requires the original effective contract',status=409)
        if card is not None:
            if template['formation']=='proposal' and (request is None or source['offer'] is None):
                corrupt('Original formed proposal has lost its signed request or issued offer')
            validate_card(template,card,**({'request':request} if request else {}))
            if (card_digest(card)!=facts['digest'] or card['templateDigest']!=row['template'] or card['provider']!=party['id']
                    or card['client']['id']!=row['client'] or card['client']['jwk']!=metadata.get('clientJwk')
                    or card['user']['jwk']!=metadata.get('userJwk') or card['delegationScopeDigest']!=row['scope']
                    or card['paymentProvider']!=self.admissions.payment_provider
                    or template['formation']=='countersign' and card_digest(card)!=row['binding']):
                corrupt('Original card differs from its frozen admission, identities or scope')
            if template['formation']=='proposal':
                offered=source['offer']['card']
                if card_digest(offered)!=facts['digest'] or any(card['signatures'][slot]!=offered['signatures'][slot] for slot in ('2','3')):
                    corrupt('Formed proposal replaced its original issued offer')
            if any(card['signatures'][slot]!=facts['initial']['signatures'][slot] for slot in ('1','2','3')):
                corrupt('Original formation signatures changed')
            chain=verify_card(template,card,keys,request_ts=request_ts)
            if chain.effective is None:corrupt('Dispute card has no original effective formation')
            self.contracts.anchored_prefix(facts,card,allow_empty=True)
            if ground!='proposal-overdue':subject={'kind':'contract','digest':card_digest(card)}
            if period is not None and not 1<=period<=len(template['deliverables']):raise LabError('invalid_input','Dispute period does not belong to this contract')
        payments=self.payments.evidence_in(conn,subject['digest'])
        steps=[] if card is None else self._steps(template,card,history,keys)
        self._payments(row,template,card,subject,payments)
        basis={'admissionId':row['id'],'interactionId':row['interaction'],'subject':subject,'ground':ground,'period':period,
            'template':template,'card':card,'request':request,'steps':steps,'payments':payments,'protocolHistory':history,
            'marketOutcome':self.contracts.market.contract_outcome(subject['digest']) if subject['kind']=='contract' else None}
        return basis,chain

    def _assess(self,conn,source,basis,chain):
        row,template=source['admission'],basis['template'];keys=self.contracts.keys(row)
        ground,period=basis['ground'],basis['period']
        card,request=basis['card'],basis['request'];subject=basis['subject']
        steps,payments,history=basis['steps'],basis['payments'],basis['protocolHistory']
        request_ts=parse_time(header_of(request['signature'])['a2c-ts']) if request is not None else None
        now=datetime.fromtimestamp(self.clock(),timezone.utc)
        def result(eligible,reason,deadline=None,route='deterministic',required=()):
            return EvidenceSnapshot(encoded(basis),fmt_time(now),fmt_time(deadline) if deadline else None,
                eligible,route,reason,tuple(required))
        if ground=='proposal-overdue':
            if request is None:return result(False,'request_not_submitted')
            original=next((item for item in history if item['request']['messageId']==source['requestMessageId']),None)
            if not original or original['request']['body']['op']!='request.submit' or original['request']['body']['body'].get('request')!=request:
                corrupt('Original signed proposal request has no matching accepted message')
            due=request_ts+parse_duration(template['deadlines']['respondWithin'])
            fee=Decimal(str(template['pricing'].get('proposalFee',0)))
            if fee<=0 or not any(p['purpose']=='proposal-fee' for p in payments):return result(False,'proposal_fee_not_paid',due)
            answered=bool(source['offer']) or any((r['body']['reply'].get('state') or 'completed') in ('completed','failed','rejected','canceled') for r in original['results'])
            return result(now>due and not answered,'answer_exists' if answered else 'deadline_passed' if now>due else 'not_due',due)
        if self.contracts.market.ruling_for(subject['digest']):return result(False,'already_ruled')
        ledger=self.contracts.market.contract_outcome(subject['digest'])
        if ledger:return result(False,'already_closed')
        termination=card['fulfillment'].get('termination')
        if termination and ground not in ('termination-refund','termination-compensation'):return result(False,'contract_terminated')
        if ground=='start-overdue':
            due=start_deadline(template,chain)
            return result(now>due and chain.receipt is None,'receipt_exists' if chain.receipt else 'deadline_passed' if now>due else 'not_due',due)
        if ground in ('termination-refund','termination-compensation'):
            if not termination:return result(False,'contract_not_terminated',route='review')
            if period!=chain.accepted+1:raise LabError('invalid_input','Use the original termination period')
            if ground=='termination-compensation':
                return result('quote' not in termination,'unilateral_termination' if 'quote' not in termination else 'agreed_termination',route='review')
            if 'quote' not in termination:return result(False,'no_agreed_refund',route='review')
            balance=termination_balance(template,card,termination,keys,deposit_paid=any(p['purpose']=='deposit' for p in payments))
            paid=any(p['purpose']=='termination' for p in payments)
            return result(balance<0 and not paid,'refund_recorded' if paid else 'agreed_refund_unpaid' if balance<0 else 'no_refund_due',route='review')
        if ground=='payment-overdue':
            signed=chain.period_ts(7,period)
            due=signed+parse_duration(template['deadlines']['respondWithin']) if signed else None
            paid=any(p['purpose']=='installment' and p['period']==period for p in payments)
            amount=Decimal(str(card['derived']['periods'][period-1]['amount']))
            return result(bool(signed and amount>0 and not paid and now>due),
                'not_accepted' if not signed else 'zero_amount' if amount==0 else 'payment_recorded' if paid else 'deadline_passed' if now>due else 'not_due',due)
        if period!=chain.accepted+1:raise LabError('invalid_input','Use this contract first unaccepted period')
        failed=self._failed_report(template,card,period)
        windows=[StepWindow(s['request']['period'],parse_time(s['requestedAt']),parse_time(s['answeredAt']) if s['answeredAt'] else None) for s in steps]
        if ground=='deliver-overdue':
            due=deliver_deadline(template,chain,period,windows,now)
            anchored=chain.period_ts(5,period)
            return result(bool(due and (anchored>due if anchored else now>due)),
                'not_started' if due is None else 'deadline_passed' if (anchored>due if anchored else now>due) else 'delivered_in_time' if anchored else 'not_due',due)
        if ground=='accept-overdue':
            due=accept_deadline(template,card,chain,period)
            return result(bool(due and now>due and not failed),
                'failing_report_exists' if failed else 'not_anchored' if due is None else 'deadline_passed' if now>due else 'not_due',due)
        if ground=='acceptance-failed':
            return result(bool(chain.period_ts(5,period) and failed),'signed_failing_report' if failed else 'no_signed_failing_report',
                route='recompute',required=('deliveryFiles',))
        if ground in ('authorize-overdue','authorize-refused'):
            requests=[s for s in steps if s['request']['period']==period]
            limit=parse_duration(template['deadlines'].get('authorizeWithin','PT0S'))
            if ground=='authorize-refused':
                refused=any(s['answer'] and s['answer']['granted'] is False for s in requests)
                return result(refused,'original_refusal' if refused else 'no_original_refusal',route='review')
            pending=[parse_time(s['requestedAt'])+limit for s in requests if s['answer'] is None]
            due=min(pending) if pending else None
            return result(bool(due and now>due),'no_unanswered_step' if due is None else 'deadline_passed' if now>due else 'not_due',due)
        if ground=='fetch-failed':
            calls=[h for h in history if h['request']['body']['op']=='delivery.fetch' and h['request']['body']['body'].get('period')==period]
            return result(bool(calls),'original_fetch_requires_review' if calls else 'no_original_fetch',route='review',required=('fetchEvidence',))
        rejections=[r['body']['reply']['body'].get('rejection') for h in history if h['request']['body']['op']=='work.start' for r in h['results']]
        evidence=next((r for r in rejections if isinstance(r,dict) and r.get('reason')=='declaration-mismatch'),None)
        if evidence:
            party=self.identities.service_party(row['service']);objects.verify_signed(evidence,party['jwk'],signer=party['id'],expect_kid=True)
            if evidence['cardDigest']!=subject['digest']:corrupt('Rejection names another contract')
        return result(bool(evidence),'original_rejection_requires_review' if evidence else 'no_original_rejection',route='review',required=('inputMaterials',))

    def _steps(self,template,card,history,keys):
        party=card['provider'];requests={};answers={};allowed={s['name'] for s in template.get('authorizableSteps',[])}
        for item in history:
            envelope=item['request'];op=envelope['body']['op']
            if op=='step.authorize':
                answer=envelope['body']['body']['stepAuthorization']
                h=objects.verify_signed(answer,card['user']['jwk'],signer=card['user']['id'])
                if answer.get('jwk')!={k:v for k,v in card['user']['jwk'].items() if k!='kid'}:corrupt('Step authorization replaced its original user key')
                dg=answer['stepRequestDigest']
                if dg in answers and answers[dg][0]!=answer:corrupt('Step has conflicting original answers')
                answers[dg]=(answer,h['a2c-ts'])
            for response in item['results']:
                request=(((response['body']['reply'].get('task') or {}).get('status') or {}).get('stepRequest'))
                if request is None:continue
                h=objects.verify_signed(request,keys.jwk(party,header_of(request['signature']).get('kid')),
                    signer=party,expect_kid=True)
                if request['cardDigest']!=card_digest(card) or request['step'] not in allowed or type(request['period']) is not int or not 1<=request['period']<=len(template['deliverables']):
                    corrupt('Historical step request differs from its original contract')
                requests[objects.object_digest(request)]=(request,h['a2c-ts'])
        if set(answers)-set(requests):corrupt('Original step authorization has lost its signed request history')
        result=[]
        for dg,(request,requested) in sorted(requests.items()):
            answer,answered=answers.get(dg,(None,None))
            if answered and not parse_time(requested)<=parse_time(answered)<=parse_time(requested)+parse_duration(template['deadlines']['authorizeWithin']):
                corrupt('Historical step answer violates its original authorization window')
            result.append({'request':request,'requestDigest':dg,'requestedAt':requested,'answer':answer,'answeredAt':answered})
        return result

    @staticmethod
    def _failed_report(template,card,period):
        periods=card['fulfillment'].get('periods',[])
        report=periods[period-1].get('report') if len(periods)>=period else None
        if report is None:return False
        objects.verify_signed(report,card['client']['jwk'],signer=card['client']['id'])
        expected=Counter(ref['id'] for ref in acceptance_of(template,card)[deliverable_of(template,period)]['verifiers'].values())
        if (report['cardDigest']!=card_digest(card) or report.get('period')!=period
                or Counter(r['verifier'] for r in report['results'])!=expected):
            corrupt('Failure report does not cover this original period and its actual verifiers')
        return any(r['status']=='fail' for r in report['results'])

    def _payments(self,row,template,card,subject,payments):
        client=self.identities.agent(row['client'])['owner_id'];provider=self.identities.agent(row['service'])['owner_id']
        for value in payments:
            purpose=value['purpose'];amount=Decimal(value['amount']);payer,payee=client,provider
            if subject['kind']=='proposal-request':
                expected=Decimal(str(template['pricing'].get('proposalFee',0)))
                valid=purpose=='proposal-fee'
            elif purpose=='deposit':expected=Decimal(str(card['derived']['deposit']));valid=True
            elif purpose=='installment':
                i=value['period'];valid=type(i) is int and 1<=i<=len(card['derived']['periods'])
                expected=Decimal(str(card['derived']['periods'][i-1]['amount'])) if valid else Decimal(-1)
            elif purpose=='termination':
                balance=termination_balance(template,card,card['fulfillment'].get('termination'),self.contracts.keys(row),
                    deposit_paid=any(p['purpose']=='deposit' for p in payments))
                expected=abs(balance);valid=True
                if balance<0:payer,payee=provider,client
            else:valid=False;expected=Decimal(-1)
            if not valid or amount!=expected or amount<=0 or value['currency']!=template['pricing']['currency'] or (value['payer'],value['payee'])!=(payer,payee) or value['provider']!=template['provider']:
                corrupt('Actual payment evidence differs from the original obligation and parties')
