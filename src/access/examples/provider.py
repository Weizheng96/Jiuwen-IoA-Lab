"""Minimal external service assembly: the host is supplied by its owner.

The instance uses the ordinary resident SDK, outbound WS and authenticated IPC.
Publish and enable it through the normal provider operations and owner review.
No inbound HTTP server, platform implementation or official service is required.
"""
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime


def create_runtime(config,host,tools=None):
    """Construct, but do not start or publish, one participant-owned service."""
    if not isinstance(config,RuntimeConfig):config=RuntimeConfig.load(config)
    role=ProviderRole(host,tools)
    return ResidentRuntime(config,modules=(role,),host_status=host.capabilities)
