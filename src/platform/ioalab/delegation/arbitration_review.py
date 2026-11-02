"""Independent review policy. Persistence and actual worker ownership belong to cases."""
from decimal import Decimal,InvalidOperation
import re

from ..errors import LabError


def request(kind,payload):
    required={'operationId','expectedEvidenceDigest','reason'}
    if kind=='review':required.add('liable')
    optional={'compensation'} if kind=='review' else set()
    if not isinstance(payload,dict) or not required<=set(payload) or set(payload)-required-optional:
        raise LabError('invalid_input','Use the documented independent review fields')
    operation=payload['operationId'];reason=payload['reason'];expected=payload['expectedEvidenceDigest']
    if not isinstance(operation,str) or not 1<=len(operation)<=128:
        raise LabError('invalid_input','Use the original bounded review operation')
    if not isinstance(expected,str) or not re.fullmatch('sha256:[0-9a-f]{64}',expected):
        raise LabError('invalid_input','Use the evidence digest from the actual review page')
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=2000:
        raise LabError('invalid_input','Provide the explicit review reason')
    if kind=='review' and payload['liable'] not in ('client','provider','none'):
        raise LabError('invalid_input','Select the actual responsibility or a neutral conclusion')
    return reason.strip()


def decision(basis,route,known,payload,operator,*,deterministic=None):
    """Never manufacture or reverse a completed original program result."""
    allowed={'client','provider','none'}
    if known is not None:allowed={known['liable'],'none'}
    elif route=='recompute':allowed={'none'}
    elif route=='deterministic':allowed={'none',deterministic['liable']} if deterministic else {'none'}
    outcome=basis.get('marketOutcome')
    if outcome:
        if outcome['outcome'] not in ('completed','client-terminated'):
            raise LabError('history_gap','Recover the existing original market ruling',status=409)
        allowed={'none'}
    if payload['liable'] not in allowed:
        raise LabError('forbidden','Do not reverse or invent an original program result or market conclusion',status=403)
    result={'rulingKind':'review','liable':payload['liable'],'operator':operator,'reason':payload['reason'].strip(),
        'evidence':{'originalEvaluation':known}}
    compensation=payload.get('compensation')
    applicable=basis['ground']=='termination-compensation' and payload['liable']=='client'
    if 'compensation' in payload:
        if not applicable or not isinstance(compensation,dict) or set(compensation)!={'amount','currency'}:
            raise LabError('invalid_input','Compensation belongs only to the original unilateral termination claim')
        amount=compensation['amount']
        if not isinstance(amount,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',amount):
            raise LabError('invalid_input','Compensation must be an explicit nonnegative decimal amount')
        try:value=Decimal(amount)
        except InvalidOperation:raise LabError('invalid_input','Invalid fixed compensation amount') from None
        card=basis['card'];period=basis['period']
        if (not value.is_finite() or card is None or period is None
                or compensation['currency']!=basis['template']['pricing']['currency']
                or value>Decimal(str(card['derived']['periods'][period-1]['amount']))):
            raise LabError('invalid_input','Compensation exceeds the original period or changes its currency')
        result['compensation']=dict(compensation)
    elif applicable:raise LabError('invalid_input','State the actual bounded compensation, including zero')
    return result
