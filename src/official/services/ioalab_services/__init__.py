"""Official service resources and their deployment coordinator; execution belongs to the ordinary IoA SDK.

The package entry is the only import surface for other modules and tools. `prepare_service`
builds one unsigned service definition; `prepare_deployment` is the coordinator's
prepare phase for a whole deployment.
"""
from .authorization import administrator_session, authorize
from .catalog import definitions, seed_manifest
from .coordinator import status
from .deployment import Deployment
from .lifecycle import serve
from .preparation import prepare as prepare_deployment
from .profiles import prepare as prepare_service
from .programming import production_document, variant

__all__ = ['Deployment', 'administrator_session', 'authorize', 'definitions', 'prepare_deployment', 'prepare_service',
           'production_document', 'seed_manifest', 'serve', 'status', 'variant']
