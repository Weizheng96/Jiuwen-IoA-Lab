"""OS-owned directory lease; automatically released when the process exits."""
import os
from pathlib import Path
import time


class Lease:
    def __init__(self, path, timeout=30):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        self.file=path.open('a+b')
        # Windows supports byte locks beyond EOF; no marker write may race the lock.
        until=time.monotonic()+timeout
        while True:
            try:
                self.file.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=until:
                    self.file.close()
                    raise RuntimeError('Identity directory is already in use: '+str(path.parent))
                time.sleep(.1)

    def __enter__(self): return self

    def __exit__(self,*args): self.close()

    def close(self):
        if self.file.closed: return
        self.file.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(self.file,fcntl.LOCK_UN)
        self.file.close()
