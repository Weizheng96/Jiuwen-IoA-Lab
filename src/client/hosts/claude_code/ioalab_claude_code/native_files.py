"""Local resource access for S01. Public SDK owns permission and business effects."""
import hashlib
from pathlib import Path

from ioalab_sdk.runtime import RuntimeFault,HostResource


class NativeFiles:
    def __init__(self,*,reader=None):
        self.reader=reader

    def materialize(self,resources):
        result=[]
        for resource in resources:
            if not isinstance(resource,HostResource):raise RuntimeFault('invalid_input','Use SDK resource references')
            # A reader is supplied only after P26 scope/permission validation.
            if self.reader is None:
                raise RuntimeFault('not_ready','Bind the SDK-authorized material reader before native execution','bind_native_files')
            value=self.reader(resource)
            if not isinstance(value,(str,Path)):
                raise RuntimeFault('not_ready','The original resource is not a native-readable local file')
            path=Path(value)
            if not path.is_absolute() or not path.is_file():
                raise RuntimeFault('not_ready','The original resource has no readable native path')
            with path.open('rb') as stream:
                actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if resource.digest and resource.digest not in (actual,'sha256:'+actual):
                raise RuntimeFault('identity_conflict','The original resource digest changed')
            result.append({'resourceId':resource.resource_id,'path':str(path),'digest':actual,
                           'name':resource.name,'mediaType':resource.media_type,'accessScope':resource.access_scope})
        return tuple(result)
