"""Shared original-case reading and historical ruling verification for both roles."""
from urllib.parse import urlencode
import uuid

from a2c.card import card_digest,request_digest,verify_request,validate_card,validate_request
from a2c.codec import digest,parse_time
from a2c.common import Directory
from a2c.errors import A2CError
from a2c.jws import header_of,verify
from a2c.objects import object_digest
from a2c.slots import verify_card,verify_template

from ..runtime.protocol import RuntimeFault


def verify_case(value,account,public,admission,subject,*,keys=None):
    """A valid market signature binds the actual public snapshot, never just a label."""
    try:
        if (not isinstance(value,dict) or value['admissionId']!=admission or value['subject']!=subject
                or str(uuid.UUID(value['caseRef']))!=value['caseRef']
                or value['state'] not in ('queued','evaluating','unknown','needs_review','ruled')):
            raise ValueError()
        document=value['ruling']
        if value['state']!='ruled':
            if document is not None or value['decisionEvidence'] is not None:raise ValueError()
            return value
        snapshot=value['decisionEvidence']
        if not isinstance(document,dict) or not isinstance(snapshot,dict):raise ValueError()
        for key in ('caseRef','admissionId','subject','ground'):
            if document[key]!=value[key]:raise ValueError()
        if (document['kind']!='ruling' or document.get('period')!=value.get('period')
                or document['liable'] not in ('client','provider','none')
                or document['rulingKind'] not in ('deadline','recompute','review')
                or document['basisDigest']!=value['assessment']['basisDigest']
                or document['materialsDigest']!=digest(value['materials'])
                or document['materialsDigest']!=value['materialsDigest']
                or document['decisionEvidenceDigest']!=digest(snapshot)
                or snapshot['subject']!=subject or snapshot['admissionId']!=admission
                or snapshot['ground']!=value['ground'] or snapshot['period']!=value.get('period')
                or 'protocolHistory' in snapshot):raise ValueError()
        if document['rulingKind']=='review':
            if not isinstance(document.get('operator'),str) or not isinstance(document.get('reason'),str) or not document['reason'].strip():raise ValueError()
        elif document['liable']=='none':raise ValueError()
        market=account.config['market'];directory=keys or Directory(fetch=public.jwks)
        header=header_of(document['signature'])
        verify(document['signature'],object_digest(document),directory.jwk(market,header.get('kid')),
            slot='market',signer=market,expect_kid=True)
        if parse_time(header['a2c-ts'])!=parse_time(document['issuedAt']):raise ValueError()
        template=snapshot['template'];card=snapshot['card'];request=snapshot['request']
        verify_template(template,directory)
        if request is not None:validate_request(template,request)
        request_ts=parse_time(verify_request(request)['a2c-ts']) if request is not None else None
        if subject['kind']=='contract':
            if card is None or card_digest(card)!=subject['digest'] or document.get('cardDigest')!=subject['digest']:raise ValueError()
            if card['templateDigest']!=template['templateDigest']:raise ValueError()
            validate_card(template,card,**({'request':request} if request is not None else {}))
            verify_card(template,card,directory,request_ts=request_ts)
        elif subject['kind']=='proposal-request':
            if card is not None or request is None or request_digest(request)!=subject['digest'] or 'cardDigest' in document:raise ValueError()
        else:raise ValueError()
        return value
    except (A2CError,ValueError,TypeError,KeyError,AttributeError,IndexError):
        raise RuntimeFault('invalid_evidence','Restore and verify the original case and its signed decision evidence','restore_original') from None


def read_case(account,public,actor,admission,subject,*,keys=None):
    value=account.api('GET','/arbitrations?'+urlencode({'agentId':actor,'admissionId':admission}))
    return None if value is None else verify_case(value,account,public,admission,subject,keys=keys)


def require_open(account,public,actor,admission,subject,*,keys=None):
    value=read_case(account,public,actor,admission,subject,keys=keys)
    if value is not None and value['state']=='ruled':
        raise RuntimeFault('closed','The original case ruling ended new fulfillment; recover its original control results','read_case')
    return value
