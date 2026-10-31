"""Map owned business resources into the ordinary provider SDK configuration."""
from copy import deepcopy
import re
from types import SimpleNamespace

from .catalog import definitions


def prepare(service_id, *, profile, verifier_ref, payment_provider, now=None):
    """Return an unsigned publication and execution config; perform no external effects.

    Upload the owned verifier using provider.verifier.publish first. The SDK checks
    its actual bytes/kind again when publishing and executing the service.
    """
    from ioalab_sdk.templates import template_from_definition

    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile):
        raise ValueError('A local service profile is required')
    definition = next((item for item in definitions() if item['id'] == service_id), None)
    if definition is None:
        raise ValueError('Unknown official service ID')
    verifier = definition['verifier']
    if (not isinstance(verifier_ref, dict) or set(verifier_ref) != {'id', 'uri', 'digest'}
            or verifier_ref.get('id') != verifier['id']
            or not isinstance(verifier_ref.get('uri'), str) or not verifier_ref['uri']
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(verifier_ref.get('digest', '')))):
        raise ValueError('Use the actual complete reference of the owned verifier')
    if (not isinstance(payment_provider, dict) or set(payment_provider) != {'id', 'jwksUri'}
            or not all(isinstance(value, str) and value for value in payment_provider.values())):
        raise ValueError('Use the platform payment declaration')
    if definition['formation'] not in ('countersign', 'proposal'):
        raise ValueError('Official services use ordinary provider admission')

    def published_reference(*, verifier_id, source, reads):
        if (verifier_id, source, reads) != (verifier['id'], verifier['source'], verifier.get('reads', [])):
            raise ValueError('Template requested a different verifier')
        return deepcopy(verifier_ref)

    template = template_from_definition(definition, packages=SimpleNamespace(put=published_reference),
                                        payment_provider=payment_provider, now=now)
    deliverable = definition['deliverable']
    generator = definition.get('generator', {})
    resources = {
        'deliverable': {'name': deliverable['name'],
                        'file': deliverable.get('file', deliverable['name'] + '.json'),
                        'mediaType': deliverable.get('mediaType', 'application/json'),
                        'description': deliverable.get('description', deliverable.get('title', deliverable['name'])),
                        'outputShape': generator.get('output', '')},
        'pricing': deepcopy(definition['pricing']),
        'acceptance': {deliverable['name']: {'verifiers': {verifier.get('kind', 'script'): deepcopy(verifier_ref)},
                                            'notCovered': list(definition.get('notCovered', []))}},
        'acceptWithin': {deliverable['name']: definition.get('deadlines', {}).get('acceptWithin', 'PT15M')},
        'requiredAuthorizations': deepcopy(definition.get('steps', [])),
    }
    instructions = '\n'.join(part for part in (generator.get('system', ''), generator.get('quote_system', ''),
        '依据本单合同及业务资源生产指定文件。报价遵守本服务定价范围；不得将拟卡费当成交付总价。'
        '需要的业务环节先调用provider.task.request_step，获得原用户授权后继续；不得替用户同意。'
        '生产后调用provider.task.selftest并根据判据修正，通过后调用provider.task.deliver；不以文字声称代替工具回执。') if part)
    return {
        'service': {'profile': profile, 'name': definition['name'], 'description': definition['description'],
                    'tags': list(definition.get('tags', [])), 'categoryId': definition['category'], 'templates': [template]},
        'execution': {'instructions': instructions, 'skills': [], 'resources': resources,
                      'max_active_delegations': definition['capacity'], 'accepting': bool(definition['runnable'])},
    }
