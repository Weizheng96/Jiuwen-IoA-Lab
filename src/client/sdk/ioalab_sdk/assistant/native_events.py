"""Role-owned projections of already validated asynchronous business facts."""
from ..runtime import NativeEventTarget,RuntimeFault,encode
import time


class NativeProjections:
    def __init__(self,role):self.role=role;self.cursors={}
    @property
    def service(self):return getattr(self.role.runtime,'native_events',None)

    def _task_target(self,task):
        actor=self.role.identity.agent_id
        return NativeEventTarget(actor,task,'delegation:'+actor,'task:'+task)

    def _page(self,namespace):
        rows=self.role.store.checkpoint_page(namespace,after=self.cursors.get(namespace,''))
        if not rows:rows=self.role.store.checkpoint_page(namespace)
        self.cursors[namespace]=rows[-1][0] if rows else ''
        return rows

    def _watch(self,key,*,done=False):
        namespace='native-projection-state:'+self.role.identity.agent_id
        value=self.role.store.checkpoint(namespace,key) or {'nextAt':0,'delay':1.}
        if done:
            self.role.store.checkpoint_set(namespace,key,{'done':True});return True
        now=time.time()
        if value.get('done'):return False
        if value['nextAt']>now+30:
            value['nextAt']=now+30;self.role.store.checkpoint_set(namespace,key,value)
        if value['nextAt']>now:return False
        delay=min(30.,value['delay'])
        self.role.store.checkpoint_set(namespace,key,{'nextAt':time.time()+delay,'delay':min(30.,delay*2)})
        return True

    def scan(self):
        if not self.service:return
        actor=self.role.identity.agent_id
        self.service.recover()
        for key,record in self._page(self.role.namespace):
            if not key.startswith('message:') or record.get('noticeOwner'):continue
            mid=record['messageId'];watch='message:'+mid
            origin=self.role.store.checkpoint('host-origins',encode([actor,mid]))
            if not origin or origin.get('source')!='native' or not self._watch(watch):continue
            state=self.role.messaging.message_status(mid)
            if not state.get('result') and state['state'] not in ('rejected','failed','expired'):continue
            target=NativeEventTarget(actor,mid,'host-origins',encode([actor,mid]))
            facts=state.get('result') or {'state':state['state'],'error':state.get('error')}
            self.service.publish(target,'message-terminal:'+mid,'message.result',facts,
                                 evidence_ref={'messageRef':mid},business_operation=record['operationRef'],interaction=state.get('interactionRef'))
            self._watch(watch,done=True)
        for key,record in self._page(self.role.trading.namespace):
            if not key.startswith('quote:') or record.get('noticeOwner') or not record.get('nativeNoticeWatch') or record.get('result') is None:continue
            watch='quote:'+record['quoteRef']
            if not self._watch(watch):continue
            task=self.role.delegation.task(record['taskRef'])
            if not task.get('nativeSource'):continue
            self.service.publish(self._task_target(record['taskRef']),'quote-terminal:'+record['quoteRef'],
                                 'business.candidate',{'status':record['status'],'result':record['result']},
                                 evidence_ref={'quoteRef':record['quoteRef']},business_operation=record['quoteRef'],interaction=record.get('interactionId'))
            self._watch(watch,done=True)
        for ref,record in self._page(self.role.contracts.namespace):
            if record.get('noticeOwner') or not record.get('nativeNoticeWatch') or record.get('result') is None:continue
            watch='contract:'+ref
            if not self._watch(watch):continue
            task=self.role.delegation.task(record['taskRef'])
            if not task.get('nativeSource'):continue
            self.service.publish(self._task_target(record['taskRef']),'contract-terminal:'+ref,
                                 'business.contract',{'status':record.get('status'),'result':record['result']},
                                 evidence_ref={'contractRef':ref},business_operation=ref,interaction=record.get('interactionId'))
            self._watch(watch,done=True)
        # Close notices come from new durable inbox frames, independent of a request's terminal watch.
        ns='native-projection-state:'+actor
        cursor=(self.role.store.checkpoint(ns,'inbox-cursor') or {}).get('seq',0)
        for frame in self.role.store.messages_after(actor,cursor):
            message=frame['message']
            if message.get('kind')=='interaction.closed' and message.get('payloadAvailable') is not False:
                relation=self.role.store.interaction(interaction_id=message.get('interactionId'))
                mid=message.get('originMessageId') or (relation['origin'] if relation else None)
                origin=self.role.store.checkpoint('host-origins',encode([actor,mid])) if mid else None
                if origin and origin.get('source')=='native':
                    target=NativeEventTarget(actor,mid,'host-origins',encode([actor,mid]))
                    self.service.publish(target,'message-closed:'+mid,'interaction.closed',{'closed':True},
                                         evidence_ref={'messageId':message['messageId']},business_operation=(self.role.store.checkpoint(self.role.namespace,'message:'+mid) or {}).get('operationRef'),interaction=relation['id'] if relation else None)
            cursor=frame['recipientSeq'];self.role.store.checkpoint_set(ns,'inbox-cursor',{'seq':cursor})
