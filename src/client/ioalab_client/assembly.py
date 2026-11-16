"""One account runtime and role assembly, regardless of the registered host."""
from contextlib import contextmanager
import importlib
from pathlib import Path

from ioalab_sdk.runtime import HostRegistry, RuntimeConfig, RuntimeFault
from ioalab_sdk.access import assembled as assemble_runtime
from .runtime import HOSTS, registry, validate


@contextmanager
def assembled(path):
    config = RuntimeConfig.load(path)
    base = Path(path).resolve().parent
    hosts=validate(config, base_dir=base)
    registrations = registry(config.host)
    resources = {}
    transferred = False
    try:
        claims={}
        # Resource constructors are side-effect free; start only after ResidentRuntime owns its lease.
        for ref, value in registrations['registrations'].items():
            owner = importlib.import_module(HOSTS[value['type']])
            if value.get('business_tools'):
                if not hasattr(owner, 'ConfiguredTools'):
                    raise RuntimeFault('not_ready', 'This adapter does not support business tool resources')
                if not isinstance(value['business_tools'],list):
                    raise RuntimeFault('invalid_input','Business tool resources require a list')
                for spec in value['business_tools']:
                    if not isinstance(spec,dict) or not isinstance(spec.get('factory'),str) or not isinstance(spec.get('profile'),str):
                        raise RuntimeFault('invalid_input','Business resources require explicit factory and profile names')
                    claim=(spec.get('factory'),spec.get('profile'))
                    if claim in claims and claims[claim]!=ref:
                        raise RuntimeFault('identity_conflict','Business resource storage is shared by conflicting host registrations')
                    claims[claim]=ref
                resources[ref] = owner.ConfiguredTools(value['business_tools'], config.identities, home=config.home, base_dir=base)
        host = HostRegistry(hosts, registrations['default'], tools=resources)
        default_tools = resources.get(registrations['default'])
        transferred=True
        with assemble_runtime(config,host,tools=default_tools,resources=resources,assembly='ioalab-client') as assembly:
            yield assembly
    finally:
        if not transferred:
            failed=[]
            for ref,resource in reversed(list(resources.items())):
                try:resource.close()
                except Exception:failed.append(ref)
            if failed:raise RuntimeFault('not_ready','Inspect resource shutdown: '+', '.join(failed),'inspect_business_resources')
