"""Actual Process-local bytes layout for SDK-authorized immutable references."""
from dataclasses import asdict
import hashlib
import os
from pathlib import Path

from a2c.codec import digest_bytes
from ioalab_sdk.runtime import RuntimeFault, encode, restrict


class ProcessFiles:
    def __init__(self,workspace,operation_id,attempt,reader,sink):
        if not callable(reader) or not callable(sink):
            raise RuntimeFault('not_ready','Bind original SDK resource reader and output sink')
        self.reader,self.sink=reader,sink
        key=hashlib.sha256(encode([operation_id,attempt.nonce,attempt.scope_digest,attempt.actor,attempt.profile]).encode()).hexdigest()
        self.root=Path(workspace)/'.jiuwen-resources'/key

    def materialize(self,resources):
        result=[]
        for resource in resources:
            # The original reader owns definition/material/output authorization.
            # A locator is never interpreted as a local path by this adapter.
            data=self.reader(resource)
            if not isinstance(data,bytes) or not resource.digest or digest_bytes(data)!=resource.digest:
                raise RuntimeFault('identity_conflict','Original resource bytes do not match their SDK digest')
            key=hashlib.sha256(encode(asdict(resource)).encode()).hexdigest()
            path=self.root/key
            if any(p.is_symlink() for p in (path,*path.parents)):
                raise RuntimeFault('forbidden','Process resource layout cannot use links')
            self.root.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if not path.is_file() or path.stat().st_nlink!=1 or path.read_bytes()!=data:
                    raise RuntimeFault('identity_conflict','Recover original Process resource bytes; do not replace them')
            else:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as stream:
                    restrict(path);stream.write(data);stream.flush();os.fsync(stream.fileno())
            result.append({**asdict(resource),'path':str(path)})
        return result
