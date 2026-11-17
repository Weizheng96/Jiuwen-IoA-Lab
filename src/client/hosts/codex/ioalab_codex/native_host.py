"""Product assembly of real Codex source/setup resources.

Desktop submission is unavailable until its original writer/approval bridge is
bound. This adapter never falls back to the independent restricted SDK driver.
"""
from importlib import metadata
from pathlib import Path
from ioalab_sdk.runtime import (HostRegistration, HostResource, HostInput, RuntimeFault,
                               SubmissionResult, SubmissionError)
from .native_skills import NATIVE_FILES
from .native_setup import NativeSetup


def _registration():
    try:
        version = metadata.version('ioalab-codex')
    except metadata.PackageNotFoundError:
        version = 'uninstalled'
    return HostRegistration('codex', version, readonly_context='unavailable', resources=tuple(
        HostResource('codex-native:'+name, name, 'application/octet-stream', None, 'installation',
                     'package:ioalab_codex/resources/native/'+name) for name in NATIVE_FILES))


class NativeCodexHost:
    registration = _registration()

    def submit(self, operation_id, input, session_id=None, *, execution=None, on_event=None, on_tool=None):
        if not isinstance(input, HostInput) or input.attempt is None:
            raise RuntimeFault('invalid_input', 'Use the original SDK-owned native attempt')
        return SubmissionResult(operation_id, input.attempt.nonce, 'failed', session_id,
            error=SubmissionError('not_ready', 'desktop_transport', 'not_submitted',
                'The original desktop writer, tool and approval transport is not bound', 'bind_native_desktop_transport'))

    def check(self):
        return {'state': 'unknown', 'evidence': 'No actual desktop runtime health fact has been received'}


def native_adapter(*, host_ref, resource_root, plugin_directory=None, resource_factory=None):
    root = Path(resource_root)
    if not root.is_absolute():
        raise RuntimeFault('invalid_input', 'Use the SDK-owned absolute resource directory')
    setup = NativeSetup(host_ref=host_ref, home=root/'launch')
    return {'host': NativeCodexHost(), 'native_handlers': {host_ref: setup.exchange},
            'native_resources': {host_ref: setup}}
