"""Explicit Jiuwen application configuration; SDK owns the shared runtime lifecycle."""
from contextlib import contextmanager
from pathlib import Path
from ioalab_sdk.runtime import RuntimeConfig
from ioalab_sdk.access import Assembly, assembled as assemble_runtime
from .configuration import validate_host
from .process import ProcessHost
from .tools import ConfiguredTools


@contextmanager
def assembled(path):
    base=Path(path).resolve().parent
    config=RuntimeConfig.load(path)
    host=validate_host(config,base_dir=base) or ProcessHost.from_config({},base_dir=base)
    tools=ConfiguredTools(config.host.get('business_tools',[]),config.identities,home=config.home,base_dir=base)
    with assemble_runtime(config,host,tools=tools,resources={'jiuwen':tools},assembly='ioalab-jiuwen') as assembly:
        yield assembly
