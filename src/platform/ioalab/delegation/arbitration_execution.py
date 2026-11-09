"""After a final ruling: guards on protocol and payment, and the service's execution finish receipt."""
import json

from a2c.codec import digest
from .arbitration_evidence import encoded,reference,stamp
from ..errors import LabError


OPEN_CALLS=('processing','working','auth-required')


class CaseExecution:
    """Mixed into ArbitrationCases; release and closure only after convergence and actual frozen payables."""
    @staticmethod
    def _finish_shape(operation,payload):
        if not isinstance(operation,str) or not 1<=len(operation)<=128:
            raise LabError('invalid_input','Use the original bounded operationId')
        if not isinstance(payload,dict) or set(payload)!={'caseRef','admissionId'}:
            raise LabError('invalid_input','Use the original caseRef and admissionId')
        reference(payload['caseRef']);reference(payload['admissionId'])

    def _finish_in(self,conn,owner,actor,prepared):
        """Release and close only after the ruled service converged and frozen payables are actually paid."""
        kind='arbitration.finish';payload=json.loads(prepared.payload);self._finish_shape(prepared.operation,payload)
        if digest(payload)!=prepared.fingerprint:raise LabError('id_conflict','Original arbitration action changed',status=409)
        prior=self._operation(conn,owner,actor,kind,prepared.operation,prepared.fingerprint)
        if prior:return json.loads(prior['result'])
        row=self._case(conn,payload['caseRef']);self._owned(conn,owner,actor,row)
        if actor!=row['service']:raise LabError('forbidden','Only the original service ends its ruled execution',status=403)
        if row['admission']!=payload['admissionId']:raise LabError('id_conflict','The case belongs to another original admission',status=409)
        if row['state']!='ruled':raise LabError('not_ready','The original case has no final ruling yet',status=409,recovery='read_original')
        admission=self.admissions.admission(conn,row['admission']);released=False
        if row['execution']!='finished':
            running=conn.execute('SELECT 1 FROM delegation_protocol_calls WHERE admission=? AND state IN (?,?,?) LIMIT 1',
                (row['admission'],*OPEN_CALLS)).fetchone()
            if running:raise LabError('not_ready','Original protocol tasks have not converged; keep the admission',status=409,recovery='reconcile_original')
            decision=self.ruled_in(conn,row['admission'])
            if decision['subject']['kind']=='contract':
                self.contracts.require_payments_in(conn,admission,decision['template'],decision['card'])
            if admission['state']!='released':
                self.admissions.finish_in(conn,admission['id']);released=True
            self.close_in(conn,admission['interaction'],'ruling-finished')
            conn.execute("UPDATE arbitration_cases SET execution='finished',updated=? WHERE id=?",(self.clock(),row['id']))
            row=self._case(conn,row['id'])
        result={'caseRef':row['id'],'admissionId':row['admission'],'interactionId':admission['interaction'],
            'execution':'finished','finishedAt':stamp(row['updated']),'released':released}
        conn.execute('INSERT INTO arbitration_operations VALUES(?,?,?,?,?,?)',
            (actor,kind,prepared.operation,owner,prepared.fingerprint,encoded(result)))
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
