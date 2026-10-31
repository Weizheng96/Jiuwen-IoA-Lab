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


class ProviderRole:
    def __init__(self,host=None):
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
        return {'operations': {'provider.' + name: self._handler(name) for name in ('preview', 'publish', 'status', 'categories','verifier.publish')},**self.execution.bind()}

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
            raise RuntimeFault('invalid_input', 'Use the documented provider operation fields')

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
                if name == 'categories':
                    self.fields(args, ())
                    return {'status': 'completed', 'data': runtime.account.api('GET', '/services/categories')}
                if name == 'status':
                    self.fields(args, ('profile',))
                    identity = self._identity(args['profile'])
                    return {'status': 'completed', 'data': {**self.status(identity),'localExecution':self.execution.status(identity.profile)}}
                if name == 'preview':
                    self.fields(args, ('service',))
                    identity, config = self.configuration(args['service'])
                    current = self.status(identity)
                    return {'status': 'completed', 'data': self.preview(identity, config, current['revision'])}
                self.fields(args, ('service', 'contentDigest', 'expectedRevision'), ('confirmationRef',))
                return self.publish(operation, args)
            except A2CError as exc:
                raise RuntimeFault('invalid_input',exc.message) from None
            except httpx.HTTPStatusError as exc:
                try:problem = exc.response.json().get('error', {})
                except (ValueError, AttributeError):problem = {}
                if exc.response.status_code >= 500:
                    raise RuntimeFault('unknown_result', 'Recover the original publication', 'resume_original') from None
                raise RuntimeFault(problem.get('code', 'invalid_input'), problem.get('message', 'Platform rejected publication')) from None
            except httpx.TransportError:
                raise RuntimeFault('unknown_result', 'Recover the original publication', 'resume_original') from None
        return execute

    def configuration(self, config):
        if not isinstance(config, dict) or set(config) != {'profile', 'name', 'description', 'tags', 'categoryId', 'templates'}:
            raise RuntimeFault('invalid_input', 'Service configuration requires profile, name, description, tags, categoryId and template drafts')
        identity = self._identity(config['profile'])
        for field, maximum in [('name', 120), ('description', 8000)]:
            if not isinstance(config[field], str) or not 1 <= len(config[field].strip()) <= maximum:
                raise RuntimeFault('invalid_input', 'Configure a bounded service ' + field)
        if not isinstance(config['categoryId'], str) or not config['categoryId']:
            raise RuntimeFault('category_required', 'Choose a category from provider.categories')
        if not isinstance(config['tags'], list) or len(config['tags']) > 20 or any(not isinstance(t, str) or not 1 <= len(t) <= 80 for t in config['tags']):
            raise RuntimeFault('invalid_input', 'Configure bounded service tags')
        drafts = config['templates']
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
        return identity, copy.deepcopy(config)

    def status(self, identity):
        return self.runtime.account.api('GET', '/services/' + quote(identity.agent_id, safe='') + '/status')

    @staticmethod
    def preview(identity, config, revision):
        return {'profile': identity.profile, 'contentDigest': digest(config), 'expectedRevision': revision,
                'preview': {'name': config['name'], 'description': config['description'], 'tags': config['tags'],
                            'categoryId': config['categoryId'], 'templateCount': len(config['templates']),
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
            preview = self.preview(identity, config, revision)['preview']
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
