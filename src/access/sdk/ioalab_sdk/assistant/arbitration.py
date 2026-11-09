"""Current-case gate for the personal assistant: a verified final ruling stops new protected contract actions."""
import base64

from ..adapters import DisputeFlow,case_status,read_case
from ..runtime import RuntimeFault

SYSTEM='urn:ioa:platform'
GUARDED=('start','answer_step','fetch','accept','terminate','termination_quote')


class Cases:
    def __init__(self,role):
        self.role,self.runtime,self.store,self.identity=role,role.runtime,role.store,role.identity
        self.namespace='arbitration:'+self.identity.agent_id
        self.errors={}
        self.flow=DisputeFlow(self.runtime,self.identity)

    def saved(self,admission):
        return self.store.checkpoint(self.namespace,admission)

    def read(self,admission,subject,*,keys=None):
        """Verified original case for this local admission, or None; a ruling is kept locally for restarts."""
        value=read_case(self.runtime.account,self.role.trading.public,self.identity.agent_id,admission,subject,keys=keys)
        if value is not None and value['state']=='ruled' and not self.saved(admission):
            self.store.checkpoint_set(self.namespace,admission,{'caseRef':value['caseRef'],'admissionId':admission,'subject':subject,
                'liable':value['ruling']['liable'],'issuedAt':value['ruling']['issuedAt']})
        return value

    def require_open(self,client,contract,reference):
        """Read the current case before a new protected action; an unreadable case starts nothing."""
        admission=contract['admissionId']
        if not self.saved(admission):self.read(admission,{'kind':'contract','digest':reference},keys=client.keys)
        saved=self.saved(admission)
        if saved:
            raise RuntimeFault('closed','The original case ruling ended new fulfillment for this contract (case '+saved['caseRef']+')','assistant.delegation.status')

    def scan(self):
        """A platform contract.ruling only triggers a verified case read for the same local admission."""
        for frame in self.store.messages(self.identity.agent_id):
            message=frame['message']
            if message.get('kind')!='contract.ruling' or message.get('sender')!=SYSTEM or message.get('payloadAvailable') is False:continue
            body=message.get('body') or {};admission=body.get('admissionId');subject=body.get('subject')
            if not isinstance(admission,str) or not isinstance(subject,dict) or self.saved(admission):continue
            relation=self.store.interaction(interaction_id=message.get('interactionId'))
            if not relation or relation['actor']!=self.identity.agent_id or relation['direction']!='outbound':continue
            try:
                self.read(admission,subject)
                self.errors.pop(admission,None)
            except RuntimeFault as exc:self.errors[admission]={'code':exc.code}

    def _object(self,args):
        """The local original contract or paid proposal request; the model cannot substitute a card or admission."""
        common=('ground',);optional=('period','materials','confirmationRef')
        keys=set(args)-set(common)-set(optional)
        if not set(common)<=set(args) or keys not in ({'contractRef'},{'quoteRef'}):
            raise RuntimeFault('invalid_input','Use contractRef or quoteRef, ground and the documented optional fields')
        if not isinstance(args['ground'],str) or 'period' in args and (type(args['period']) is not int or args['period']<1):
            raise RuntimeFault('invalid_input','Use a documented ground and a positive original period')
        if 'contractRef' in args:
            reference=args['contractRef'];contract=self.role.contracts.record(reference)
            if not contract.get('result',{}).get('formed'):raise RuntimeFault('not_ready','Only an original formed contract can be disputed by contractRef')
            def delivered():
                client,_=self.role.fulfillment.client(reference)
                cached=(client.store.get('cards',reference) or {}).get('deliveries',{}).get(str(args.get('period')))
                return {name:base64.b64decode(blob,validate=True) for name,blob in cached.items()} if isinstance(cached,dict) else None
            return contract['admissionId'],{'kind':'contract','digest':reference},delivered
        quote=self.store.checkpoint(self.role.trading.namespace,'quote:'+str(args['quoteRef']))
        if not quote:raise RuntimeFault('forbidden','Quote belongs to another local assistant')
        if not quote.get('admissionId'):raise RuntimeFault('not_ready','This request has no original platform admission yet')
        return quote['admissionId'],None,lambda:None

    def dispute(self,operation,args):
        admission,subject,delivered=self._object(args)
        return self.flow.call(operation,args,admission=admission,subject=subject,delivered=delivered,stage='delegation.dispute')

    def _known(self,admission,subject):
        if not isinstance(admission,str) or not isinstance(subject,dict):return False
        if subject.get('kind')=='contract':
            contract=self.store.checkpoint(self.role.contracts.namespace,subject.get('digest'))
            return bool(contract and contract.get('admissionId')==admission)
        return any(value.get('admissionId')==admission for value in self.store.checkpoints(self.role.trading.namespace).values())

    def dispute_status(self,args):
        value=case_status(self.runtime.account,self.role.trading.public,self.identity.agent_id,args['caseRef'],self._known)
        if value['state']=='ruled':self.read(value['admissionId'],value['subject'])
        return {'status':'completed','data':value}

