"""Minimal external service assembly: the host is supplied by its owner.

The instance uses the ordinary resident SDK, outbound WS and authenticated IPC.
Publish and enable it through the normal provider operations and owner review.
No inbound HTTP server, platform implementation or official service is required.
"""
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime import HostRegistry


class _ServiceTools:
    def __init__(self,profiles):self.profiles=profiles
    def for_profile(self,profile):return dict(self.profiles.get(profile,{}))


def create_runtime(config,host,tools=None):
    """Construct, but do not start or publish, one participant-owned service."""
    if not isinstance(config,RuntimeConfig):config=RuntimeConfig.load(config)
    registry=HostRegistry({'application':host},'application',
                          tools={'application':_ServiceTools(tools or {})})
    role=ProviderRole(registry,tools)
    return ResidentRuntime(config,modules=(role,),host_registry=registry,host_status=registry.status_snapshot)
