"""Compose both existing SDK roles with the selected Claude host."""
from contextlib import contextmanager
from pathlib import Path
from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime import ResidentRuntime, RuntimeConfig, RuntimeFault
from .configuration import validate_host


class Assembly:
    def __init__(self, runtime, host):
        self.runtime, self.host = runtime, host

    def run(self):
        if not self.host.probe()['available']:
            raise RuntimeFault('not_ready', 'Claude model/capability probe did not pass', 'configure_host')
        self.runtime.run()


@contextmanager
def assembled(path):
    config = RuntimeConfig.load(path)
    host = validate_host(config, base_dir=Path(path).resolve().parent)
    # Claude's first release exposes only the fixed SDK tools. Other resources
    # are rejected by its host validator rather than silently discarded.
    runtime = ResidentRuntime(config, modules=(AssistantRole(host, {}), ProviderRole(host, {})),
                              host_status=host.capabilities, assembly='ioalab-claude-code')
    try:
        yield Assembly(runtime, host)
    finally:
        runtime.close()
