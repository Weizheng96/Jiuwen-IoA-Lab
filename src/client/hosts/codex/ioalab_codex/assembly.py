"""Explicit single-host application entry, sharing the SDK assembly lifecycle."""
from contextlib import contextmanager
from pathlib import Path
from ioalab_sdk.runtime import RuntimeConfig
from ioalab_sdk.access import Assembly, assembled as assemble_runtime
from .configuration import validate_host


@contextmanager
def assembled(path):
    config=RuntimeConfig.load(path)
    host=validate_host(config,base_dir=Path(path).resolve().parent)
    with assemble_runtime(config,host,assembly='ioalab-codex') as assembly:
        yield assembly
