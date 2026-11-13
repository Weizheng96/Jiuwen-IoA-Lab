"""Wait for original writers, then settle and close through original A2C/AP2."""
from ..adapters import stable
from ..runtime import RuntimeFault
from ..runtime import JobOutcome


class Termination:
    def __init__(self,execution):self.execution,self.runtime=execution,execution.runtime

    def writers(self,endpoint):
        return self.runtime.scheduler.stop_pending(endpoint.identity.agent_id,endpoint.interaction,
            ('provider.production','provider.a2c'),{'reason':'contract_terminated'})

    def advance(self,endpoint):
        # A scan must never wait for another contract's model or protocol call.
        if not endpoint.lock.acquire(blocking=False):return
        try:
            for ref,record in endpoint.store.all('cards').items():
                if record.get('state') not in ('terminated','closed'):continue
                operation=stable('termination-settlement',ref)
                reference=self.runtime.scheduler.reference(endpoint.identity.agent_id,'provider.settlement',operation)
                try:job=self.runtime.scheduler.get(reference)
                except RuntimeFault as exc:
                    if exc.code!='not_ready':raise
                    job=None
                if job and job['status']=='unknown' and not self.runtime.scheduler.is_executing(reference):
                    with endpoint.provider.transport.operation(operation):receipt=endpoint.provider.transport.control_receipt('a2c.market')
                    if receipt and receipt['payload'].get('op')=='card.close' and receipt['payload']['body']['card']==record['card']:
                        # Consume the actual original committed reply. This is
                        # not a new close or an inference from a cancellation.
                        with endpoint.provider.transport.operation(operation):endpoint.provider.close(ref)
                        self.runtime.scheduler.resolve(reference,JobOutcome('completed',{'contractRef':ref,'executionDrained':True,'closed':True}))
                if record.get('state')=='closed':continue
                writers=self.writers(endpoint)
                if any(row['executing'] or row['state'] in ('running','unknown') for row in writers):continue
                term=record['card']['fulfillment']['termination']
                balance=endpoint.provider.balance(record) if term.get('quote') else None
                if balance is not None and balance>0 and not any(value.get('purpose')=='termination' for value in record.get('vouchers',[])):continue
                self.runtime.scheduler.submit(endpoint.identity.agent_id,endpoint.interaction,'provider.settlement',
                    {'profile':endpoint.identity.profile,'cardDigest':ref},operation_id=operation,admitted=True)

        finally:endpoint.lock.release()

    def run(self,job):
        identity=self.execution.role.identities[job['payload']['profile']]
        relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        endpoint=self.execution.context(identity,relation,job);ref=job['payload']['cardDigest']
        with endpoint.lock,endpoint.provider.transport.operation(stable('termination-settlement',ref)):
            if any(row['executing'] or row['state'] in ('running','unknown') for row in self.writers(endpoint)):
                raise RuntimeFault('unknown_result','Original execution has not converged; preserve its admission')
            record=endpoint.store.get('cards',ref)
            if record.get('state') not in ('terminated','closed'):raise RuntimeFault('forbidden','Only the original terminated contract may settle')
            if record['state']!='closed':
                if record['card']['fulfillment']['termination'].get('quote') and endpoint.provider.balance(record)<0:
                    endpoint.provider.refund_termination(ref)
                endpoint.provider.close(ref)
                self.execution.production.sync(endpoint,ref)
        return JobOutcome('completed',{'contractRef':ref,'executionDrained':True,'closed':True},relation['session'])
