"""Business compositions over the existing assistant operations, without model routing."""
from ..runtime import FlowJournal, RuntimeFault


class AssistantFlows:
    DELEGATION = ('begin', 'search', 'inspect', 'request', 'compare', 'select', 'answer_step',
                  'accept', 'pay', 'renew', 'close', 'termination_quote', 'terminate', 'dispute', 'dispute_status')
    MESSAGING = ('interfaces', 'send', 'close', 'configure_inbox', 'disable_inbox', 'inbox', 'tools')

    def __init__(self, role):
        self.role = role
        self.delegation = FlowJournal(role.runtime, role.identity.agent_id, 'delegation', self.advance_delegation)
        self.messaging = FlowJournal(role.runtime, role.identity.agent_id, 'messaging', self.advance_messaging)
        from .delivery_watch import DeliveryWatch
        self.deliveries = DeliveryWatch(self)

    def invoke(self, name, operation, args):
        return self.role._operation(name.split('.', 1)[1])(self.role.runtime, operation, args)

    def step(self, journal, record, index, name, args):
        return journal.step(record, index, 'assistant.' + name, args, self.invoke)

    def finish(self, journal, record, data, *, reason='business_input', required=()):
        record['active'] = False
        return journal.receipt(record, 'completed', reason, required=required, result=data)

    @staticmethod
    def bound(record, field):
        if field not in record['context']:
            raise RuntimeFault('not_ready', 'This flow has no original ' + field)
        return record['context'][field]

    def advance_delegation(self, journal, r):
        if not r['active']:
            return r.get('receipt') or journal.receipt(r)
        action, args, ctx = r['action'], r['input'], r['context']
        if action == 'begin':
            if ctx.get('taskRef') and not r['results']:
                raise RuntimeFault('invalid_input','Begin a new flow for a new task; renew the original scope explicitly')
            query = args.get('query')
            scope = {k:v for k,v in args.items() if k != 'query'}
            result = self.step(journal, r, 0, 'delegation.begin', scope)
            ctx['taskRef'] = result['taskRef']
            if query:
                result = self.step(journal, r, 1, 'delegation.search', {'taskRef':ctx['taskRef'], 'query':query})
            return self.finish(journal, r, result, required=('query or service selection',))
        if action == 'select':
            if set(args) != {'quoteRef'} or args['quoteRef'] not in ctx.get('quoteRefs', []):
                raise RuntimeFault('forbidden', 'Select a candidate requested by this flow')
            selected = self.step(journal, r, 0, 'delegation.confirm', args)
            ctx['contractRef'] = selected['contractRef']
            started = self.step(journal, r, 1, 'delegation.start', {'contractRef':ctx['contractRef']})
            ctx['deliveryWatch'] = True
            return self.finish(journal, r, started, reason='delivery_push')
        if action == 'request':
            self.role.fields(args,('service','spec'),('materials','templateIndex'))
            if not isinstance(args['service'],str) or not args['service']:
                raise RuntimeFault('invalid_input','Choose the original inspected service identity')
            templates = ctx.get('inspections',{}).get(args['service'],[])
            if not templates:
                r['active'] = False
                return journal.receipt(r,reason='business_input',required=('inspect the selected service',))
            index = args.get('templateIndex',1 if len(templates)==1 else None)
            if index is None:
                r['active'] = False
                return journal.receipt(r,reason='business_choice',required=('templateIndex (1-based)',),templateCount=len(templates))
            if type(index) is not int or not 1 <= index <= len(templates):
                raise RuntimeFault('invalid_input','Choose an original inspected templateIndex (1-based)')
            request = {k:v for k,v in args.items() if k != 'templateIndex'}
            request.update(taskRef=self.bound(r,'taskRef'),templateDigest=templates[index-1])
            result = self.step(journal, r, 0, 'delegation.request', request)
            refs = ctx.setdefault('quoteRefs', [])
            if result['quoteRef'] not in refs:
                refs.append(result['quoteRef'])
            compared = self.step(journal, r, 1, 'delegation.compare', {'taskRef':ctx['taskRef'],'quoteRefs':refs})
            return self.finish(journal, r, compared, required=('quoteRef',))
        if action == 'inspect':
            self.role.fields(args,('service',))
            result = self.step(journal,r,0,'delegation.inspect',args)
            ctx.setdefault('inspections',{})[args['service']] = [t['templateDigest'] for t in result['templates']]
            return self.finish(journal,r,result,required=('spec','materials if required','templateIndex if multiple templates'))
        if action in ('search', 'renew', 'close'):
            if 'taskRef' in args:
                raise RuntimeFault('invalid_input', 'The flow supplies its original task')
            args = {'taskRef':self.bound(r,'taskRef'), **args}
        elif action == 'compare':
            if args:
                raise RuntimeFault('invalid_input', 'Comparison uses this flow candidates')
            args = {'taskRef':self.bound(r,'taskRef'), 'quoteRefs':ctx.get('quoteRefs', [])}
        elif action == 'dispute_status':
            if args:
                raise RuntimeFault('invalid_input','Read the original case from this flow')
            args = {'caseRef':self.bound(r,'caseRef')}
        elif action not in ('inspect',):
            if 'contractRef' in args:
                raise RuntimeFault('invalid_input', 'The flow supplies its original contract')
            args = {'contractRef':self.bound(r,'contractRef'), **args}
            if action == 'answer_step':
                if args.keys() != {'contractRef'}:
                    raise RuntimeFault('invalid_input', 'The flow reads the current step from its original contract')
                original = r['results'].get('0:assistant.delegation.answer_step')
                if original:
                    args = original['args']
                else:
                    facts = self.role.fulfillment.status(args['contractRef'])['data']
                    if not facts.get('stepRequestDigest'):
                        return self.finish(journal,r,facts,reason='external_processing')
                    args['stepRequestDigest'] = facts['stepRequestDigest']
        result = self.step(journal, r, 0, 'delegation.' + action, args)
        if action == 'dispute' and result.get('caseRef'):
            ctx['caseRef'] = result['caseRef']
        return self.finish(journal, r, result)

    def advance_messaging(self, journal, r):
        if not r['active']:
            return r.get('receipt') or journal.receipt(r)
        action, args, ctx = r['action'], r['input'], r['context']
        if action == 'configure_inbox':
            draft = self.step(journal,r,0,'rules.draft',args)
            self.step(journal,r,1,'rules.validate',{'draftRef':draft['draftRef']})
            preview = self.step(journal,r,2,'rules.preview',{'draftRef':draft['draftRef']})
            result = self.step(journal,r,3,'rules.publish',{
                'draftRef':draft['draftRef'],'contentDigest':preview['contentDigest'],'expectedRevision':preview['preview']['previousRevision']})
        elif action == 'disable_inbox':
            result = self.step(journal,r,0,'rules.disable',args)
        elif action == 'tools':
            result = self.step(journal,r,0,'tools.list',args)
        elif action == 'inbox':
            result = self.step(journal,r,0,'rules.list',args)
        elif action == 'interfaces':
            result = self.step(journal,r,0,'messaging.interfaces',args)
            ctx['interfaces'] = result
        elif action == 'close':
            if args:
                raise RuntimeFault('invalid_input','Close uses the original flow interaction')
            result = self.step(journal,r,0,'messaging.close',{'messageRef':self.bound(r,'messageRef')})
        else:
            if 'account' in args:
                interfaces = self.step(journal,r,0,'messaging.interfaces',{'account':args['account']})
                ctx['interfaces'] = interfaces
                # Interface choice is semantic; a name is required even for a single hit.
                selected = [i for i in interfaces if i['name'] == args.get('interface')]
                if len(selected) != 1:
                    return self.finish(journal,r,interfaces,reason='business_choice',required=('interface','input'))
                send = {k:v for k,v in args.items() if k not in ('account','interface')}
                send['interfaceRef'] = selected[0]['interfaceRef']
            else:
                send = {**args, 'interactionRef':self.bound(r,'interactionRef')}
            result = self.step(journal,r,1,'messaging.send',send)
            ctx['messageRef'] = result['messageRef']
            status = self.role.messaging.status({'messageRef':ctx['messageRef']})
            if status['state'] == 'unknown':
                return journal.receipt(r,'unknown','unknown_result',evidence=status,retryAfterSeconds=5)
            if (not status.get('result') and not status.get('closed') and not status.get('error')
                    and status['state'] not in ('expired','failed','rejected','closed')):
                return journal.receipt(r, reason='external_processing', evidence=status, retryAfterSeconds=5)
            ctx['interactionRef'] = status['interactionRef']
            result = status
        return self.finish(journal,r,result)

    def scan(self):
        self.delegation.scan()
        self.messaging.scan()
        self.deliveries.scan()
