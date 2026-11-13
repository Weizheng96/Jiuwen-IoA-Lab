"""Owner-controlled continuation of an original, reconciled production round."""
import json
import hashlib
import uuid

from a2c.codec import digest
from a2c.slots import verify_card

from ..runtime import validate_output
from ..runtime import RuntimeFault
from .policy import definition_digest


class ProductionRecovery:
    def __init__(self, execution):
        self.execution, self.runtime = execution, execution.runtime

    @staticmethod
    def namespace(identity):
        return 'provider-production-recovery:' + identity.agent_id

    def begin(self, identity, job, relation, definition):
        namespace = self.namespace(identity)
        self.runtime.store.checkpoint_delete(namespace, 'proof:' + job['id'])
        record = {'roundId': str(uuid.uuid4()), 'taskRef': job['id'], 'actor': identity.agent_id,
                  'interactionId': relation['id'], 'sessionId': relation['session'],
                  'definitionDigest': definition, 'contractRef': job['payload']['cardDigest'],
                  'period': job['payload']['period'], 'state': 'started'}
        self.runtime.store.checkpoint_set(namespace, 'round:' + job['id'], record)
        return record

    def finish(self, identity, job, record, result):
        namespace = self.namespace(identity)
        current = self.runtime.store.checkpoint(namespace, 'round:' + job['id'])
        if current != record:
            raise RuntimeFault('identity_conflict', 'Original production round changed')
        relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        endpoint=self.execution.context(identity,relation,job)
        calls={key:value for key,value in endpoint.store.all('production_calls').items()
               if value.get('cardDigest')==record['contractRef'] and value.get('context',{}).get('period')==record['period']}
        ended = {**record, 'state': result.status, 'requestId': result.request_id,
                 'hostError': result.error, 'effectsDigest': digest(calls)}
        self.runtime.store.checkpoint_set(namespace, 'round:' + job['id'], ended)
        if result.status in ('completed', 'failed') and result.session_id == record['sessionId'] and result.request_id:
            self.runtime.store.checkpoint_set(namespace, 'proof:' + job['id'],
                {**ended, 'source': 'runtime_host_result'})

    def diagnostics(self, identity, job, relation):
        """Workspace records are review material, never a terminal attestation."""
        workspace = self.execution.production.workspace(identity, relation)
        folder = workspace / 'runs'
        if not workspace.resolve().is_relative_to(self.runtime.config.home.resolve()):
            raise RuntimeFault('forbidden', 'Restore the original production workspace')
        if not folder.exists():
            return None
        if folder.is_symlink():
            raise RuntimeFault('forbidden', 'Restore the original production diagnostics')
        records = []
        for index, path in enumerate(folder.glob('*.json')):
            if index >= 128:
                raise RuntimeFault('not_ready', 'Review the original diagnostic history within its explicit bound')
            if path.is_symlink() or path.stat().st_size > 65536:
                continue
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if (value.get('operationId') != job['id'] or value.get('sessionId') != relation['session']
                    or not isinstance(value.get('createdAt'), str)):
                continue
            records.append({'requestId': value.get('requestId'), 'state': value.get('state'),
                            'createdAt': value['createdAt'], 'recordDigest': digest(value),
                            'source': 'workspace_diagnostic_not_authoritative'})
        if not records:
            return None
        return {**max(records, key=lambda value: value['createdAt']),
                'historyDigest': digest({'records': sorted(records, key=lambda value: value['recordDigest'])}),
                'historyCount': len(records)}

    def artifacts(self, identity, relation):
        workspace = self.execution.production.workspace(identity, relation)
        root = workspace / 'artifacts'
        if not workspace.resolve().is_relative_to(self.runtime.config.home.resolve()):
            raise RuntimeFault('forbidden', 'Restore the original production workspace')
        if not root.exists():
            return []
        if (not root.resolve().is_relative_to(workspace.resolve())
                or any(path.is_symlink() for path in (root, workspace))):
            raise RuntimeFault('forbidden', 'Restore the original task artifact directory')
        files, entries, total = [], 0, 0
        for path in root.rglob('*'):
            entries += 1
            if entries > 256 or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise RuntimeFault('not_ready', 'Review the bounded original artifacts before continuation')
            if path.is_dir():
                continue
            if not path.is_file() or len(files) >= 64:
                raise RuntimeFault('not_ready', 'Review the original task artifact types and count')
            with path.open('rb') as stream:
                content = stream.read(16 * 1024 * 1024 + 1)
            total += len(content)
            if len(content) > 16 * 1024 * 1024 or total > 64 * 1024 * 1024:
                raise RuntimeFault('not_ready', 'Review the original task artifact size')
            files.append({'path': path.relative_to(root).as_posix(), 'bytes': len(content),
                          'digest': 'sha256:' + hashlib.sha256(content).hexdigest()})
        return sorted(files, key=lambda value: value['path'])

    def candidate(self, identity, job, *, starting=False):
        if job['handler'] != 'provider.production':
            raise RuntimeFault('forbidden', 'Only an original production task can continue')
        if job['state'] != 'unknown' and not (starting and job['state'] in ('queued', 'running')):
            raise RuntimeFault('not_ready', 'Original production is not awaiting recovery')
        relation = self.runtime.store.interaction(interaction_id=job['interaction'])
        if (not relation or relation['actor'] != identity.agent_id or relation['direction'] != 'inbound'
                or relation['name'] != 'delegation' or not relation['session'] or job['session'] != relation['session']):
            raise RuntimeFault('identity_conflict', 'Restore the original production session')
        for other in self.runtime.scheduler.records(identity.agent_id, self.execution.tasks.HANDLERS):
            same = other['interaction'] == relation['id'] or other['session'] == relation['session']
            if same and (other['id'] != job['id'] or not starting) and (
                    self.runtime.scheduler.is_executing(other['id']) or other['state'] in ('queued', 'running')):
                raise RuntimeFault('not_ready', 'Original execution has not released this session')
        if not self.execution.model_available() or not self.runtime.connection.connected or self.runtime.stop_requested.is_set():
            raise RuntimeFault('not_ready', 'Restore the original connected host before continuation')
        endpoint = self.execution.context(identity, relation, job)
        with endpoint.lock:
            cd, period = job['payload']['cardDigest'], job['payload']['period']
            record = endpoint.store.get('cards', cd)
            if not record or record.get('state') != 'started':
                raise RuntimeFault('forbidden', 'The original contract no longer permits production')
            card = record['card']
            template = endpoint.provider.template(card['templateDigest'])
            verify_card(template, card, endpoint.provider.keys)
            if len(card['signatures']['4']) >= period or len(card['signatures']['7']) >= period:
                raise RuntimeFault('forbidden', 'The original period is already delivered or accepted')
            if period != len(card['signatures']['4']) + 1 or period != len(card['signatures']['7']) + 1:
                raise RuntimeFault('identity_conflict', 'Restore the original first undelivered period')
            steps = record.get('steps', [])
            if any(not item.get('answer') or item['answer'].get('granted') is not True for item in steps):
                raise RuntimeFault('forbidden', 'The original protected step has no valid approval')
            definition = endpoint.provider.policy.definition(template)
            definition_hash = definition_digest(definition)
            if relation['details'].get('definitionDigest') != definition_hash:
                raise RuntimeFault('identity_conflict', 'Restore the original production agent definition')
            business = self.execution.business_tools(identity.profile, endpoint.provider.policy.config.get('businessTools', []))
            effects = []
            original_calls = {}
            for key, call in sorted(endpoint.store.all('production_calls').items()):
                if call.get('cardDigest') != cd:
                    continue
                context = call.get('context')
                if not isinstance(context, dict) or type(context.get('period')) is not int:
                    raise RuntimeFault('identity_conflict', 'Original production effect period is unavailable')
                if context['period'] != period:
                    continue
                original_calls[key] = call
                if any(context.get(field) != value for field, value in (
                        ('agentId', identity.agent_id), ('interactionId', relation['id']),
                        ('sessionId', relation['session']), ('contractRef', cd))):
                    raise RuntimeFault('identity_conflict', 'Original production effect binding changed')
                if (not isinstance(call.get('name'), str) or not isinstance(call.get('args'), dict)
                        or call.get('fingerprint') != digest({'name': call['name'], 'args': call['args']})):
                    raise RuntimeFault('identity_conflict', 'Original production effect input changed')
                result = call.get('result')
                if ('result' not in call or call.get('state') == 'unknown' or not isinstance(result, dict)
                        or result.get('status') == 'unknown' or result.get('state') == 'unknown'):
                    raise RuntimeFault('unknown_result', 'Reconcile the original production effect before continuation')
                if call['name'] == 'provider.task.deliver':
                    raise RuntimeFault('unknown_result', 'Reconcile the original delivery instead of producing again')
                if call['name'] in business:
                    validate_output(business[call['name']], result)
                elif call['name'] == 'provider.task.request_step':
                    if result.get('status') != 'waiting' or not steps:
                        raise RuntimeFault('unknown_result', 'Restore the original protected-step receipt')
                elif call['name'] == 'provider.task.selftest':
                    if result.get('status') not in ('pass', 'fail', 'error'):
                        raise RuntimeFault('unknown_result', 'Restore the original self-test report')
                else:
                    raise RuntimeFault('unknown_result', 'Restore the original production tool definition')
                effects.append({'call': key, 'tool': call['name'], 'inputDigest': call['fingerprint'],
                                'resultDigest': digest(result), 'status': result.get('status')})
            contract_state = digest({'card': card, 'steps': steps})
        namespace = self.namespace(identity)
        round_record = self.runtime.store.checkpoint(namespace, 'round:' + job['id'])
        proof = self.runtime.store.checkpoint(namespace, 'proof:' + job['id'])
        binding = {'taskRef': job['id'], 'actor': identity.agent_id, 'interactionId': relation['id'],
                   'sessionId': relation['session'], 'definitionDigest': definition_hash,
                   'contractRef': cd, 'period': period}
        if round_record and any(round_record.get(key) != value for key, value in binding.items()):
            raise RuntimeFault('identity_conflict', 'Original production round binding changed')
        verified = bool(proof and proof.get('source') == 'runtime_host_result'
                        and proof.get('state') in ('completed', 'failed') and proof.get('requestId')
                        and round_record and all(proof.get(key) == value for key, value in round_record.items())
                        and proof.get('effectsDigest') == digest(original_calls))
        diagnostic = round_record or self.diagnostics(identity, job, relation)
        if not diagnostic:
            raise RuntimeFault('not_ready', 'Original round evidence is unavailable for owner review')
        mode = 'runtime_verified' if verified and not definition.native_tools else 'owner_review'
        preview = {**binding, 'profile': identity.profile, 'service': identity.name,
                   'roundId': round_record['roundId'] if round_record else 'legacy:' + digest(diagnostic),
                   'recoveryMode': mode, 'roundEvidence': diagnostic,
                   'contractProgressDigest': contract_state, 'effects': effects, 'effectsDigest': digest({'calls': effects}),
                   'candidateArtifacts': self.artifacts(identity, relation),
                   'nativeTools': dict(definition.native_tools), 'originalDelivered': False,
                   'reviewRequirements': ['核对原轮已经结束且无活动执行者', '核对原生文件或外部工具效果，未决效果不得批准',
                                          '仅允许原合同、原期次、原Session及原权限内继续修正成果'],
                   'notice': '所有者审阅是本机声明，不是宿主权威回查。原失败、自测、期限及协议后果保留。'}
        return {'mode': mode, 'preview': preview, 'previewDigest': digest(preview)}

    def approve(self, identity, job, operation, candidate, confirmation_ref):
        if candidate['mode'] == 'runtime_verified':
            if confirmation_ref is not None:
                raise RuntimeFault('id_conflict', 'This continuation uses its original runtime evidence')
            return None
        preview = {'title': '审阅原生产轮并允许同单续接', **candidate['preview']}
        binding = {'actor': identity.agent_id, 'operationId': operation,
                   'subject': {'taskRef': job['id'], 'previewDigest': candidate['previewDigest']},
                   'stage': 'provider.production.resume', 'action': 'production.resume', 'preview': preview}
        decision = self.runtime.confirmations.ensure(binding)
        if confirmation_ref not in (None, decision['reference']):
            raise RuntimeFault('id_conflict', 'Use the original production recovery confirmation')
        if decision['status'] != 'approved':
            return {'status': 'waiting' if decision['status'] == 'pending' else 'rejected',
                    'data': {'taskRef': job['id'], 'confirmationRef': decision['reference'],
                             'confirmationStatus': decision['status'], 'preview': preview,
                             'nextAction': {'operation': 'runtime.open_ui', 'purpose': 'review_original_round'}}}
        self.runtime.confirmations.claim(decision['reference'], binding)
        return None

    def require_continuation(self, identity, job):
        continuation = job.get('continuation')
        if not continuation:
            return
        if set(continuation)=={'stepAnswer'}:
            return
        if continuation.get('kind') != 'provider.production':
            raise RuntimeFault('identity_conflict', 'Restore the original production continuation')
        current = self.candidate(identity, job, starting=True)
        if continuation.get('previewDigest') != current['previewDigest'] or continuation.get('mode') != current['mode']:
            raise RuntimeFault('identity_conflict', 'The reviewed production round or effects changed')
