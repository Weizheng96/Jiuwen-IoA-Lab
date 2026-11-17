"""Pure SDK-to-adapter resource-factory port; Registry owns every live scope."""
import threading

from .host_submission import SubmissionAttempt
from .protocol import RuntimeFault


class SubmissionResourcePort:
    def __init__(self):
        self._lookup = None
        self._principal = None
        self._lock = threading.RLock()

    def bind(self, lookup):
        if not callable(lookup):
            raise RuntimeFault('invalid_input', 'Use the original SDK Registry resource delegate')
        principal = (getattr(lookup,'__self__',None),getattr(lookup,'__func__',lookup))
        with self._lock:
            if self._lookup is not None:
                if all(before is after for before,after in zip(self._principal,principal)):
                    return
                raise RuntimeFault('identity_conflict', 'This resource port already belongs to another Registry delegate')
            self._lookup,self._principal = lookup,principal

    def for_host(self, host_ref):
        if not isinstance(host_ref,str) or not host_ref or len(host_ref)>8192:
            raise RuntimeFault('invalid_input', 'Use the originally registered host reference')

        def resource_factory(operation_id, attempt):
            if not isinstance(operation_id,str) or not operation_id or not isinstance(attempt,SubmissionAttempt):
                raise RuntimeFault('invalid_input', 'Use the original SDK operation and full submission attempt')
            with self._lock:lookup = self._lookup
            if lookup is None:
                raise RuntimeFault('not_ready', 'Original SDK resource scope is not bound', 'restore_original_resource_scope')
            # No port lock spans Registry validation or any actual control work.
            result = lookup(host_ref,operation_id,attempt)
            if not isinstance(result,tuple) or len(result)!=2 or not all(callable(item) for item in result):
                raise RuntimeFault('not_ready', 'Original SDK resource reader and sink are unavailable', 'restore_original_resource_scope')
            return result

        return resource_factory
