"""Public read-only verification of stopped epoch resources."""
import sqlite3
from pathlib import Path
from .profile import capacity,RESOURCE_LAYOUT
from .sandbox import DockerSandbox


def verify(config):
    import fcntl
    home=Path(config['control_directory'])
    if not home.is_absolute():raise ValueError('Use the original absolute control directory')
    with (home/'coordinator.lock').open('r+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        connection=sqlite3.connect((home/'control.sqlite').as_uri()+'?mode=ro',uri=True)
        try:
            profile=config['deployment_profile']
            bound=connection.execute('SELECT profile,capacity,resource_layout FROM deployment').fetchall()
            if bound!=[(profile,capacity(profile),RESOURCE_LAYOUT)]:raise ValueError('Original trial layout changed')
            rows=connection.execute('SELECT id,epoch FROM seats ORDER BY id').fetchall()
            if [row[0] for row in rows]!=list(range(1,capacity(profile)+1)):raise ValueError('Original trial capacity changed')
            sandbox=DockerSandbox(config['sandbox'],deployment_profile=profile)
            for seat,epoch in rows:
                if epoch:sandbox.verify_stopped(seat,epoch)
            return {'allEpochResourcesStopped':True,'capacity':capacity(profile)}
        finally:connection.close()
