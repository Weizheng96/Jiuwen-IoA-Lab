"""One metadata boundary for current events, history and browser replay."""
from decimal import Decimal,InvalidOperation

from ..events import LABELS


OPS=set(LABELS)|{op+'.result' for op in LABELS}|{'participant.activity','delegation.intent','delegation.result','interaction.opened','user.register','wallet.adjust','transfer','identity.bound',
    'identity.connected','identity.disconnected','service.online','service.status','service.registered',
    'discovery.search','discovery.compared','message.queued','message.started','message.processing','message.completed','message.failed','message.unknown','message.audit',
    'order.proposed','order.signed','order.started','order.fetched','order.verified','order.accepted',
    'order.completed','order.client-terminated','order.arbitrated','identity.roles_changed'}
FIELDS={'source','trust','evidenceTrust','messageId','interactionId','originMessageId','kind','protocolOp',
    'assistant','provider','state','total','periods','formation','name','templates','external','username',
    'displayName','agentId','generation','targetId','interface','phase'}
OUTCOMES={'ok','error','queued','delivered','processing','completed','failed','rejected','unknown','expired',
    'pending','working','auth-required','started','signed','reserved','bound','online','offline','login',
    'external','virtual','isc','isp','deposit','installment','proposal-fee','termination','fee','grant',
    'arbitrated','client-terminated','countersign','proposal','paused','canceled'}


def event_view(event):
    op=event.get('op') if event.get('op') in OPS else 'participant.activity'
    detail=event.get('detail') or {}
    safe={key:value for key,value in detail.items() if key in FIELDS and isinstance(value,(str,int,float,bool))}
    signatures=detail.get('signatures')
    if isinstance(signatures,dict):
        safe['signatures']={key:[bool(item) for item in value[:64]] if isinstance(value,list) else bool(value)
            for key,value in signatures.items() if key in set('1234567')}
    amount=event.get('amount')
    try:
        if amount is not None:
            number=Decimal(str(amount));amount=str(number) if number.is_finite() else None
    except (InvalidOperation,ValueError):amount=None
    return {'seq':event.get('seq'),'at':event.get('at'),'run':event.get('run',event.get('run_id')),
        'kind':event.get('kind'),'op':op,'label':LABELS.get(op,op),'sender':event.get('sender',''),
        'receiver':event.get('receiver',''),'cardDigest':event.get('cardDigest',event.get('card_digest')),
        'period':event.get('period'),'amount':amount,
        'outcome':event.get('outcome') if event.get('outcome') in OUTCOMES else '', 'detail':safe}
