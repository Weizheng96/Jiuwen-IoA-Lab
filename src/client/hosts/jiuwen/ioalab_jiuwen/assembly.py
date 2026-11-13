"""One resident assembly for every launcher of the Jiuwen host adapter."""
from contextlib import contextmanager
from pathlib import Path

from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime import ResidentRuntime, RuntimeConfig, RuntimeFault
from .process import ProcessHost
from .configuration import validate_host
from .tools import ConfiguredTools


class Assembly:
    """Business tools, the original host, both role modules and their resident runtime."""

    def __init__(self, config, runtime, host, tools):
        self.config, self.runtime, self.host, self.tools = config, runtime, host, tools

    def run(self):
        """Probe the host before any business tool starts, then serve until stopped."""
        if not self.host.probe()['available']:
            raise RuntimeFault('not_ready', 'Jiuwen model/capability probe did not pass', 'configure_host')
        self.tools.start()
        self.runtime.run()


@contextmanager
def assembled(path):
    """Yield the resident assembly; always close the runtime before its business tools."""
    path = Path(path)
    base = path.resolve().parent
    config = RuntimeConfig.load(path)
    host = validate_host(config,base_dir=base) or ProcessHost.from_config({},base_dir=base)
    host_config = dict(config.host)
    tools = ConfiguredTools(host_config.pop('business_tools', []), config.identities, home=config.home, base_dir=base)
    runtime = None
    try:
        personal = next((item['key'] for item in config.identities if item['role'] == 'assistant'), None)
        runtime = ResidentRuntime(config, modules=(AssistantRole(host, tools.for_profile(personal)), ProviderRole(host, tools.by_profile)),
                                  host_status=lambda: {**host.capabilities(), 'businessTools': tools.status()}, assembly='ioalab-jiuwen')
        yield Assembly(config, runtime, host, tools)
    finally:
        # Business resources must outlive active runtime jobs, even if shutdown fails.
        try:
            if runtime:
                runtime.close()
        finally:
            tools.close()
