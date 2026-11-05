"""Normal TERM of owned descendants in this seat's private PID namespace only."""
import os
from pathlib import Path
import signal
import time

UIDS={10001,10002,10003,10004}
STARTUP_TIMEOUT=420


def identity(pid):
    folder=Path('/proc')/str(pid)
    try:
        fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in (folder/'status').read_text().splitlines() if ':' in line}
        stat=(folder/'stat').read_text().rsplit(')',1)[1].split()
    except FileNotFoundError:return None
    if [int(value) for value in fields.get('NSpid','').split()]!=[pid]:
        raise RuntimeError('Process is not visible in the isolated seat PID namespace')
    return {'pid':pid,'uid':int(fields['Uid'].split()[0]),'parent':int(fields['PPid']),
            'start':stat[19],'state':fields['State'].split()[0]}


def require_pid1():
    if os.getpid()!=1 or identity(1)['pid']!=1:
        raise RuntimeError('Seat supervision requires PID1 in its private namespace')


def descendants(allowed_root=()):
    require_pid1();rows={}
    for name in os.listdir('/proc'):
        if not name.isdigit() or int(name)==1:continue
        row=identity(int(name))
        if row is not None:rows[row['pid']]=row
    for row in rows.values():
        if row['uid'] not in UIDS and row['pid'] not in allowed_root:
            raise RuntimeError('Unknown process ownership remains in the seat')
        parent=row['parent'];seen={row['pid']}
        while parent!=1:
            if parent not in rows or parent in seen:raise RuntimeError('Seat descendant ownership chain changed')
            seen.add(parent);parent=rows[parent]['parent']
    return list(rows.values())


def terminate_owned(uids,*,timeout=90):
    """Stop phase-owned UIDs after their direct Popen roles have been waited.

    Reap only exact adopted target-UID zombies. Other roles retain their Popen
    wait owner and original return code throughout this phase.
    """
    require_pid1()
    if not set(uids)<=UIDS:raise ValueError('Only the four assigned seat UIDs may be stopped')
    deadline=time.monotonic()+timeout;sent=set()
    while True:
        owned=[row for row in descendants() if row['uid'] in uids]
        for row in owned:
            if row['parent']!=1 or row['state']!='Z':continue
            current=identity(row['pid'])
            if current is None:continue
            if current!=row:raise RuntimeError('Adopted process changed before exact reap')
            try:os.waitpid(row['pid'],os.WNOHANG)
            except ChildProcessError:pass
        rows=[row for row in owned if row['state']!='Z']
        if not rows:return
        for row in rows:
            key=(row['pid'],row['start'],row['uid'])
            if key in sent:continue
            current=next((value for value in descendants() if value['pid']==row['pid']),None)
            if current is None:continue
            if any(current[key]!=row[key] for key in ('pid','uid','start')):
                raise RuntimeError('Owned process changed before TERM')
            try:os.kill(row['pid'],signal.SIGTERM)
            except ProcessLookupError:continue
            sent.add(key)
        if time.monotonic()>=deadline:raise RuntimeError('Seat descendants have not normally exited; preserve the epoch')
        time.sleep(.1)
