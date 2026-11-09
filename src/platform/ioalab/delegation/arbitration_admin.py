"""Independent web administrator reads, review and explicit reevaluation of original cases."""
import json
import re

from a2c.codec import digest,digest_bytes,canonical
from .arbitration_evidence import EvidenceSnapshot,encoded,public_evidence,stamp
from ..errors import LabError


class CaseReview:
    """Mixed into ArbitrationCases; every read and decision rechecks the current independent administrator."""
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
