"""`ioalab-access` and `python -m ioalab_sdk.access`: the SDK role assembly.

The shared runtime receives these roles through `run` and never imports this module."""
from .runtime import run
from contextlib import contextmanager


class Assembly:
    """Compose injected hosts/resources; never discover or construct a concrete adapter."""
    def __init__(self,config,host,*,tools=None,resources=None,assembly=None):
        from .assistant import AssistantRole
        from .provider import ProviderRole
        from .runtime import HostRegistry,ResidentRuntime
        self.config,self.host,self.tools=config,host,tools
        self.resources=dict(resources or {})
        personal=next((item['key'] for item in config.identities if item['role']=='assistant'),None)
        status=lambda:{**host.capabilities(),**({'businessTools':tools.status()} if tools else {})}
        self.runtime=ResidentRuntime(config,modules=(AssistantRole(host,tools.for_profile(personal) if tools else {}),
                                      ProviderRole(host,tools.by_profile if tools else {})),host_status=status,assembly=assembly,
                                      host_registry=host if isinstance(host,HostRegistry) else None)

    def run(self):
        from .runtime import RuntimeFault
        result=self.host.probe()
        if not result.get('defaultAvailable',result.get('available')):
            raise RuntimeFault('not_ready','Host model/capability probe did not pass','configure_host')
        for ref,resource in self.resources.items():
            try:resource.start()
            except Exception as exc:
                raise RuntimeFault('not_ready','Registered business resource could not start: '+ref,'configure_business_tools') from exc
        self.runtime.run()


@contextmanager
def assembled(config,host,*,tools=None,resources=None,assembly=None):
    """Single role/runtime/resource lifecycle for explicit application and product callers."""
    from .runtime import RuntimeFault
    value=None
    try:
        value=Assembly(config,host,tools=tools,resources=resources,assembly=assembly)
        yield value
    finally:
        if value:value.runtime.close()
        failed=[]
        for ref,resource in reversed(list((resources or {}).items())):
            try:resource.close()
            except Exception:failed.append(ref)
        if failed:raise RuntimeFault('not_ready','Inspect resource shutdown: '+', '.join(failed),'inspect_business_resources')


def modules():
    from .assistant import AssistantRole
    from .provider import ProviderRole
    return AssistantRole(),ProviderRole()


def validate(config):
    from .provider import validate_provider
    validate_provider(config.provider,config.identities)


def service_fields():
    from .provider import EDITABLE_SERVICE_FIELDS
    return EDITABLE_SERVICE_FIELDS


def main(argv=None):
    return run(argv,modules=modules,validate=validate,service_fields=service_fields)


if __name__=='__main__':raise SystemExit(main())
