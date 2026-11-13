"""Verified original progress triggers receipt/export/preview, never acceptance/payment."""
from a2c.codec import digest
from ..runtime import RuntimeFault, flow_key, validate_output


class DeliveryWatch:
    def __init__(self, flows):
        self.flows, self.role = flows, flows.role
        self.cursor = 0

    def scan(self):
        journal = self.flows.delegation
        with journal.lock:
            records = [(key,flow) for key,flow in journal.store.checkpoints(journal.namespace).items()
                       if not key.startswith(('request:','delivery:')) and flow.get('context',{}).get('deliveryWatch')]
            if not records:
                return
            selected = (records + records)[self.cursor:self.cursor + min(16,len(records))]
            self.cursor = (self.cursor + 16) % len(records)
            for key, flow in selected:
                if key.startswith('request:') or key.startswith('delivery:') or not flow.get('context',{}).get('deliveryWatch'):
                    continue
                try:
                    self.advance(journal,flow)
                except RuntimeFault as exc:
                    flow['context']['deliveryWait'] = {'code':exc.code,'recovery':exc.recovery}
                    journal.save(flow)

    def advance(self, journal, flow):
        ref = flow['context']['contractRef']
        contract = self.role.contracts.record(ref)
        relation = self.role.store.interaction(interaction_id=contract['interactionId'])
        if not relation or relation['actor'] != self.role.identity.agent_id or relation['direction'] != 'outbound':
            raise RuntimeFault('forbidden','Delivery progress has no original outbound interaction')
        frames = [f['message'] for f in self.role.store.messages(self.role.identity.agent_id)
                  if f['message'].get('interactionId') == contract['interactionId']
                  and f['message'].get('sender') == contract['service']
                  and f['message'].get('kind') == 'a2c.result' and f['message'].get('payloadAvailable') is not False]
        if not frames:
            return
        progress = digest({'messages':frames})
        ctx = flow['context']
        pending = ctx.get('deliveryPending')
        if not pending and ctx.get('deliveryProgress') == progress:
            return
        # status verifies the original provider's signed card and task binding.
        facts = self.role.fulfillment.status(ref)['data']
        client, original = self.role.fulfillment.client(ref)
        self.role.cases.require_open(client,original,ref)
        if facts.get('closed') or facts['card']['fulfillment'].get('termination'):
            ctx['deliveryWatch'] = False
            ctx['deliveryWait'] = {'code':'closed','recovery':'observe_original'}
            journal.save(flow)
            return
        pending_record = journal.store.checkpoint(journal.namespace,pending) if pending else None
        if pending and pending_record is None:
            raise RuntimeFault('not_ready','Restore the original pending delivery record','restore_original')
        period = pending_record['context']['period'] if pending_record else facts['acceptedPeriods'] + 1
        if facts['availablePeriods'] < period:
            ctx['deliveryProgress'] = progress
            journal.save(flow)
            return
        deliveries = ctx.setdefault('deliveries', {})
        if str(period) in deliveries:
            ctx['deliveryProgress'] = progress
            journal.save(flow)
            return
        operation = flow_key('delivery', journal.actor, ref, period)
        record_key = 'delivery:' + operation
        record = journal.store.checkpoint(journal.namespace,record_key)
        if record is None:
            record = {'flowRef':record_key,'context':{'period':period},'action':'delivery','operation':operation,
                      'results':{},'active':True,'stage':0}
        ctx['deliveryPending'] = record_key
        journal.save(flow)
        def advance(j,r):
            fetched = self.flows.step(j,r,0,'delegation.fetch',{'contractRef':ref,'period':period})
            verified = self.flows.step(j,r,1,'delegation.verify',{'contractRef':ref,'period':period})
            r['context'].update(period=period,files=fetched['files'],report=verified['report'],
                                requiresUserAcceptance=True)
            tool = self.role.tools.get('owner.notify')
            if tool is None:
                return j.receipt(r,reason='host_unavailable',required=('owner.notify',))
            if 'notification' not in r:
                # Stable source and immutable text let the existing receipt queue deduplicate crashes.
                notice = {'title':'委托成果待验收','text': '流程 '+flow['flowRef']+'，合同 '+ref+'，第 '+str(period)+
                          ' 期已领取。文件：'+str(fetched['files'])+'；验收预览：'+str(verified['report'])+
                          '。请审阅后独立确认验收；尚未签署验收或付款。'}
                source = {'operationId':operation,'agentId':journal.actor,'messageId':operation,
                          'interactionId':contract['interactionId'],'peerAgentId':contract['service']}
                self.role.runtime.require_execution()
                result = tool.invoke(notice,source)
                validate_output(tool,result)
                r['notification'] = result
                j.save(r)
            r['active'] = False
            return j.receipt(r,'completed','user_acceptance',notification=r['notification'])
        result = journal.drive(record,advance)
        ctx['deliveryWait'] = result['data']
        if not record['active']:
            deliveries[str(period)] = result['data']
            ctx.pop('deliveryPending',None)
        journal.save(flow)
