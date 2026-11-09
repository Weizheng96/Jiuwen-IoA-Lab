"""After a verified final ruling: stop new fulfillment, wait for original writers, then send the finish receipt."""
import time
from urllib.parse import urlencode

from a2c.card import request_digest
from ..adapters import DisputeFlow,case_status,verify_case
from ..adapters import stable
from ..runtime import RuntimeFault
from ..runtime import JobOutcome

SYSTEM='urn:ioa:platform'
WRITERS=('provider.production','provider.a2c')
HANDLER='provider.arbitration'
RETRY_SECONDS=30


class Rulings:
    def __init__(self,execution):
        self.execution,self.runtime=execution,execution.runtime
        self.checked=set()

    def ruled(self,endpoint):
        return endpoint.store.get('arbitration','case')

    def _subject_known(self,endpoint,subject):
        if subject.get('kind')=='contract':return endpoint.store.get('cards',subject.get('digest')) is not None
        if subject.get('kind')!='proposal-request':return False
        for frame in self.runtime.store.messages(endpoint.identity.agent_id):
            value=frame['message'];body=value.get('body') or {}
            if value.get('kind')=='a2c.request' and value.get('interactionId')==endpoint.interaction and body.get('op')=='request.submit':
                try:
                    if request_digest(body['body']['request'])==subject.get('digest'):return True
                except (KeyError,TypeError,ValueError):continue
        return False

    def check(self,endpoint,*,force=False):
        """Read the current case once per process or on a ruling notice; a failed read raises and blocks new rounds."""
        saved=self.ruled(endpoint)
        if saved or (endpoint.interaction in self.checked and not force):return saved
        admission=endpoint.provider.transport.admission;actor=endpoint.identity.agent_id
        value=self.runtime.account.api('GET','/arbitrations?'+urlencode({'agentId':actor,'admissionId':admission}))
        if value is not None:
            subject=value.get('subject') if isinstance(value,dict) else None
            if not isinstance(subject,dict) or not self._subject_known(endpoint,subject):
                raise RuntimeFault('invalid_evidence','The case names an object this service did not handle','restore_original')
            value=verify_case(value,self.runtime.account,self.execution.public,admission,subject)
            if value['state']=='ruled':
                saved={'caseRef':value['caseRef'],'admissionId':admission,'subject':subject,
                       'liable':value['ruling']['liable'],'issuedAt':value['ruling']['issuedAt'],'attempt':1}
                endpoint.store.put('arbitration','case',saved)
        self.checked.add(endpoint.interaction)
        return saved

    def notice(self,identity,message):
        """A platform contract.ruling only triggers a verified case read; the notice itself grants nothing."""
        if message.get('sender')!=SYSTEM:return
        relation=self.runtime.store.interaction(interaction_id=message.get('interactionId'))
        if not relation or relation['direction']!='inbound' or relation['actor']!=identity.agent_id:return
        endpoint=self.execution.context(identity,relation,None)
        if not self.ruled(endpoint):self.check(endpoint,force=True)

    def writers(self,endpoint):
        return self.runtime.scheduler.stop_pending(endpoint.identity.agent_id,endpoint.interaction,WRITERS,{'reason':'contract_ruled'})

    @staticmethod
    def converged(rows):
        return not any(row['executing'] or row['state'] in ('running','unknown') for row in rows)

    @staticmethod
    def operation(endpoint,case):
        return stable(endpoint.identity.agent_id,endpoint.interaction,'arbitration.finish',case['caseRef'],case.get('attempt',1))

    def _finished(self,endpoint,case,receipt):
        endpoint.store.put('arbitration','case',{**case,'finish':receipt})

    def _rejected(self,endpoint,case,error):
        endpoint.store.put('arbitration','case',{**case,'retryAt':time.time()+RETRY_SECONDS,'lastError':error})

    def advance(self,endpoint):
        case=self.ruled(endpoint)
        if not case or case.get('finish'):return
        if not endpoint.lock.acquire(blocking=False):return
        try:
            if not self.converged(self.writers(endpoint)):return
            actor=endpoint.identity.agent_id;operation=self.operation(endpoint,case)
            reference=self.runtime.scheduler.reference(actor,HANDLER,operation)
            try:job=self.runtime.scheduler.get(reference)
            except RuntimeFault as exc:
                if exc.code!='not_ready':raise
                job=None
            if job and job['status']=='unknown' and not self.runtime.scheduler.is_executing(reference):
                # Consume only the saved original platform response for this exact operation.
                response=self.runtime.store.response(actor,'arbitration.finish',operation)
                if response and response['ok']:
                    self._finished(endpoint,case,response['result'])
                    self.runtime.scheduler.resolve(reference,JobOutcome('completed',{'caseRef':case['caseRef'],'executionDrained':True,'finish':response['result']}))
                elif response:
                    self._rejected(endpoint,case,response['error'])
                    self.runtime.scheduler.resolve(reference,JobOutcome('rejected',{'caseRef':case['caseRef'],'error':response['error']}))
                return
            if job and job['status'] in ('rejected','failed'):
                if time.time()<case.get('retryAt',0):return
                case={**case,'attempt':case.get('attempt',1)+1};case.pop('retryAt',None)
                endpoint.store.put('arbitration','case',case);operation=self.operation(endpoint,case);job=None
            if job is None:
                relation=self.runtime.store.interaction(interaction_id=endpoint.interaction)
                self.runtime.scheduler.submit(actor,endpoint.interaction,HANDLER,
                    {'profile':endpoint.identity.profile,'caseRef':case['caseRef'],'attempt':case.get('attempt',1)},
                    operation_id=operation,session=relation['session'],admitted=True)
        finally:endpoint.lock.release()

    def run(self,job):
        identity=self.execution.role.identities[job['payload']['profile']]
        relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        endpoint=self.execution.context(identity,relation,job);case=self.ruled(endpoint)
        if not case or case['caseRef']!=job['payload']['caseRef']:raise RuntimeFault('forbidden','Only the original verified ruling can end this execution')
        with endpoint.lock:
            if not self.converged(self.writers(endpoint)):
                return JobOutcome('rejected',{'caseRef':case['caseRef'],'error':{'code':'not_ready','message':'Original execution has not converged'}},relation['session'])
            try:
                receipt=self.runtime.connection.call(identity.agent_id,'arbitration.finish',{'caseRef':case['caseRef'],'admissionId':case['admissionId']},
                    operation_id=self.operation(endpoint,case))
            except RuntimeFault as exc:
                if exc.code=='unknown_result':raise
                error={'code':exc.code,'message':str(exc),'recovery':exc.recovery}
                self._rejected(endpoint,case,error)
                return JobOutcome('rejected',{'caseRef':case['caseRef'],'error':error},relation['session'])
            self._finished(endpoint,case,receipt)
        return JobOutcome('completed',{'caseRef':case['caseRef'],'executionDrained':True,'finish':receipt},relation['session'])

    def _endpoint(self,identity,task):
        job=next(iter(self.execution.tasks.records(identity,task)))
        relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        if not relation or relation['actor']!=identity.agent_id or relation['direction']!='inbound' or relation['name']!='delegation':
            raise RuntimeFault('forbidden','Use an original task of this service')
        return self.execution.context(identity,relation,None)

    def flow(self,identity):
        return DisputeFlow(self.runtime,identity)

    def dispute(self,identity,operation,args):
        """provider.dispute: the admission comes from the original local task, never from the caller."""
        if not isinstance(args['ground'],str) or 'period' in args and (type(args['period']) is not int or args['period']<1):
            raise RuntimeFault('invalid_input','Use a documented ground and a positive original period')
        endpoint=self._endpoint(identity,args['taskRef'])
        request={key:value for key,value in args.items() if key not in ('profile','taskRef')}
        return self.flow(identity).call(operation,request,admission=endpoint.provider.transport.admission,subject=None,
            delivered=lambda:None,stage='provider.dispute',known=lambda subject:self._subject_known(endpoint,subject))

    def dispute_status(self,identity,args):
        known=lambda admission,subject:(isinstance(admission,str) and isinstance(subject,dict) and any(
            endpoint.provider.transport.admission==admission and self._subject_known(endpoint,subject)
            for endpoint in self._endpoints(identity)))
        return {'status':'completed','data':case_status(self.runtime.account,self.execution.public,identity.agent_id,args['caseRef'],known)}

    def _endpoints(self,identity):
        seen=set()
        for job in self.execution.tasks.records(identity):
            if job['interaction'] in seen:continue
            seen.add(job['interaction'])
            relation=self.runtime.store.interaction(interaction_id=job['interaction'])
            if relation and relation['actor']==identity.agent_id and relation['direction']=='inbound' and relation['name']=='delegation':
                try:yield self.execution.context(identity,relation,None)
                except RuntimeFault:continue

