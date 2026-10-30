"""Service ingress and per-interaction original Providers on the shared runtime."""
import copy
import threading
import time
import httpx

from a2c.binding import A2ATransport
from a2c.common import Directory
from a2c.codec import parse_time
from a2c.provider import Provider
from a2c.errors import A2CError
from a2c.slots import verify_template
from ..adapters import ProtocolStore,SessionTransport
from ..adapters.endpoint import EndpointSession
from ..adapters.transport import stable
from ..runtime.protocol import RuntimeFault,instant,stamp,encode
from ..runtime.scheduling import JobHandler,JobOutcome
from .policy import HostPolicy


class ProviderExecution:
    def __init__(self,role,runtime,host):
        self.role,self.runtime,self.host=role,runtime,host
        self.configs=copy.deepcopy(runtime.config.provider.get('services',{}))
        if set(runtime.config.provider)-{'services'} or not isinstance(self.configs,dict):raise RuntimeFault('invalid_input','Configure provider.services by local service profile')
        for profile,config in self.configs.items():
            if profile not in role.identities or not isinstance(config,dict) or set(config)-{'instructions','skills','resources','max_active_delegations','accepting'} or not isinstance(config.get('instructions'),str) or not config['instructions'].strip():
                raise RuntimeFault('invalid_input','Each service needs its local profile and professional instructions')
            if type(config.get('max_active_delegations',1)) is not int or not 1<=config.get('max_active_delegations',1)<=1000 or type(config.get('accepting',True)) is not bool:
                raise RuntimeFault('invalid_input','Configure bounded active delegations and accepting flag')
            if not isinstance(config.get('skills',[]),list) or any(not isinstance(skill,str) or not skill for skill in config.get('skills',[])):
                raise RuntimeFault('invalid_input','Use installed local business skill references')
            if not isinstance(config.get('resources',{}),dict) or len(encode(config.get('resources',{})).encode())>65536:raise RuntimeFault('invalid_input','Use bounded local business resource metadata')
        self.stop=threading.Event();self.changed=threading.Event();self.thread=None
        self.active=set();self.contexts={};self.lock=threading.RLock()
        self.public=A2ATransport(http=lambda:httpx.AsyncClient(timeout=20,trust_env=False))
        self.published={};self.refresh=set(self.configs)
        self.errors={}
        from .production import Production
        self.production=Production(self)

    def bind(self):
        if not self.role.identities:return {}
        return {'jobs':{'provider.a2c':JobHandler(self.run,'provider-a2c-v1',True,self.model_available),
                        'provider.control':JobHandler(self.run,'provider-control-v1',False,control=True),
                        'provider.production':JobHandler(self.production.run,'provider-production-v1',True,self.model_available)},'profiles':list(self.configs)}

    def model_available(self):return bool(self.host and self.host.capabilities().get('available'))

    def start(self):
        self.thread=threading.Thread(target=self.listen,name='ioa-provider',daemon=True);self.thread.start()

    def wake(self):self.changed.set()

    def ready(self,profile):
        return bool(profile in self.configs and self.published.get(profile,0)>time.time() and self.host and self.host.capabilities().get('available') and self.thread and self.thread.is_alive())

    def status(self,profile):
        return {'configured':profile in self.configs,'hostAvailable':bool(self.host and self.host.capabilities().get('available')),
                'executionReady':self.ready(profile),'publicationValidUntil':self.published.get(profile),
                'errors':self.errors.get('profile:'+profile),'blockedRequests':len(self.errors)}

    def close(self):
        self.stop.set();self.wake()
        if self.host:
            for job in list(self.active):self.host.cancel(job)
        if self.thread:
            self.thread.join(15)
            if self.thread.is_alive():raise RuntimeError('Provider control thread is still active; preserve local ownership')
        self.public.close()

    def listen(self):
        while not self.stop.is_set():
            self.changed.clear()
            try:
                if self.runtime.connection.connected:
                    self.scan()
            except (RuntimeFault,httpx.HTTPError):pass
            except Exception as exc:
                self.role.error={'code':'not_ready','type':type(exc).__name__,'recovery':'inspect_local_provider_state'}
                return
            self.changed.wait(.25)

    def templates(self,identity):
        values=self.public.send(identity.protocol_id,'template.get',{}).body['templates']
        keys=Directory(fetch=self.public.jwks)
        for value in values:verify_template(value,keys)
        return values

    def configure(self,identity):
        config=self.configs[identity.profile]
        state=self.runtime.connection.call(identity.agent_id,'admission.status',{},operation_id=stable('status',identity.agent_id,time.time()))
        if state['maxActiveDelegations']==config.get('max_active_delegations',1) and state['accepting']==config.get('accepting',True):return
        self.runtime.connection.call(identity.agent_id,'admission.configure',{'expectedRevision':state['revision'],
            'maxActiveDelegations':config.get('max_active_delegations',1),'accepting':config.get('accepting',True)},
            operation_id=stable('policy',identity.agent_id,state['revision'],config.get('max_active_delegations',1),config.get('accepting',True)))

    def scan(self):
        with self.lock:
            refreshed=list(self.refresh);self.refresh.clear()
        for profile in refreshed:
            identity=self.role.identities[profile]
            try:
                templates=self.templates(identity)
                if templates:
                    self.configure(identity);self.published[profile]=max(parse_time(t['validUntil']).timestamp() for t in templates)
            except Exception as exc:
                self.published.pop(profile,None)
                self.errors['profile:'+profile]={'code':getattr(exc,'code','not_ready'),'type':type(exc).__name__}
        for profile,identity in self.role.identities.items():
            for frame in self.runtime.store.messages(identity.agent_id):
                value=frame['message'];kind=value.get('kind')
                if value.get('payloadAvailable') is False or kind not in ('delegation.intent','a2c.request'):continue
                try:
                    if kind=='delegation.intent':
                        if self.ready(profile):self.intent(identity,value)
                    else:self.schedule(identity,value)
                except RuntimeFault as exc:
                    self.errors[value['messageId']]={'code':exc.code}
                    if kind=='delegation.intent' and exc.code in ('capacity_full','expired','closed','forbidden'):
                        self.reject(identity,value,exc.code)
                except httpx.HTTPError:self.errors[value['messageId']]={'code':'platform_unavailable'}
        for endpoint in list(self.contexts.values()):
            try:self.production.advance(endpoint)
            except (RuntimeFault,A2CError,httpx.HTTPError) as exc:self.errors[endpoint.interaction]={'code':getattr(exc,'code','platform_unavailable')}

    def intent(self,identity,message):
        namespace='provider-inbox:'+identity.agent_id;mid=message['messageId']
        saved=self.runtime.store.checkpoint(namespace,mid)
        if saved:return
        if not self.configs[identity.profile].get('accepting',True):return self.reject(identity,message,'service_paused')
        if instant(message['expiresAt'])<=time.time():return self.reject(identity,message,'expired')
        self.configure(identity)
        opened=self.runtime.store.prepare_open(identity.agent_id,mid)
        self.runtime.connection.wait(identity.agent_id,'message.send',opened['operationId'],timeout=2)
        self.runtime.connection.call(identity.agent_id,'request.start',{'messageId':mid,'interactionId':opened['interactionId']},operation_id=stable('start',identity.agent_id,mid))
        receipt=self.runtime.connection.call(identity.agent_id,'admission.reserve',{'interactionId':opened['interactionId'],
            'originMessageId':mid,'bindingDigest':message['body']['bindingDigest']},operation_id=stable('reserve',identity.agent_id,mid))
        templates=self.templates(identity);template=next((value for value in templates if value['templateDigest']==message['body']['templateDigest']),None)
        if template is None:raise RuntimeFault('not_ready','The original service template is unavailable')
        self.published[identity.profile]=max(parse_time(t['validUntil']).timestamp() for t in templates)
        if template['formation']=='proposal' and not template['pricing'].get('proposalFee',0):
            self.runtime.connection.call(identity.agent_id,'admission.commit',{'admissionId':receipt['admissionId']},operation_id=stable('commit',receipt['admissionId']))
        body={'outcome':'reserved',**{key:receipt[key] for key in ('admissionId','bindingDigest','holdUntil')}}
        envelope={'messageId':stable('reserved',identity.agent_id,mid),'kind':'delegation.result','recipient':message['sender'],
                  'interactionId':opened['interactionId'],'inReplyTo':mid,'expiresAt':message['expiresAt'],'body':body}
        operation=stable('reserved-send',identity.agent_id,mid)
        # Save the envelope before transmission; a lost reply resumes this operation.
        original=self.runtime.store.checkpoint(namespace,'reply:'+mid)
        if original:envelope=original
        else:self.runtime.store.checkpoint_set(namespace,'reply:'+mid,envelope)
        self.runtime.connection.call(identity.agent_id,'message.send',envelope,operation_id=operation)
        self.runtime.store.checkpoint_set(namespace,mid,{'admission':receipt,'template':template})

    def reject(self,identity,message,code):
        namespace='provider-inbox:'+identity.agent_id;mid=message['messageId']
        if self.runtime.store.checkpoint(namespace,mid):return
        relation=self.runtime.store.interaction(origin=mid)
        envelope={'messageId':stable('intent-rejected',identity.agent_id,mid),'kind':'delegation.result','recipient':message['sender'],
                  'inReplyTo':mid,'expiresAt':stamp(time.time()+86400),'body':{'outcome':'rejected','error':{'code':code}}}
        envelope['interactionId' if relation else 'originMessageId']=relation['id'] if relation else mid
        key='reject:'+mid;original=self.runtime.store.checkpoint(namespace,key)
        if original:envelope=original
        else:self.runtime.store.checkpoint_set(namespace,key,envelope)
        self.runtime.connection.call(identity.agent_id,'message.send',envelope,operation_id=stable('reject-send',identity.agent_id,mid))
        self.runtime.store.checkpoint_set(namespace,mid,{'rejected':code})

    def schedule(self,identity,message):
        relation=self.runtime.store.interaction(interaction_id=message.get('interactionId'))
        if not relation or relation['direction']!='inbound' or relation['peer']!=message['sender']:raise RuntimeFault('forbidden','Protocol request changed its original relationship')
        handler='provider.control'
        reference=self.runtime.scheduler.reference(identity.agent_id,handler,message['messageId'])
        try:
            current=self.runtime.scheduler.get(reference)
            candidates=[current]
            if current['status']=='completed':
                try:candidates.append(self.runtime.scheduler.get(self.runtime.scheduler.reference(identity.agent_id,'provider.a2c',message['messageId'])))
                except RuntimeFault:pass
            for candidate in candidates:
                if candidate['status']=='unknown':
                    endpoint=self.context(identity,relation,candidate)
                    result=endpoint.recover(message)
                    if result:self.runtime.scheduler.resolve(candidate['reference'],JobOutcome('completed',result,relation['session']))
            return
        except RuntimeFault as exc:
            if exc.code!='not_ready':raise
        if message['body']['op'] in ('request.submit','card.countersign') and not self.model_available():return
        if instant(message['expiresAt'])<=time.time():return
        self.runtime.scheduler.submit(identity.agent_id,relation['id'],handler,{'messageId':message['messageId'],'profile':identity.profile},
            operation_id=message['messageId'],session=relation['session'],deadline=instant(message['expiresAt']),admitted=True)

    def context(self,identity,relation,job):
        with self.lock:
            endpoint=self.contexts.get(relation['id'])
            if endpoint:
                endpoint.provider.policy=HostPolicy(self,identity,relation,endpoint.provider,job,endpoint.store.get('context','config'),endpoint.provider.transport.admission)
                return endpoint
            original=self.runtime.store.checkpoint('provider-inbox:'+identity.agent_id,relation['origin'])
            if not original or 'admission' not in original:raise RuntimeFault('not_ready','Recover the original intent and admission first')
            aid=original['admission']['admissionId'];store=ProtocolStore(self.runtime.store,identity.agent_id,relation['id'])
            config=store.get('context','config')
            if config is None:
                if identity.profile not in self.configs:raise RuntimeFault('not_ready','Restore the original local service configuration')
                config={key:self.configs[identity.profile].get(key,[] if key=='skills' else {} if key=='resources' else '') for key in ('instructions','skills','resources')};store.put('context','config',config)
            transport=SessionTransport(self.runtime,identity,relation['id'],aid,relation['peer'],public=self.public)
            provider=Provider(identity.signer,market=self.runtime.account.config['market'],keys=Directory(fetch=transport.jwks),transport=transport,
                fetch=self.runtime.account.fetch_package,store=store,ap2_key=identity.payment,name=identity.name)
            provider.store.put('templates',original['template']['templateDigest'],original['template'])
            endpoint=EndpointSession(self.runtime,identity,relation['id'],provider)
            provider.policy=HostPolicy(self,identity,relation,provider,job,config,aid)
            self.contexts[relation['id']]=endpoint
            return endpoint

    def run(self,job):
        identity=self.role.identities[job['payload']['profile']]
        relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        message=next(frame['message'] for frame in self.runtime.store.messages(identity.agent_id) if frame['message']['messageId']==job['payload']['messageId'])
        endpoint=self.context(identity,relation,job)
        if message['body']['op']=='request.submit':endpoint.provider.transport.request=message['body']['body']['request']
        result=endpoint.finish(message) if job['handler']=='provider.a2c' else endpoint.prepare(message)
        if result.get('deferred'):
            self.runtime.scheduler.submit(identity.agent_id,relation['id'],'provider.a2c',job['payload'],operation_id=message['messageId'],
                session=relation['session'],deadline=instant(message['expiresAt']),admitted=True)
        current=self.runtime.store.interaction(interaction_id=job['interaction'])
        return JobOutcome('completed',result,current['session'])
