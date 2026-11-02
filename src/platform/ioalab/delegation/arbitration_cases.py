"""Owned arbitration cases and frozen submissions; A2C owns protocol criteria."""
from dataclasses import dataclass
from datetime import datetime,timezone
import json
import re
import threading
import uuid

from a2c.codec import digest,digest_bytes,canonical,fmt_time
from a2c.template import acceptance_of,deliverable_of
from .arbitration_evidence import EvidenceSnapshot,GROUNDS,NO_PERIOD,CLIENT,PROVIDER,encoded,public_evidence
from ..errors import LabError


SCHEMA='''
CREATE TABLE IF NOT EXISTS arbitration_cases(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,admission TEXT UNIQUE NOT NULL,
 owner TEXT NOT NULL,actor TEXT NOT NULL,client TEXT NOT NULL,service TEXT NOT NULL,
 client_owner TEXT NOT NULL,service_owner TEXT NOT NULL,request TEXT NOT NULL,basis TEXT NOT NULL,
 assessment TEXT NOT NULL,materials TEXT NOT NULL,model_binding TEXT NOT NULL,state TEXT NOT NULL,
 created REAL NOT NULL,updated REAL NOT NULL,result TEXT,ruling TEXT,decision_basis TEXT,execution TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS arbitration_operations(
 actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,owner TEXT NOT NULL,fingerprint TEXT NOT NULL,
 result TEXT NOT NULL,PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS arbitration_attempts(
 id TEXT PRIMARY KEY,case_id TEXT NOT NULL,sequence INTEGER NOT NULL,state TEXT NOT NULL,
 started REAL NOT NULL,finished REAL,result TEXT,error TEXT,UNIQUE(case_id,sequence));
CREATE TABLE IF NOT EXISTS arbitration_admin_operations(
 operator TEXT NOT NULL,id TEXT NOT NULL,case_id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(operator,id));
CREATE TABLE IF NOT EXISTS arbitration_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT,operator TEXT NOT NULL,action TEXT NOT NULL,at REAL NOT NULL);
'''
GROUPS={'deliveryFiles','fetchEvidence','inputMaterials'}


def stamp(value):return fmt_time(datetime.fromtimestamp(value,timezone.utc)) if value is not None else None


@dataclass(frozen=True)
class PreparedCase:
    operation:str
    fingerprint:str
    payload:str
    snapshot:EvidenceSnapshot|None=None
    model_binding:str|None=None
    result:str|None=None


def reference(value):
    if not isinstance(value,str):raise LabError('invalid_input','Use the original UUID reference')
    try:parsed=str(uuid.UUID(value))
    except ValueError:raise LabError('invalid_input','Use the original UUID reference') from None
    if parsed!=value:raise LabError('invalid_input','Use the canonical original UUID reference')
    return value


class ArbitrationCases:
    def __init__(self,db,identities,auth,admissions,evidence,files,*,model_binding,clock,market,notify_in,notify):
        self.db,self.identities,self.auth=db,identities,auth
        self.admissions,self.evidence,self.files=admissions,evidence,files
        self.model_binding,self.clock=model_binding,clock
        self.market,self.notify_in,self.notify=market,notify_in,notify
        self.processing=threading.Lock()
        self.active_case=None
        with db.lock:
            db.conn.executescript(SCHEMA)
            if 'decision_basis' not in {row['name'] for row in db.conn.execute('PRAGMA table_info(arbitration_cases)')}:
                db.conn.execute('ALTER TABLE arbitration_cases ADD COLUMN decision_basis TEXT')

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

    def _admin(self,operator,row=None):
        if not self.auth.is_admin(operator):raise LabError('forbidden','Independent web administrator permission is required',status=403)
        if row and operator in (row['client_owner'],row['service_owner']):
            raise LabError('forbidden','A party cannot independently review its own case',status=403)

    def _audit(self,conn,operator,action,row=None):
        conn.execute('INSERT INTO arbitration_audit(case_id,operator,action,at) VALUES(?,?,?,?)',
            (row['id'] if row else None,operator,action,self.clock()))

    def admin_get(self,operator,case_ref):
        with self.db.tx() as conn:
            row=self._case(conn,case_ref);self._admin(operator,row);self._audit(conn,operator,'read',row)
            result=self.public(conn,row)
            result['review']=self._review(conn,row)[0]
            return result

    def admin_list(self,operator,*,state=None,limit=50,cursor=None):
        if type(limit) is not int or not 1<=limit<=100 or state not in (None,'queued','evaluating','needs_review','unknown','ruled'):
            raise LabError('invalid_input','Use a documented case state and bounded page size')
        if cursor is not None and (not isinstance(cursor,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',cursor) or int(cursor)>=2**63):
            raise LabError('invalid_input','Use the original case page cursor')
        with self.db.tx() as conn:
            self._admin(operator)
            rows=conn.execute('''SELECT * FROM arbitration_cases WHERE client_owner!=? AND service_owner!=?
                AND (? IS NULL OR state=?) AND seq<? ORDER BY seq DESC LIMIT ?''',
                (operator,operator,state,state,int(cursor) if cursor else 2**63-1,limit+1)).fetchall()
            self._audit(conn,operator,'list')
            return {'cases':[{'caseRef':row['id'],'admissionId':row['admission'],'subject':json.loads(row['basis'])['subject'],
                'ground':json.loads(row['basis'])['ground'],'state':row['state'],'createdAt':stamp(row['created']),
                'updatedAt':stamp(row['updated'])} for row in rows[:limit]],
                'nextCursor':str(rows[limit-1]['seq']) if len(rows)>limit else None}

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

    def recover(self):
        """Call once after acquiring Core ownership, before dispatching any work."""
        with self.db.tx() as conn:
            at=self.clock()
            conn.execute("UPDATE arbitration_attempts SET state='unknown',finished=?,error='core_interrupted' WHERE state='evaluating' AND case_id IN (SELECT id FROM arbitration_cases WHERE state='evaluating' AND result IS NULL)",(at,))
            conn.execute("UPDATE arbitration_cases SET state='unknown',updated=? WHERE state='evaluating' AND result IS NULL",(at,))

    def _fresh(self,row):
        payload=json.loads(row['request'])
        fresh=self._capture(row['owner'],row['actor'],payload)
        if not fresh.eligible or fresh.basis!=row['basis']:
            raise LabError('revision_conflict','Original case facts changed; independent review is required',status=409)
        if self._binding(fresh)!=row['model_binding']:
            raise LabError('revision_conflict','Restore the original dispute model mapping',status=409)
        return fresh

    def _claim(self):
        with self.db.tx() as conn:
            row=conn.execute("SELECT * FROM arbitration_cases WHERE state='queued' OR state='evaluating' AND result IS NOT NULL ORDER BY seq LIMIT 1").fetchone()
            if row is None:return None
            self.public(conn,row)  # Verify frozen input before claiming anything.
            if row['result'] is not None:return dict(row),None
            at=self.clock();attempt=str(uuid.uuid4())
            sequence=conn.execute('SELECT COUNT(*) FROM arbitration_attempts WHERE case_id=?',(row['id'],)).fetchone()[0]+1
            conn.execute('INSERT INTO arbitration_attempts(id,case_id,sequence,state,started) VALUES(?,?,?,?,?)',
                (attempt,row['id'],sequence,'evaluating',at))
            conn.execute("UPDATE arbitration_cases SET state='evaluating',updated=? WHERE id=?",(at,row['id']))
            return dict(self._case(conn,row['id'])),attempt

    def _interrupted(self,case_ref,attempt,error):
        with self.db.tx() as conn:
            row=self._case(conn,case_ref)
            if row['state']!='evaluating' or row['result'] is not None:return
            state='needs_review' if isinstance(error,LabError) and error.code in ('revision_conflict','history_gap') else 'unknown'
            at=self.clock()
            # Exception text may contain model endpoints or credentials.
            code=error.code if isinstance(error,LabError) else type(error).__name__
            conn.execute('UPDATE arbitration_cases SET state=?,updated=? WHERE id=?',(state,at,case_ref))
            conn.execute("UPDATE arbitration_attempts SET state=?,finished=?,error=? WHERE id=? AND state='evaluating'",
                (state,at,code,attempt))

    def _save_result(self,row,attempt,result):
        result={**result,'basisDigest':digest(json.loads(row['basis'])),
            'materialsDigest':digest(json.loads(row['materials'])),
            'modelBindingDigest':digest(json.loads(row['model_binding'])),'evaluatedAt':stamp(self.clock())}
        with self.db.tx() as conn:
            current=self._case(conn,row['id'])
            original=conn.execute('SELECT * FROM arbitration_attempts WHERE id=?',(attempt,)).fetchone()
            if current['state']!='evaluating' or current['result'] is not None or not original or original['state']!='evaluating':
                raise LabError('revision_conflict','Recover the already recorded evaluation',status=409)
            body=encoded(result);at=self.clock()
            conn.execute('UPDATE arbitration_cases SET result=?,updated=? WHERE id=?',(body,at,row['id']))
            conn.execute("UPDATE arbitration_attempts SET state='evaluated',finished=?,result=? WHERE id=?",(at,body,attempt))

    def _finalize(self,case_ref):
        with self.db.tx() as conn:
            row=self._case(conn,case_ref)
            if row['state']=='ruled':return json.loads(row['ruling'])
            if row['state']!='evaluating' or row['result'] is None:
                raise LabError('not_ready','No completed original evaluation is available',status=409)
            result=json.loads(row['result']);basis=json.loads(row['basis'])
            expected={'basisDigest':digest(basis),'materialsDigest':digest(json.loads(row['materials'])),
                'modelBindingDigest':digest(json.loads(row['model_binding']))}
            if any(result.get(key)!=value for key,value in expected.items()):
                conn.execute("UPDATE arbitration_cases SET state='needs_review',updated=? WHERE id=?",(self.clock(),case_ref))
                return None
            try:self._fresh(row)
            except LabError:
                conn.execute("UPDATE arbitration_cases SET state='needs_review',updated=? WHERE id=?",(self.clock(),case_ref))
                return None
            return self._publish_in(conn,row,basis,result)

    def _publish_in(self,conn,row,decision,result):
        original=json.loads(row['basis'])
        ruling=self.market.prepare_ruling(row['id'],row['admission'],original,result,json.loads(row['materials']),public_evidence(decision))
        self.market.commit_ruling_in(conn,decision,ruling)
        self.notify_in(conn,row['admission'],ruling)
        outcome=decision.get('marketOutcome')
        execution='finished' if outcome and outcome['outcome'] in ('completed','client-terminated') else 'pending'
        conn.execute("UPDATE arbitration_cases SET state='ruled',ruling=?,decision_basis=?,execution=?,updated=? WHERE id=?",
            (encoded(ruling),encoded(decision),execution,self.clock(),row['id']))
        return ruling

    def process_one(self):
        """One owned worker; completed output commits separately from evaluation."""
        if not self.processing.acquire(blocking=False):return False
        try:
            claimed=self._claim()
            if claimed is None:return False
            row,attempt=claimed
            with self.db.lock:self.active_case=row['id']
            if attempt is not None:
                try:
                    snapshot=self._fresh(row);materials={}
                    for group,refs in json.loads(row['materials']).items():
                        materials[group]={}
                        for name,ref in refs.items():
                            actual,data=self.files.get(row['owner'],row['actor'],ref['fileId'])
                            if actual!=ref:raise LabError('history_gap','Restore original submitted bytes',status=409)
                            materials[group][name]=data
                    result=self.market.evaluate_case(json.loads(row['basis']),snapshot.route,materials)
                    self._save_result(row,attempt,result)
                except Exception as exc:
                    self._interrupted(row['id'],attempt,exc)
                    return True
            # A commit failure retains the known result; never repeat a model.
            self._finalize(row['id'])
            self.notify()
            return True
        finally:
            with self.db.lock:self.active_case=None
            self.processing.release()

    def request_outcomes(self,provider):
        """Only original request rulings, not fabricated contract ledger entries."""
        with self.db.lock:
            result=[]
            for row in self.db.conn.execute("SELECT basis,ruling FROM arbitration_cases WHERE state='ruled'"):
                basis=json.loads(row['basis'])
                if basis['subject']['kind']=='proposal-request' and basis['template']['provider']==provider:
                    result.append(json.loads(row['ruling']))
            return result

    def ruled_in(self,conn,admission):
        row=conn.execute("SELECT * FROM arbitration_cases WHERE admission=? AND state='ruled'",(admission,)).fetchone()
        if row and not row['decision_basis']:raise LabError('history_gap','Restore the original decision evidence',status=409)
        return json.loads(row['decision_basis']) if row else None

    def guard_in(self,conn,admission,op,body):
        """A ruling leaves original payment/control recovery, never new fulfillment."""
        basis=self.ruled_in(conn,admission)
        if basis is None:return
        if basis['subject']['kind']=='contract':
            if op=='payment.notify':return
            if op=='ap2.checkout' and body.get('purpose') in ('installment','termination'):
                if body['purpose']=='termination' and basis['card']['fulfillment'].get('termination',{}).get('quote'):return
                period=body.get('period')
                if body['purpose']=='installment' and type(period) is int and 1<=period<=len(basis['card']['signatures']['7']):return
        raise LabError('closed','The original case ruling ended new fulfillment; recover the original case',status=409)

    def payment_in(self,conn,admission,payment,original):
        basis=self.ruled_in(conn,admission)
        if basis is None:return
        from .contracts import evidence_extends
        card=original.get('card') if isinstance(original,dict) else None
        frozen=basis['card']
        if basis['subject']['kind']!='contract' or not isinstance(card,dict) or not evidence_extends(card,frozen):
            raise LabError('closed','A ruling cannot authorize new payment evidence',status=409)
        if payment.purpose=='installment' and type(payment.period) is int and 1<=payment.period<=len(frozen['signatures']['7']):return
        if payment.purpose=='termination' and frozen['fulfillment'].get('termination',{}).get('quote') and card['fulfillment'].get('termination')==frozen['fulfillment']['termination']:return
        raise LabError('closed','Only previously accepted prices or agreed termination balances remain payable',status=409)

    def _current(self,row):
        payload=json.loads(row['request'])
        return self.evidence.current(row['owner'],{'agentId':row['actor'],**{key:payload[key]
            for key in ('admissionId','ground','period') if key in payload}})

    def _review(self,conn,row):
        basis=json.loads(row['decision_basis']) if row['state']=='ruled' and row['decision_basis'] else self._current(row)
        evidence=public_evidence(basis);meta=json.loads(row['assessment'])
        snapshot=EvidenceSnapshot(encoded(basis),meta['assessedAt'],meta['deadline'],True,meta['route'],meta['reason'],tuple(meta['requiredEvidence']))
        current_binding=self._binding(snapshot)
        value={'basisDigest':digest(json.loads(row['basis'])),'decisionEvidenceDigest':digest(evidence),
            'materialsDigest':digest(json.loads(row['materials'])),
            'evaluationDigest':digest_bytes(canonical(json.loads(row['result']) if row['result'] else None)),
            'modelBindingDigest':digest(json.loads(row['model_binding'])),
            'currentModelBindingDigest':digest(json.loads(current_binding)),'state':row['state']}
        actions=[]
        if self.active_case!=row['id']:
            if row['state'] in ('unknown','needs_review'):actions.append('review')
            if (row['state']=='unknown' and row['result'] is None and encoded(basis)==row['basis']
                    and current_binding==row['model_binding']):actions.append('reevaluate')
        outcome=basis.get('marketOutcome')
        if outcome and outcome['outcome']=='arbitrated' and row['state']!='ruled':actions=[]
        return {'evidence':evidence,'evidenceDigest':digest(evidence),
            'expectedEvidenceDigest':digest(value),'availableActions':actions},basis

    def _admin_operation(self,conn,operator,kind,case_ref,payload):
        from .arbitration_review import request
        request(kind,payload)
        row=self._case(conn,case_ref);self._admin(operator,row)
        fingerprint=digest({'kind':kind,'caseRef':case_ref,'payload':payload})
        old=conn.execute('SELECT * FROM arbitration_admin_operations WHERE operator=? AND id=?',
            (operator,payload['operationId'])).fetchone()
        if old and old['fingerprint']!=fingerprint:
            raise LabError('id_conflict','The original independent review operation changed',status=409)
        return row,fingerprint,json.loads(old['result']) if old else None

    def _save_admin(self,conn,operator,kind,row,payload,fingerprint,result):
        conn.execute('INSERT INTO arbitration_admin_operations(operator,id,case_id,fingerprint,result) VALUES(?,?,?,?,?)',
            (operator,payload['operationId'],row['id'],fingerprint,encoded(result)))
        self._audit(conn,operator,kind,row)
        return result

    def _review_inputs(self,operator,kind,case_ref,payload):
        with self.db.lock:
            row,_,old=self._admin_operation(self.db.conn,operator,kind,case_ref,payload)
            if old is not None:return old
            materials=json.loads(row['materials'])
        # Verify actual immutable bytes outside the application transaction.
        for refs in materials.values():
            for ref in refs.values():
                actual,_=self.files.get(row['owner'],row['actor'],ref['fileId'])
                if actual!=ref:raise LabError('history_gap','Restore the original submitted evidence',status=409)
        return None

    def admin_review(self,operator,case_ref,payload):
        from .arbitration_review import decision
        old=self._review_inputs(operator,'review',case_ref,payload)
        if old is not None:return old
        with self.db.tx() as conn:
            row,fingerprint,old=self._admin_operation(conn,operator,'review',case_ref,payload)
            if old is not None:return old
            view,basis=self._review(conn,row)
            if payload['expectedEvidenceDigest']!=view['expectedEvidenceDigest']:
                raise LabError('revision_conflict','Refresh the actual original case evidence before deciding',status=409)
            if 'review' not in view['availableActions']:
                raise LabError('not_ready','This original case cannot be independently reviewed now',status=409)
            known=json.loads(row['result']) if row['result'] else None
            route=json.loads(row['assessment'])['route'];deterministic=None
            if route=='deterministic' and known is None:
                try:current=self._capture(row['owner'],row['actor'],json.loads(row['request']))
                except LabError as exc:
                    if exc.code!='invalid_input':raise
                else:
                    if current.eligible:deterministic=self.market.evaluate_case(basis,'deterministic',{})
            result=decision(basis,route,known,payload,operator,deterministic=deterministic)
            ruling=self._publish_in(conn,row,basis,result)
            result=self._save_admin(conn,operator,'review',row,payload,fingerprint,
                {'caseRef':case_ref,'state':'ruled','ruling':ruling})
        self.notify()
        return result

    def admin_reevaluate(self,operator,case_ref,payload):
        old=self._review_inputs(operator,'reevaluate',case_ref,payload)
        if old is not None:return old
        with self.db.tx() as conn:
            row,fingerprint,old=self._admin_operation(conn,operator,'reevaluate',case_ref,payload)
            if old is not None:return old
            view,_=self._review(conn,row)
            if payload['expectedEvidenceDigest']!=view['expectedEvidenceDigest']:
                raise LabError('revision_conflict','Refresh the actual original case evidence before reevaluation',status=409)
            if 'reevaluate' not in view['availableActions']:
                raise LabError('not_ready','Only the same unknown input with no result or active writer can be reevaluated',status=409)
            self._fresh(row)
            conn.execute("UPDATE arbitration_cases SET state='queued',updated=? WHERE id=?",(self.clock(),case_ref))
            result=self._save_admin(conn,operator,'reevaluate',row,payload,fingerprint,
                {'caseRef':case_ref,'state':'queued'})
        return result
