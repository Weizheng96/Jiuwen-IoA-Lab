"""P26 candidate scoped bytes transport; does not execute business or read model paths."""
import base64
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from a2c.codec import digest, digest_bytes
from .files import private_json, read_json
from .host_submission import HostResource
from .protocol import RuntimeFault


@dataclass(frozen=True)
class ResourceGrant:
    resource: HostResource
    read: Callable


class ExecutionResources:
    """SDK creates grants only after original material authorization/period checks."""
    def __init__(self, *, binding, operation_id, grants=(), output_root, authorize, max_bytes=16*1024*1024):
        if not isinstance(operation_id,str) or not operation_id or type(max_bytes) is not int or max_bytes<1:
            raise RuntimeFault('invalid_input', 'Use the original operation and a positive SDK file bound')
        self.scope, self.operation_id = digest(binding), operation_id
        self.root, self.max_bytes = Path(output_root), max_bytes
        if not callable(authorize):raise RuntimeFault('forbidden', 'Use the original SDK material authorization fence')
        self.authorize=authorize
        self.grants = {}
        if not self.root.is_absolute() or self.root.is_symlink():
            raise RuntimeFault('invalid_input', 'Use the SDK-owned output directory')
        for grant in grants:
            resource = grant.resource
            if (not isinstance(resource,HostResource) or resource.access_scope != self.scope or
                not resource.digest or resource.resource_id in self.grants or not callable(grant.read)):
                raise RuntimeFault('forbidden', 'Use distinct original authorized resource grants')
            self.grants[resource.resource_id] = grant

    def read_resource(self, resource):
        grant = self.grants.get(resource.resource_id) if isinstance(resource,HostResource) else None
        if not grant or grant.resource != resource:
            raise RuntimeFault('forbidden', 'Resource was not granted to this original execution scope')
        self.authorize('read',resource)
        try:data = grant.read()
        except RuntimeFault:raise
        except (OSError,ValueError):
            raise RuntimeFault('not_ready', 'Original granted material is unavailable', 'recover_original_resource') from None
        if not isinstance(data,bytes) or len(data)>self.max_bytes or digest_bytes(data)!=resource.digest:
            raise RuntimeFault('identity_conflict', 'Original material bytes do not match the authorized reference')
        return data

    def store_output(self, name, media_type, data, expected_digest):
        if (not isinstance(name,str) or not name or len(name)>200 or
            any(char in name for char in ('/','\\','\x00')) or name in ('.','..') or
            not isinstance(media_type,str) or not media_type or not isinstance(data,bytes) or
            len(data)>self.max_bytes or not expected_digest or digest_bytes(data)!=expected_digest):
            raise RuntimeFault('invalid_input', 'Return bounded actual output bytes with a verified digest')
        self.authorize('output',None)
        # Physical storage names never use model/host file paths or output labels.
        identity = digest({'scope':self.scope,'operation':self.operation_id,'name':name,
                           'mediaType':media_type,'contentDigest':expected_digest})
        key = identity.removeprefix('sha256:')
        path = self.root/(key+'.json')
        resource = HostResource(identity,name,media_type,expected_digest,self.scope,'sdk-output:'+key)
        if path.exists():
            old = self._cached(path)
            if old.get('resource') != asdict(resource):
                raise RuntimeFault('identity_conflict', 'Original cached output reference changed')
            recovered = self._decode(old)
            if digest_bytes(recovered)!=expected_digest or recovered!=data:
                raise RuntimeFault('identity_conflict', 'Original output receipt bytes changed')
            return resource
        private_json(path,{'resource':asdict(resource),'bytes':base64.b64encode(data).decode('ascii')})
        return resource

    def read_output(self, resource):
        if not isinstance(resource,HostResource) or resource.access_scope != self.scope:
            raise RuntimeFault('forbidden', 'Output belongs to another execution scope')
        expected = digest({'scope':self.scope,'operation':self.operation_id,'name':resource.name,
                           'mediaType':resource.media_type,'contentDigest':resource.digest})
        if resource.resource_id != expected or resource.locator != 'sdk-output:'+expected.removeprefix('sha256:'):
            raise RuntimeFault('forbidden', 'Use the original SDK-issued output reference')
        self.authorize('read_output',resource)
        value = self._cached(self.root/(expected.removeprefix('sha256:')+'.json'))
        if value.get('resource') != asdict(resource):
            raise RuntimeFault('identity_conflict', 'Original output reference changed')
        data = self._decode(value)
        if len(data)>self.max_bytes or digest_bytes(data)!=resource.digest:
            raise RuntimeFault('identity_conflict', 'Original output bytes changed')
        return data

    @staticmethod
    def _cached(path):
        if path.is_symlink():raise RuntimeFault('forbidden', 'Original output cache cannot be a link')
        try:return read_json(path)
        except (OSError,ValueError):
            raise RuntimeFault('not_ready', 'Recover original output bytes; do not repeat production', 'recover_original_resource') from None

    @staticmethod
    def _decode(value):
        try:return base64.b64decode(value['bytes'],validate=True)
        except (KeyError,ValueError,TypeError):
            raise RuntimeFault('identity_conflict', 'Original output cache bytes are invalid') from None
