"""The single case evaluation worker: claim, evaluate outside a transaction, then commit the final ruling."""
import json
import uuid

from a2c.codec import digest
from .arbitration_evidence import encoded,public_evidence,stamp
from ..errors import LabError


class CaseEvaluation:
    """Mixed into ArbitrationCases; the known evaluation commits separately from the final ruling."""
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
