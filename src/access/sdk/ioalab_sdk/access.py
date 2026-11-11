"""`ioalab-access` and `python -m ioalab_sdk.access`: the SDK role assembly.

The shared runtime receives these roles through `run` and never imports this module."""
from .runtime import run


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
