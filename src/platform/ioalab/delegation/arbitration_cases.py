"""Owned arbitration cases and frozen submissions; A2C owns protocol criteria."""
from dataclasses import dataclass
import json
import re
import threading
import uuid

from a2c.codec import digest
from a2c.template import acceptance_of,deliverable_of
from .arbitration_evidence import EvidenceSnapshot,GROUNDS,NO_PERIOD,CLIENT,PROVIDER,encoded,public_evidence,reference,stamp
from .arbitration_admin import CaseReview
from .arbitration_execution import CaseExecution
from .arbitration_worker import CaseEvaluation
from ..errors import LabError


GROUPS={'deliveryFiles','fetchEvidence','inputMaterials'}


@dataclass(frozen=True)
class PreparedCase:
    operation:str
    fingerprint:str
    payload:str
    snapshot:EvidenceSnapshot|None=None
    model_binding:str|None=None
    result:str|None=None


class ArbitrationCases(CaseEvaluation,CaseReview,CaseExecution):
    def __init__(self,db,identities,auth,admissions,evidence,files,*,model_binding,clock,market,notify_in,notify,contracts,close_in):
        self.db,self.identities,self.auth=db,identities,auth
        self.admissions,self.evidence,self.files=admissions,evidence,files
        self.model_binding,self.clock=model_binding,clock
        self.market,self.notify_in,self.notify=market,notify_in,notify
        self.contracts,self.close_in=contracts,close_in
        self.processing=threading.Lock()
        self.active_case=None

    def _shape(self,operation,payload):
        if not isinstance(operation,str) or not 1<=len(operation)<=128:
            raise LabError('invalid_input','Use the original bounded operationId')
        if (not isinstance(payload,dict) or set(payload)-{'admissionId','ground','period','basisDigest','materials'}
                or not {'admissionId','ground','basisDigest'}<=set(payload)):
            raise LabError('invalid_input','Use original admission, ground, evidence digest and material references')
        reference(payload['admissionId']);ground=payload['ground']
        if (not isinstance(ground,str) or ground not in GROUNDS or ground in NO_PERIOD and 'period' in payload
                or ground not in NO_PERIOD and (type(payload.get('period')) is not int or payload['period']<1)):
            raise LabError('invalid_input','Use the documented ground and its original period')
        if not isinstance(payload['basisDigest'],str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',payload['basisDigest']):
            raise LabError('invalid_input','Use the original preview basisDigest')
        materials=payload.get('materials',{})
        if (not isinstance(materials,dict) or set(materials)-GROUPS
                or any(not isinstance(group,dict) or not group for group in materials.values())
                or sum(len(group) for group in materials.values())>32):
            raise LabError('invalid_input','Use at most32 frozen file references in documented material groups')

    def _party(self,conn,owner,actor,admission,ground=None):
        self.identities.owned(owner,actor)
        row=self.admissions.admission(conn,admission)
        if actor not in (row['client'],row['service']):
            raise LabError('forbidden','This dispute belongs to other original parties',status=403)
        if ground in CLIENT and actor!=row['client'] or ground in PROVIDER and actor!=row['service']:
            raise LabError('forbidden','This ground belongs to the other original party',status=403)
        return row

    def _operation(self,conn,owner,actor,kind,operation,fingerprint=None):
        self.identities.owned(owner,actor)
        row=conn.execute('SELECT * FROM arbitration_operations WHERE actor=? AND kind=? AND id=?',
            (actor,kind,operation)).fetchone()
        if row and (row['owner']!=owner or fingerprint is not None and row['fingerprint']!=fingerprint):
            raise LabError('id_conflict','Original arbitration operation changed',status=409)
        return row

    def operation(self,owner,actor,kind,operation):
        with self.db.lock:row=self._operation(self.db.conn,owner,actor,kind,operation)
        if not row:raise LabError('not_ready','Original arbitration result is not available',status=404,recovery='retry_original')
        return json.loads(row['result'])

    def _capture(self,owner,actor,payload):
        return self.evidence.capture(owner,{'agentId':actor,**{key:payload[key]
            for key in ('admissionId','ground','period') if key in payload}})

    def _materials(self,conn,actor,materials,snapshot):
        if set(snapshot.required)-set(materials):
            raise LabError('invalid_input','Provide the original required evidence groups before applying')
        for group in materials.values():
            for name,ref in group.items():
                row=self.files.checked_in(conn,actor,ref)
                if name!=row['name']:raise LabError('invalid_input','Preserve the original material name')
        basis=json.loads(snapshot.basis)
        if 'deliveryFiles' in materials:
            period=basis['period'];card=basis['card']
            if card is None or period is None:raise LabError('invalid_input','Delivery files require this original contract period')
            manifest=card['fulfillment']['periods'][period-1]['manifest']
            expected={item['name']:item['digest'] for item in manifest['files']}
            if {name:ref['digest'] for name,ref in materials['deliveryFiles'].items()}!=expected:
                raise LabError('invalid_input','Submitted delivery files differ from the original signed manifest')

    def _binding(self,snapshot):
        basis=json.loads(snapshot.basis);models=set()
        if snapshot.route=='recompute':
            target=deliverable_of(basis['template'],basis['period'])
            for ref in acceptance_of(basis['template'],basis['card'])[target]['verifiers'].values():
                models.update(ref.get('disputeModels',[]))
        return encoded(self.model_binding(tuple(sorted(models))))

    def prepare(self,owner,actor,kind,operation,payload):
        if kind=='arbitration.finish':
            self._finish_shape(operation,payload)
            return PreparedCase(operation,digest(payload),encoded(payload))
        if kind!='arbitration.request':raise LabError('not_ready','This arbitration operation is not yet available',status=409)
        self._shape(operation,payload);body=encoded(payload);fingerprint=digest(payload)
        with self.db.lock:
            conn=self.db.conn
            prior=self._operation(conn,owner,actor,kind,operation,fingerprint)
            if prior:return PreparedCase(operation,fingerprint,body,result=prior['result'])
            self._party(conn,owner,actor,payload['admissionId'],payload['ground'])
            existing=conn.execute('SELECT id FROM arbitration_cases WHERE admission=?',(payload['admissionId'],)).fetchone()
            if existing:return PreparedCase(operation,fingerprint,body)
        snapshot=self._capture(owner,actor,payload)
        if not snapshot.eligible:raise LabError('not_ready','Original ground is not eligible: '+snapshot.reason,status=409,recovery='read_original')
        if digest(json.loads(snapshot.basis))!=payload['basisDigest']:
            raise LabError('revision_conflict','Original evidence changed; read the current preview',status=409)
        materials=payload.get('materials',{})
        with self.db.lock:self._materials(self.db.conn,actor,materials,snapshot)
        # Read actual bytes outside the short application/connection transaction.
        for group in materials.values():
            for ref in group.values():
                actual,_=self.files.get(owner,actor,ref['fileId'])
                if actual!=ref:raise LabError('revision_conflict','Original material reference changed',status=409)
        return PreparedCase(operation,fingerprint,body,snapshot,self._binding(snapshot))

    def apply_in(self,conn,owner,actor,kind,prepared):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Arbitration admission needs its original application transaction')
        if kind=='arbitration.finish':return self._finish_in(conn,owner,actor,prepared)
        payload=json.loads(prepared.payload);self._shape(prepared.operation,payload)
        if kind!='arbitration.request' or digest(payload)!=prepared.fingerprint:
            raise LabError('id_conflict','Original arbitration action changed',status=409)
        prior=self._operation(conn,owner,actor,kind,prepared.operation,prepared.fingerprint)
        if prior:return json.loads(prior['result'])
        row=self._party(conn,owner,actor,payload['admissionId'],payload['ground'])
        case=conn.execute('SELECT * FROM arbitration_cases WHERE admission=?',(row['id'],)).fetchone()
        existing=case is not None
        if case is None:
            if prepared.snapshot is None or prepared.model_binding is None:
                raise LabError('unknown_result','Recover the original prepared evidence',status=409,recovery='retry_original')
            try:fresh=self._capture(owner,actor,payload)
            except LabError as exc:
                if exc.code=='invalid_input':raise LabError('revision_conflict','Original facts changed before case admission',status=409) from exc
                raise
            if not fresh.eligible or fresh.basis!=prepared.snapshot.basis or self._binding(fresh)!=prepared.model_binding:
                raise LabError('revision_conflict','Original evidence or model mapping changed before case admission',status=409)
            materials=payload.get('materials',{});self._materials(conn,actor,materials,fresh)
            public=fresh.public();assessment={key:public[key] for key in
                ('assessedAt','deadline','eligible','route','reason','requiredEvidence')}
            identifier=str(uuid.uuid4());at=self.clock()
            conn.execute('''INSERT INTO arbitration_cases(id,admission,owner,actor,client,service,client_owner,service_owner,
                request,basis,assessment,materials,model_binding,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identifier,row['id'],owner,actor,row['client'],row['service'],self.identities.agent(row['client'])['owner_id'],
                 self.identities.agent(row['service'])['owner_id'],prepared.payload,fresh.basis,encoded(assessment),
                 encoded(materials),prepared.model_binding,'needs_review' if fresh.route=='review' else 'queued',at,at))
            case=conn.execute('SELECT * FROM arbitration_cases WHERE id=?',(identifier,)).fetchone()
        result={'caseRef':case['id'],'admissionId':case['admission'],'subject':json.loads(case['basis'])['subject'],
            'acceptedAt':stamp(case['created']),'existing':existing}
        conn.execute('INSERT INTO arbitration_operations VALUES(?,?,?,?,?,?)',
            (actor,kind,prepared.operation,owner,prepared.fingerprint,encoded(result)))
        return result

    def _case(self,conn,case_ref):
        row=conn.execute('SELECT * FROM arbitration_cases WHERE id=?',(reference(case_ref),)).fetchone()
        if row is None:raise LabError('not_ready','Original case is not available',status=404)
        return row

    def _owned(self,conn,owner,actor,row):
        self.identities.owned(owner,actor)
        if (actor,owner) not in ((row['client'],row['client_owner']),(row['service'],row['service_owner'])):
            raise LabError('forbidden','This case belongs to other original parties',status=403)

    def public(self,conn,row):
        meta=json.loads(row['assessment']);basis=json.loads(row['basis'])
        request=json.loads(row['request']);materials=json.loads(row['materials'])
        if digest(basis)!=request['basisDigest'] or materials!=request.get('materials',{}):
            raise LabError('history_gap','Restore the original frozen case input',status=409,recovery='restore_original')
        snapshot=EvidenceSnapshot(row['basis'],meta['assessedAt'],meta['deadline'],meta['eligible'],
            meta['route'],meta['reason'],tuple(meta['requiredEvidence']))
        attempts=[{**{key:value[key] for key in ('id','sequence','state')},
            'startedAt':stamp(value['started']),'finishedAt':stamp(value['finished'])}
            for value in conn.execute('SELECT * FROM arbitration_attempts WHERE case_id=? ORDER BY sequence',(row['id'],))]
        decision=public_evidence(row['decision_basis']) if row['decision_basis'] else None
        ruling=json.loads(row['ruling']) if row['ruling'] else None
        if ruling and (decision is None or ruling.get('decisionEvidenceDigest')!=digest(decision)):
            raise LabError('history_gap','Restore the original signed decision evidence',status=409)
        return {'caseRef':row['id'],'admissionId':row['admission'],'subject':basis['subject'],
            'ground':basis['ground'],'period':basis['period'],'state':row['state'],
            'createdAt':stamp(row['created']),'updatedAt':stamp(row['updated']),'assessment':snapshot.public(),
            'materials':materials,'materialsDigest':digest(materials),
            'modelBindingDigest':digest(json.loads(row['model_binding'])),'attempts':attempts,
            'evaluation':json.loads(row['result']) if row['result'] else None,
            'ruling':ruling,'decisionEvidence':decision,'execution':row['execution']}

    def get(self,owner,actor,case_ref):
        with self.db.lock:
            row=self._case(self.db.conn,case_ref);self._owned(self.db.conn,owner,actor,row)
            return self.public(self.db.conn,row)

    def by_admission(self,owner,actor,admission):
        with self.db.lock:
            self._party(self.db.conn,owner,actor,reference(admission))
            row=self.db.conn.execute('SELECT * FROM arbitration_cases WHERE admission=?',(admission,)).fetchone()
            return self.public(self.db.conn,row) if row else None

    def material(self,case_ref,file_id,*,owner=None,actor=None,operator=None):
        with self.db.lock:
            row=self._case(self.db.conn,case_ref)
            self._admin(operator,row) if operator is not None else self._owned(self.db.conn,owner,actor,row)
            ref=next((ref for group in json.loads(row['materials']).values() for ref in group.values() if ref['fileId']==file_id),None)
            if ref is None:raise LabError('forbidden','This file was not submitted to the original case',status=404)
        actual,data=self.files.get(row['owner'],row['actor'],file_id)
        if actual!=ref:raise LabError('history_gap','Original case material metadata changed',status=409,recovery='restore_original')
        with self.db.tx() as conn:
            if operator is not None:self._admin(operator,row);self._audit(conn,operator,'material',row)
            else:self._owned(conn,owner,actor,row)
        return actual,data

    def request_outcomes(self,provider):
        """Only original request rulings, not fabricated contract ledger entries."""
        with self.db.lock:
            result=[]
            for row in self.db.conn.execute("SELECT basis,ruling FROM arbitration_cases WHERE state='ruled'"):
                basis=json.loads(row['basis'])
                if basis['subject']['kind']=='proposal-request' and basis['template']['provider']==provider:
                    result.append(json.loads(row['ruling']))
            return result
