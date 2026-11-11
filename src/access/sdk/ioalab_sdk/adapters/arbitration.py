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

from ..runtime.files_api import PrivateFiles
from ..runtime.protocol import RuntimeFault
from .transport import stable


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


def case_status(account,public,actor,case_ref,known):
    """Read one original case of this agent; `known(admission,subject)` must recognize the local object."""
    try:
        if not isinstance(case_ref,str) or str(uuid.UUID(case_ref))!=case_ref:raise ValueError()
    except ValueError:raise RuntimeFault('invalid_input','Use the original caseRef') from None
    value=account.api('GET','/arbitrations/'+case_ref+'?'+urlencode({'agentId':actor}))
    if not isinstance(value,dict) or not known(value.get('admissionId'),value.get('subject')):
        raise RuntimeFault('forbidden','The case belongs to another local object')
    value=verify_case(value,account,public,value['admissionId'],value['subject'])
    return {'caseRef':value['caseRef'],'admissionId':value['admissionId'],'subject':value['subject'],'state':value['state'],
            'ground':value['ground'],'period':value.get('period'),'ruling':value['ruling'],'execution':value['execution'],
            'payments':(value['decisionEvidence'] or {}).get('payments'),'case':value}


class DisputeFlow:
    """Shared by both roles: original preview, frozen material references, independent user approval, then arbitration.request."""

    def __init__(self,runtime,identity):
        self.runtime,self.identity=runtime,identity
        self.namespace='dispute:'+identity.agent_id

    def record(self,operation):
        return self.runtime.store.checkpoint(self.namespace,operation)

    def _save(self,operation,record,**updates):
        record={**record,**updates};self.runtime.store.checkpoint_set(self.namespace,operation,record);return record

    def _materials(self,operation,required,given,delivered):
        materials={}
        if given is not None and not isinstance(given,dict):raise RuntimeFault('invalid_input','Use material groups of original FileRef objects')
        for group,refs in (given or {}).items():
            if group not in ('fetchEvidence','inputMaterials') or not isinstance(refs,dict) or not refs:
                raise RuntimeFault('invalid_input','Only fetchEvidence and inputMaterials accept own file references; delivery files come from the original saved bytes')
            for name,ref in refs.items():
                if not isinstance(ref,dict) or set(ref)!={'fileId','name','digest','size'} or ref['name']!=name:
                    raise RuntimeFault('invalid_input','Use complete original FileRef objects named by their protocol file names')
            materials[group]=refs
        if 'deliveryFiles' in required:
            files=delivered()
            if not files:raise RuntimeFault('not_ready','The original delivered bytes of this period are not saved locally')
            uploads=PrivateFiles(self.runtime.account,self.identity.agent_id)
            materials['deliveryFiles']={name:uploads.put(name,data,stable(operation,'dispute-material',name)) for name,data in files.items()}
        missing=[group for group in required if group not in materials]
        if missing:raise RuntimeFault('invalid_input','Provide the required evidence groups: '+', '.join(missing))
        if sum(len(group) for group in materials.values())>32:raise RuntimeFault('invalid_input','A case accepts at most 32 material files')
        return materials

    def call(self,operation,args,*,admission,subject,delivered,stage,known=None):
        """args: ground, optional period, materials and confirmationRef; `subject` is the exact local object, or None with `known(subject)`."""
        request={key:args[key] for key in ('ground','period','materials') if key in args}
        fingerprint=digest({'admissionId':admission,'subject':subject,**request})
        record=self.record(operation)
        if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original dispute input changed')
        if record and record.get('result') is not None:return {'status':record['status'],'data':record['result']}
        if not record:
            query={'agentId':self.identity.agent_id,'admissionId':admission,'ground':args['ground'],**({'period':args['period']} if 'period' in args else {})}
            preview=self.runtime.account.api('POST','/arbitrations/preview',query)
            if (preview.get('subject')!=subject) if subject is not None else (known is not None and not known(preview.get('subject'))):
                raise RuntimeFault('forbidden','The platform evidence names another object')
            if not preview.get('eligible'):
                data={'operationId':operation,'eligible':False,'reason':preview.get('reason'),'deadline':preview.get('deadline'),'ground':args['ground']}
                self._save(operation,{'fingerprint':fingerprint,'phase':'finished','status':'rejected','result':data})
                return {'status':'rejected','data':data}
            materials=self._materials(operation,preview.get('requiredEvidence',[]),args.get('materials'),delivered)
            payload={'admissionId':admission,'ground':args['ground'],'basisDigest':preview['basisDigest'],
                     **({'period':args['period']} if 'period' in args else {}),**({'materials':materials} if materials else {})}
            shown={'title':'确认申请仲裁','ground':args['ground'],'period':args.get('period'),'subject':preview['subject'],
                   'route':preview.get('route'),'deadline':preview.get('deadline'),'basisDigest':preview['basisDigest'],
                   'materials':{group:{name:{'digest':ref['digest'],'size':ref['size']} for name,ref in refs.items()} for group,refs in materials.items()},
                   'notice':'申请、受理、评估和裁定分别返回；裁定不自动付款'}
            record=self._save(operation,{'fingerprint':fingerprint,'phase':'prepared','payload':payload,'preview':shown})
        binding={'actor':self.identity.agent_id,'operationId':operation,'subject':record['payload'],'stage':stage,
                 'action':'arbitration.request','preview':record['preview']}
        if record['phase']=='prepared':
            decision=self.runtime.confirmations.ensure(binding,kind='approval')
            if args.get('confirmationRef',decision['reference'])!=decision['reference']:raise RuntimeFault('id_conflict','Use this exact dispute confirmation')
            data={'operationId':operation,'confirmationRef':decision['reference']}
            if decision['status']!='approved':
                if decision['status']!='pending':self._save(operation,record,phase='finished',status='rejected',result=data)
                return {'status':'waiting' if decision['status']=='pending' else 'rejected','data':data}
            self.runtime.confirmations.claim(decision['reference'],binding)
            record=self._save(operation,record,phase='submitted',approval=decision['reference'])
        try:
            receipt=self.runtime.connection.call(self.identity.agent_id,'arbitration.request',record['payload'],
                operation_id=stable(self.identity.agent_id,'arbitration.request',operation))
        except RuntimeFault as exc:
            if exc.code=='unknown_result':
                return {'status':'unknown','data':{'operationId':operation,'error':{'code':exc.code,'message':str(exc),'recovery':'resume_original'}}}
            data={'operationId':operation,'error':{'code':exc.code,'message':str(exc),'recovery':exc.recovery}}
            self._save(operation,record,phase='finished',status='rejected',result=data)
            return {'status':'rejected','data':data}
        data={'operationId':operation,**receipt}
        self._save(operation,record,phase='finished',status='completed',result=data)
        return {'status':'completed','data':data}

    def cases(self):
        """caseRef to (admission, subject) for this agent's own accepted applications."""
        return {value['result']['caseRef']:(value['payload']['admissionId'],value['result']['subject'])
                for value in self.runtime.store.checkpoints(self.namespace).values()
                if value.get('status')=='completed' and value.get('result',{}).get('caseRef')}
