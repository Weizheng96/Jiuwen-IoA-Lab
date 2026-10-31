"""User-confirmed public service management on the shared resident runtime."""
import copy
import threading
from urllib.parse import quote, urlencode

import httpx
from a2c.codec import digest, utcnow
from a2c.jws import sign
from a2c.slots import sign_template
from a2c.template import validate,template_digest
from a2c.errors import A2CError

from ..runtime.protocol import RuntimeFault
from ..runtime.scheduling import JobHandler,JobOutcome


class ProviderRole:
    def __init__(self,host=None,tools_by_profile=None):
        self.tools_by_profile={profile:dict(tools) for profile,tools in (tools_by_profile or {}).items()}
        self.host,self.execution=host,None
        self.runtime = None
        self.identities = {}
        self.error = None
        self.lock = threading.RLock()

    def bind(self, runtime):
        self.runtime = runtime
        self.identities = {p.profile: p for p in runtime.identities if p.role == 'service'}
        if not self.identities:
            return {}
        from .execution import ProviderExecution
        self.execution=ProviderExecution(self,runtime,self.host)
        registration=self.execution.bind()
        registration['jobs']['provider.preview']=JobHandler(self.run_preview,'provider-preview-v1',False)
        return {'operations': {'provider.' + name: self._handler(name) for name in ('preview', 'publish', 'status', 'categories','verifier.publish','tools.list','template.draft')},**registration}

    def start(self):
        if self.execution:self.execution.start()
    def wake(self):
        if self.execution:self.execution.wake()
    def quiesce(self):
        if self.execution:self.execution.quiesce()
    def close(self):
        if self.execution:self.execution.close()
    def ready(self,profile):return bool(self.execution and self.execution.ready(profile))

    def _identity(self, profile):
        identity = self.identities.get(profile) if isinstance(profile, str) else None
        if identity is None:
            raise RuntimeFault('invalid_input', 'Choose a configured service profile')
        return identity

    @staticmethod
    def fields(args, required, optional=()):
        if not set(required).issubset(args) or set(args) - set(required) - set(optional):
            raise RuntimeFault('invalid_input', 'Use the documented provider operation fields','correct_input')

    def _handler(self, name):
        def execute(runtime, operation, args):
            try:
                if name=='verifier.publish':
                    self.fields(args,('profile','verifierId','source'),('reads','kind'))
                    identity=self._identity(args['profile']);namespace='provider:'+identity.agent_id;key='verifier:'+operation
                    record=runtime.store.checkpoint(namespace,key);fingerprint=digest(args)
                    if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original verifier upload changed')
                    if not record:
                        record={'fingerprint':fingerprint,'body':{k:v for k,v in args.items() if k!='profile'}}
                        runtime.store.checkpoint_set(namespace,key,record)
                    if 'result' not in record:
                        result=runtime.account.api('POST','/provider/packages',record['body'])
                        if result.get('id')!=args['verifierId'] or result.get('uri')!='ioalab://pkg/'+result.get('digest',''):raise RuntimeFault('protocol_error','Verifier upload returned a different reference')
                        runtime.account.fetch_package(result['uri'])
                        record={**record,'result':result};runtime.store.checkpoint_set(namespace,key,record)
                    return {'status':'completed','data':record['result']}
                if name == 'template.draft':
                    self.fields(args,('profile','terms'))
                    return self.prepare_template(operation,args)
                if name == 'categories':
                    self.fields(args, ())
                    return {'status': 'completed', 'data': runtime.account.api('GET', '/services/categories')}
                if name=='tools.list':
                    self.fields(args,('profile',))
                    identity=self._identity(args['profile'])
                    return {'status':'completed','data':{'tools':[tool.policy() for tool in self.execution.business_tools(identity.profile).values()]}}
                if name == 'status':
                    self.fields(args, ('profile',))
                    identity = self._identity(args['profile'])
                    return {'status': 'completed', 'data': {**self.status(identity),'localExecution':self.execution.status(identity.profile)}}
                if name == 'preview':
                    self.fields(args, ('service',))
                    return self.prepare_preview(operation,args['service'])
                self.fields(args, ('service', 'contentDigest', 'expectedRevision'), ('confirmationRef',))
                return self.publish(operation, args)
            except A2CError as exc:
                raise RuntimeFault('invalid_input',exc.message,'correct_input') from None
            except httpx.HTTPStatusError as exc:
                try:problem = exc.response.json().get('error', {})
                except (ValueError, AttributeError):problem = {}
                if exc.response.status_code >= 500:
                    raise RuntimeFault('unknown_result', 'Recover the original publication', 'resume_original') from None
                if (not isinstance(problem,dict) or not {'code','message','recovery'}<=set(problem)
                        or not all(isinstance(problem[key],str) and problem[key] for key in ('code','message','recovery'))):
                    raise RuntimeFault('protocol_error','Platform returned an invalid error envelope','inspect_platform') from None
                raise RuntimeFault(problem['code'],problem['message'],problem['recovery']) from None
            except httpx.TransportError:
                raise RuntimeFault('unknown_result', 'Recover the original publication', 'resume_original') from None
        return execute

    def configuration(self, config, *, require_category=True):
        fields={'profile','name','description','tags','templates'}
        if not isinstance(config,dict):
            raise RuntimeFault('invalid_input','Service must be an object','correct_input')
        missing,unknown=fields-set(config),set(config)-fields-{'categoryId'}
        if missing or unknown:
            raise RuntimeFault('invalid_input',f'Service fields: missing={sorted(missing)}, unknown={sorted(unknown)}; use profile, name, description, tags, templates and optional categoryId','correct_input')
        identity = self._identity(config['profile'])
        for field, maximum in [('name', 120), ('description', 8000)]:
            if not isinstance(config[field], str) or not 1 <= len(config[field].strip()) <= maximum:
                raise RuntimeFault('invalid_input', 'Configure a bounded service ' + field)
        if ('categoryId' in config or require_category) and (not isinstance(config.get('categoryId'),str) or not config['categoryId']):
            raise RuntimeFault('category_required','Use the complete resolved service returned by provider.preview')
        if not isinstance(config['tags'], list) or len(config['tags']) > 20 or any(not isinstance(t, str) or not 1 <= len(t) <= 80 for t in config['tags']):
            raise RuntimeFault('invalid_input', 'Configure bounded service tags')
        self.validate_templates(config['templates'],identity)
        return identity, copy.deepcopy(config)

    def validate_templates(self,drafts,identity):
        if not isinstance(drafts, list) or not 1 <= len(drafts) <= 16:
            raise RuntimeFault('invalid_input', 'Configure one to 16 unsigned template drafts')
        # The caller does not choose signing identities or supply signatures.
        for draft in drafts:
            if not isinstance(draft, dict) or any(k in draft for k in ('provider', 'templateDigest', 'signatures')):
                raise RuntimeFault('invalid_input', 'Use unsigned templates; the runtime supplies the bound provider identity')
            template = {**copy.deepcopy(draft), 'provider': identity.protocol_id,'signatures':{'provider':None}}
            template['templateDigest']=template_digest(template)
            validate(template, endorsed=False)
            payment=self.runtime.account.config['pay']
            if template['formation'] not in ('proposal', 'countersign') or template['pricing'].get('paymentProviders') != [{'id':payment,'jwksUri':payment+'/.well-known/jwks.json'}]:
                raise RuntimeFault('invalid_input', 'Use proposal/countersign templates and the configured platform payment provider')

    def prepare_template(self,operation,args):
        from ..templates import draft_from_terms
        identity=self._identity(args['profile'])
        namespace='provider:'+identity.agent_id;key='template:'+operation;fingerprint=digest(args)
        with self.lock:
            previous=self.runtime.store.checkpoint(namespace,key)
            if previous:
                if previous['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original template terms changed')
                return {'status':'completed','data':{'template':previous['template']}}
            payment=self.runtime.account.config['pay']
            template=draft_from_terms(args['terms'],payment_provider={'id':payment,'jwksUri':payment+'/.well-known/jwks.json'})
            self.validate_templates([template],identity)
            self.runtime.store.checkpoint_set(namespace,key,{'fingerprint':fingerprint,'template':template})
        return {'status':'completed','data':{'template':template}}

    def prepare_preview(self,operation,service):
        identity,config=self.configuration(service,require_category=False)
        namespace='provider:'+identity.agent_id;key='preview:'+operation;fingerprint=digest(config)
        with self.lock:
            old=self.runtime.store.checkpoint(namespace,key)
            if old:
                if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original preview input changed')
                if 'result' in old:return {'status':'completed','data':old['result']}
            else:self.runtime.store.checkpoint_set(namespace,key,{'fingerprint':fingerprint,'config':config})
        if 'categoryId' not in config:
            job=self.runtime.scheduler.submit(identity.agent_id,'publication-preview:'+operation,'provider.preview',{'operationId':operation},operation_id=operation)
            return {'status':job['status'] if job['status'] in ('failed','unknown') else 'accepted','data':{'job':job}}
        return {'status':'completed','data':self.compute_preview(identity,operation,config)}

    def run_preview(self,job):
        identity=next(identity for identity in self.identities.values() if identity.agent_id==job['actor'])
        operation=job['payload']['operationId']
        record=self.runtime.store.checkpoint('provider:'+identity.agent_id,'preview:'+operation)
        return JobOutcome('completed',record.get('result') or self.compute_preview(identity,operation,record['config']))

    def compute_preview(self,identity,operation,config):
        namespace='provider:'+identity.agent_id;key='preview:'+operation;fingerprint=digest(config)
        request={name:config[name] for name in ('name','description','tags','categoryId') if name in config}
        category=self.runtime.account.api('POST','/services/category-preview',request)
        if category.get('status')=='matched':
            if not isinstance(category.get('categoryId'),str) or not category.get('categoryPath') or category['categoryPath'][-1]['id']!=category['categoryId']:
                raise RuntimeFault('protocol_error','Invalid actual category preview')
            config={**config,'categoryId':category['categoryId']}
            result={**self.preview(identity,config,self.status(identity)['revision'],category),'service':config,'category':category}
        elif category.get('status')=='choose_category':result={'profile':identity.profile,'category':category,'readyToPublish':False}
        else:raise RuntimeFault('protocol_error','Invalid category matching outcome')
        with self.lock:
            old=self.runtime.store.checkpoint(namespace,key)
            if old and old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Concurrent preview input changed')
            if old and 'result' in old:result=old['result']
            else:self.runtime.store.checkpoint_set(namespace,key,{**(old or {}),'fingerprint':fingerprint,'result':result})
        return result

    def status(self, identity):
        return self.runtime.account.api('GET', '/services/' + quote(identity.agent_id, safe='') + '/status')

    @staticmethod
    def preview(identity, config, revision, category=None):
        return {'profile': identity.profile, 'contentDigest': digest(config), 'expectedRevision': revision,
                'preview': {'name': config['name'], 'description': config['description'], 'tags': config['tags'],
                            'categoryId': config['categoryId'], **({'categoryPath':category['categoryPath']} if category else {}), 'templateCount': len(config['templates']),
                            'templates': config['templates'], 'protocolId': identity.protocol_id,
                            'notice': '发布仅公示资料；连接、容量和执行就绪分别检查'}}

    def publish(self, operation, args):
        config = args['service']
        identity = self._identity(config.get('profile') if isinstance(config, dict) else None)
        namespace = 'provider:' + identity.agent_id
        fingerprint = digest({k: v for k, v in args.items() if k != 'confirmationRef'})
        key = 'publish:' + operation
        with self.lock:
            record = self.runtime.store.checkpoint(namespace, key)
        if record:
            if record['fingerprint'] != fingerprint:
                raise RuntimeFault('id_conflict', 'Original publication parameters changed')
            if record.get('result'):
                return self.finish(identity, record['result'])
            try:
                result = self.runtime.account.api('GET', '/operations/' + quote(operation, safe='') + '?' +
                                                 urlencode({'agentId': identity.agent_id, 'operation': 'service.publish'}))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:raise
            else:
                self.save_result(namespace, key, record, result)
                return self.finish(identity, result)
        else:
            identity, config = self.configuration(config)
            revision = args['expectedRevision']
            if type(revision) is not int or revision < 0 or args['contentDigest'] != digest(config):
                raise RuntimeFault('id_conflict', 'Use the current preview digest and expected revision')
            category=self.runtime.account.api('POST','/services/category-preview',{name:config[name] for name in ('name','description','tags','categoryId')})
            if category.get('status')!='matched' or category.get('categoryId')!=config['categoryId']:
                raise RuntimeFault('category_required','The previewed category is no longer available')
            preview = self.preview(identity, config, revision, category)['preview']
            binding = {'actor': identity.agent_id, 'operationId': operation,
                       'subject': {'contentDigest': args['contentDigest'], 'expectedRevision': revision},
                       'stage': 'provider.publish', 'action': 'service.publish', 'preview': preview}
            decision = self.runtime.confirmations.ensure(binding)
            if decision['status'] != 'approved':
                return {'status': 'waiting' if decision['status'] == 'pending' else 'rejected',
                        'data': {'confirmationRef': decision['reference'], 'confirmationStatus': decision['status'], 'preview': preview}}
            if args.get('confirmationRef') not in (None, decision['reference']):
                raise RuntimeFault('id_conflict', 'Use the original publication confirmation')
            self.runtime.confirmations.claim(decision['reference'], binding)
            templates = [sign_template({**draft, 'provider': identity.protocol_id}, identity.signer, 'provider', utcnow()) for draft in config['templates']]
            declaration = {'schema': 'ioalab.service/1', 'agentId': identity.agent_id, 'protocolId': identity.protocol_id,
                           'revision': revision + 1, **{name: config[name] for name in ('name', 'description', 'tags', 'categoryId')},
                           'templateDigests': [t['templateDigest'] for t in templates]}
            body = {'operationId': operation, 'declaration': declaration, 'proof': sign(digest(declaration), identity.signer, 'ioa-service', utcnow()), 'templates': templates}
            record = {'fingerprint': fingerprint, 'body': body}
            # Save exactly one signed request before any network effect.
            with self.lock:
                original = self.runtime.store.checkpoint(namespace, key)
                if original and original['fingerprint'] != fingerprint:
                    raise RuntimeFault('id_conflict', 'Concurrent publication parameters changed')
                if original:record = original
                else:self.runtime.store.checkpoint_set(namespace, key, record)
        result = self.runtime.account.api('POST', '/services/' + identity.agent_id + '/publications', record['body'])
        self.save_result(namespace, key, record, result)
        return self.finish(identity, result)

    def save_result(self, namespace, key, record, result):
        expected = record['body']['declaration']
        if result.get('agentId') != expected['agentId'] or result.get('declarationDigest') != digest(expected) or result.get('acceptedRevision') != expected['revision']:
            raise RuntimeFault('protocol_error', 'Platform returned a different publication receipt')
        self.runtime.store.checkpoint_set(namespace, key, {**record, 'result': result})

    def finish(self, identity, result):
        if self.execution:
            with self.execution.lock:self.execution.refresh.add(identity.profile)
            self.execution.wake()
        return {'status': 'completed', 'data': {'publicationReceipt': result, 'currentPublication': self.status(identity)}}
