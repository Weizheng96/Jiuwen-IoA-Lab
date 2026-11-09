"""Assistant role assembly: operation dispatch, job registration and the control loop; messaging and inbound live beside it."""
import threading

from a2c.errors import A2CError
import httpx

from ..runtime import ASSISTANT,CATALOG,handled_by
from ..runtime import RuntimeFault
from ..runtime import JobHandler
from .rules import Rules
from .delegation import DelegationTasks
from .trading import Trading
from .contracts import Contracts
from .candidates import Comparisons
from ..adapters import reputation_evidence
from .fulfillment import Fulfillment
from .termination import Termination
from .flow import begin, guidance
from .inbound import Inbound
from .messaging import Messaging,stable


class AssistantRole:
    def __init__(self,host=None,tools=None):
        self.host,self.tools=host,dict(tools or {})
        self.runtime=self.identity=self.rules=None
        self.trading=None
        self.error=None
        self._stop=threading.Event();self._wake=threading.Event();self._thread=None
        self.lock=threading.RLock()

    def bind(self,runtime):
        self.runtime=runtime
        self.identity=next((p for p in runtime.identities if p.role=='assistant'),None)
        if not self.identity:return {}
        self.store=runtime.store
        self.namespace='assistant:'+self.identity.agent_id
        self.rules=Rules(runtime,self.identity,self.tools)
        self.delegation=DelegationTasks(runtime,self.identity)
        self.trading=Trading(self)
        self.comparisons=Comparisons(self.trading)
        self.contracts=Contracts(self.trading)
        self.fulfillment=Fulfillment(self)
        self.termination=Termination(self.fulfillment)
        from .arbitration import Cases
        self.cases=Cases(self)
        self.messaging=Messaging(self)
        self.inbound=Inbound(self)
        return {'operations':{name:self._operation(name.split('.',1)[1]) for name in handled_by(ASSISTANT)},
                'jobs':{'assistant.message':JobHandler(self.inbound.process,'assistant-message-v1',ready=lambda:self.ready(self.identity.profile) and bool(self.host and self.host.capabilities().get('available'))),
                        'assistant.quote':JobHandler(self.trading.run,'assistant-quote-v1',False),
                        'assistant.compare':JobHandler(self.comparisons.run,'assistant-compare-v1',False),
                        'assistant.contract':JobHandler(self.contracts.run,'assistant-contract-v1',False),
                        'assistant.fulfillment':JobHandler(self.fulfillment.run,'assistant-fulfillment-v1',False),
                        'assistant.verification':JobHandler(self.fulfillment.run,'assistant-verification-v1',True),
                        'assistant.termination':JobHandler(self.termination.run,'assistant-termination-v1',False),
                        'assistant.refund':JobHandler(self.termination.receive,'assistant-refund-v1',False,control=True),
                        'assistant.receipt':JobHandler(self.fulfillment.reconcile_receipt,'assistant-receipt-v1',False,control=True)},
                'profiles':[self.identity.profile] if self.host else []}

    @staticmethod
    def fields(args,required=(),optional=()):
        if not set(required).issubset(args) or set(args)-set(required)-set(optional):
            raise RuntimeFault('invalid_input','Use the documented fields for this operation')

    def _operation(self,name):
        def execute(runtime,operation,args):
            try:return guidance(name,operation,args,self._call(name,operation,args))
            except A2CError as exc:raise RuntimeFault('invalid_input',exc.message) from None
            except httpx.HTTPStatusError as exc:
                try:body=exc.response.json();problem=body.get('error') or body.get('detail')
                except (ValueError,AttributeError):problem=None
                if exc.response.status_code>=500:raise RuntimeFault('unknown_result','Original platform result needs recovery','resume_original',operation_id=operation) from None
                raise RuntimeFault(problem.get('code','invalid_input') if isinstance(problem,dict) else 'invalid_input',
                                   problem.get('message','Platform rejected this action') if isinstance(problem,dict) else str(problem or 'Platform rejected this action')) from None
            except httpx.TransportError:
                raise RuntimeFault('unknown_result','Original platform result needs recovery','resume_original',operation_id=operation) from None
        return execute

    def _call(self,name,operation,args):
        inputs=CATALOG['assistant.'+name].inputs
        if len(inputs)==1:self.fields(args,*inputs[0])
        if name=='delegation.begin':return begin(self,operation,args)
        if name=='delegation.dispute':return self.cases.dispute(operation,args)
        if name=='delegation.dispute_status':return self.cases.dispute_status(args)
        if name in ('delegation.termination_quote','delegation.terminate'):return self.termination.call(name.split('.')[1],operation,args)
        if name=='tools.list':
            return {'status':'completed','data':{'tools':[tool.policy() for tool in self.tools.values()]}}
        if name in ('delegation.start','delegation.answer_step','delegation.fetch','delegation.verify','delegation.accept','delegation.pay'):return self.fulfillment.call(name.split('.')[1],operation,args)
        if name=='delegation.compare':return self.comparisons.start(operation,args)
        if name=='delegation.request':return self.trading.request(operation,args)
        elif name=='delegation.confirm':return self.contracts.confirm(operation,args)
        elif name=='delegation.inspect':
            return {'status':'completed','data':{'service':args['service'],'templates':self.trading.inspect(args['service'],args.get('templateDigest')),
                'reputation':reputation_evidence(self.runtime.account,self.trading.public,self.trading.protocol_id(args['service']))}}
        elif name=='delegation.search':
            self.delegation.task(args['taskRef'])
            return {'status':'completed','data':self.runtime.account.api('POST','/search',{'query':args['query'],'top_k':args.get('topK',8)})}
        elif name=='delegation.status' and set(args)=={'quoteRef'}:return self.trading.status(args['quoteRef'])
        elif name=='delegation.status' and 'contractRef' in args:
            self.fields(args,('contractRef',),('fulfillmentOperationId',))
            target=args.get('fulfillmentOperationId')
            if 'fulfillmentOperationId' in args and (not isinstance(target,str) or not target.strip()):
                raise RuntimeFault('invalid_input','Use the saved nonempty fulfillmentOperationId')
            if self.contracts.record(args['contractRef']).get('result',{}).get('formed'):return self.fulfillment.status(args['contractRef'],target)
            return self.contracts.status(args['contractRef'])
        if name=='delegation.intent':
            return self.delegation.intent(operation,args)
        elif name=='delegation.intent_status':
            return self.delegation.intent_status(args['messageRef'])
        elif name=='delegation.prepare':
            return self.delegation.prepare(operation,args)
        elif name in ('delegation.authorize','delegation.renew'):
            return self.delegation.authorize(operation,args,renew=name.endswith('.renew'))
        elif name=='delegation.status':
            self.fields(args,('taskRef',))
            return self.delegation.status(args['taskRef'])
        elif name=='delegation.close':
            return self.delegation.close(operation,args)
        elif name=='rules.draft':
            data=self.rules.draft(operation,args['declaration'],args['instructions'])
        elif name in ('rules.validate','rules.preview'):
            data=getattr(self.rules,name.split('.')[1])(args['draftRef'])
        elif name=='rules.publish':
            return self.rules.publish(operation,args['draftRef'],args['contentDigest'],args['expectedRevision'],args.get('confirmationRef'))
        elif name=='rules.disable':
            version=self.rules.version(args['name'],args['expectedRevision'],validate_tools=False)
            draft=self.rules.draft(stable(operation,'disable'),{**version['declaration'],'enabled':False},version['instructions'])
            value=self.rules.read(draft['draftRef'])
            return self.rules.publish(operation,draft['draftRef'],value['contentDigest'],args['expectedRevision'],args.get('confirmationRef'))
        elif name=='rules.list':
            data=self.rules.list()
        elif name=='messaging.interfaces':
            data=self.messaging.interfaces(args['account'])
        elif name=='messaging.send':
            return self.messaging.send(operation,args)
        elif name=='messaging.status':
            data=self.messaging.status(args)
        else:
            return self.messaging.close(operation,args)
        return {'status':'completed','data':data}

    def start(self):
        if self.identity:
            self._thread=threading.Thread(target=self._listen,name='ioa-assistant',daemon=True)
            self._thread.start()

    def wake(self):self._wake.set()

    def ready(self,profile):
        return bool(self.identity and self.identity.profile==profile and self._thread and self._thread.is_alive() and not self.error)

    def quiesce(self):
        self._stop.set();self.wake()
        if self._thread:
            self._thread.join(15)
            if self._thread.is_alive():raise RuntimeError('Assistant control thread is still active; preserve local ownership')

    def close(self):
        self.quiesce()
        if self.trading:self.trading.close()

    def _listen(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                if self.runtime.connection.connected and self.trading:
                    self.trading.recover();self.contracts.recover();self.fulfillment.recover();self.termination.recover();self.termination.scan();self.cases.scan()
                if self.runtime.connection.connected and self.host and self.host.capabilities().get('available'):
                    self.inbound.schedule_received()
            except (RuntimeFault,httpx.HTTPError):pass
            except Exception as exc:
                self.error={'code':'not_ready','type':type(exc).__name__,'recovery':'inspect_local_role_state'}
                return
            self._wake.wait(.5)
