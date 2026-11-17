"""`ioalab-access` and `python -m ioalab_sdk.access`: the SDK role assembly.

The shared runtime receives these roles through `run` and never imports this module."""
from .runtime import run
from contextlib import contextmanager


class Assembly:
    """Compose injected hosts/resources; never discover or construct a concrete adapter."""
    def __init__(self,config,host,*,tools=None,resources=None,assembly=None,native_handlers=None,native_resources=None,native_entry_required=False,resource_port=None):
        from .assistant import AssistantRole
        from .provider import ProviderRole
        from .runtime import HostRegistry,ResidentRuntime,SubmissionResourcePort
        if resource_port is not None and not isinstance(resource_port,SubmissionResourcePort):
            raise ValueError('Use the SDK-owned submission resource port')
        self.config,self.tools=config,tools
        self.host=host if isinstance(host,HostRegistry) else HostRegistry({'application':host},'application',tools={'application':tools} if tools else None)
        host=self.host
        self.resources=dict(resources or {})
        self.resources.update({'native:'+ref:value for ref,value in (native_resources or {}).items()})
        personal=next((item['key'] for item in config.identities if item['role']=='assistant'),None)
        status=lambda:{**host.status_snapshot(),**({'businessTools':tools.status()} if tools else {})}
        self.runtime=ResidentRuntime(config,modules=(AssistantRole(host,tools.for_profile(personal) if tools else {}),
                                      ProviderRole(host,tools.by_profile if tools else {})),host_status=status,assembly=assembly,
                                      host_registry=host,native_handlers=native_handlers,native_resources=native_resources,native_entry_required=native_entry_required)
        self.resource_port=resource_port or SubmissionResourcePort()
        try:self.resource_port.bind(host._resources_for)
        except Exception:
            self.runtime.close()
            raise

    def run(self):
        from .runtime import RuntimeFault
        for ref,resource in self.resources.items():
            try:resource.start()
            except Exception as exc:
                raise RuntimeFault('not_ready','Registered business resource could not start: '+ref,'configure_business_tools') from exc
        self.runtime.run()


@contextmanager
def assembled(config,host,*,tools=None,resources=None,assembly=None,native_handlers=None,native_resources=None,native_entry_required=False,resource_port=None):
    """Single role/runtime/resource lifecycle for explicit application and product callers."""
    from .runtime import RuntimeFault
    value=None
    try:
        value=Assembly(config,host,tools=tools,resources=resources,assembly=assembly,native_handlers=native_handlers,native_resources=native_resources,native_entry_required=native_entry_required,resource_port=resource_port)
        yield value
    finally:
        if value:value.runtime.close()
        failed=[]
        owned={**dict(resources or {}),**{'native:'+ref:value for ref,value in (native_resources or {}).items()}}
        for ref,resource in reversed(list(owned.items())):
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
