"""Private, verifiable pre-switch snapshots. Never restores or rolls back business data."""
import hashlib
import os
from pathlib import Path
import stat
import tarfile
import uuid

from .release import digest, read_json, sha
from .workspace import path_without_links, save


def members(layout):
    result={}
    for name,root in (('core',layout.state/'core'),('agents',layout.state/'agents'),
                      ('config',layout.config),('deployment',layout.private)):
        root=path_without_links(root)
        if not root.is_dir():raise RuntimeError('Restore the original state roots before taking a backup')
        for path in [root,*sorted(root.rglob('*'))]:
            metadata=path.lstat();key=name+('/'+path.relative_to(root).as_posix() if path!=root else '')
            entry={'mode':stat.S_IMODE(metadata.st_mode),'uid':metadata.st_uid,'gid':metadata.st_gid}
            if stat.S_ISLNK(metadata.st_mode):entry.update(kind='link',target=os.readlink(path))
            elif stat.S_ISDIR(metadata.st_mode):entry.update(kind='directory')
            elif stat.S_ISREG(metadata.st_mode):entry.update(kind='file',size=metadata.st_size,sha256=sha(path))
            else:raise RuntimeError('A special state file remains; verify the original writers have stopped')
            result[key]=(path,entry)
    return result


def verify(folder):
    folder=path_without_links(folder);record=read_json(folder/'manifest.json')
    expected=record.get('members')
    if (record.get('format')!='ioalab.deployment-backup/1' or not isinstance(expected,dict)
            or record.get('phase') not in ('archiving','ready')):
        raise RuntimeError('Restore the original backup manifest')
    archive=path_without_links(folder/'state.tar')
    if not archive.is_file():raise RuntimeError('The original backup archive is incomplete; preserve it for review')
    try:
        with tarfile.open(archive,'r:') as source:
            entries=source.getmembers()
            if len(entries)!=len(expected) or {item.name for item in entries}!=set(expected):raise ValueError()
            for item in entries:
                entry=expected[item.name]
                if item.mode!=entry['mode'] or item.uid!=entry['uid'] or item.gid!=entry['gid']:raise ValueError()
                if entry['kind']=='file':
                    if not item.isfile() or item.size!=entry['size']:raise ValueError()
                    stream=source.extractfile(item)
                    if stream is None:raise ValueError()
                    with stream:
                        checksum=hashlib.file_digest(stream,'sha256').hexdigest()
                    if checksum!=entry['sha256']:raise ValueError()
                elif entry['kind']=='directory':
                    if not item.isdir():raise ValueError()
                elif entry['kind']=='link':
                    if not item.issym() or item.linkname!=entry['target']:raise ValueError()
                else:raise ValueError()
    except (tarfile.TarError,ValueError,KeyError,OSError):
        raise RuntimeError('Original backup verification failed; preserve the archive and manifest') from None
    checksum=sha(archive)
    if record.get('phase')=='ready' and record.get('archiveSha256')!=checksum:
        raise RuntimeError('The completed original backup archive changed')
    return record,checksum


def capture(layout,operation,previous_set,*,released):
    """Caller retains the deployment and real SDK execution locks for this entire call."""
    if not isinstance(operation,str) or uuid.UUID(operation).hex!=operation:
        raise ValueError('Use the original deployment operation UUID')
    if not released():raise RuntimeError('Original writers have not released; backup and activation are forbidden')
    folder=path_without_links(layout.state/'backups'/operation)
    parent=folder.parent
    parent.mkdir(mode=0o700,exist_ok=True)
    identity={'format':'ioalab.deployment-backup/1','deploymentId':layout.inspect()['deploymentId'],
              'operationId':operation,'previousSet':previous_set}
    if folder.exists():
        record,checksum=verify(folder)
        if any(record.get(k)!=v for k,v in identity.items()):raise RuntimeError('Backup belongs to another original operation')
        if record['phase']=='archiving' and {key:value for key,(_,value) in members(layout).items()}!=record['members']:
            record['phase']='source_changed';save(folder/'manifest.json',record)
            raise RuntimeError('Original backup source changed before completion; preserve it for explicit review')
    else:
        source=members(layout)
        folder.mkdir(mode=0o700)
        record={**identity,'phase':'archiving','members':{key:value for key,(_,value) in source.items()}}
        save(folder/'manifest.json',record)
        # Dereference is off: a link is evidence, never permission to read its target.
        descriptor=os.open(folder/'state.tar',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'wb') as output:
            with tarfile.open(fileobj=output,mode='w:',dereference=False) as archive:
                for key,(path,entry) in source.items():
                    info=tarfile.TarInfo(key)
                    info.mode,info.uid,info.gid=entry['mode'],entry['uid'],entry['gid']
                    info.mtime=int(path.lstat().st_mtime)
                    if entry['kind']=='file':
                        info.size=entry['size']
                        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
                        with os.fdopen(fd,'rb') as stream:archive.addfile(info,stream)
                    else:
                        info.type=tarfile.DIRTYPE if entry['kind']=='directory' else tarfile.SYMTYPE
                        if entry['kind']=='link':info.linkname=entry['target']
                        archive.addfile(info)
            output.flush();os.fsync(output.fileno())
        if {key:value for key,(_,value) in members(layout).items()}!=record['members']:
            record['phase']='source_changed';save(folder/'manifest.json',record)
            raise RuntimeError('State changed while backing up; preserve the original evidence and inspect writers')
        record,checksum=verify(folder)
    if not released():
        record['phase']='writer_returned';save(folder/'manifest.json',record)
        raise RuntimeError('A writer reappeared during backup; activation is forbidden and evidence requires review')
    if record['phase']!='ready':
        record.update(phase='ready',archiveSha256=checksum)
        save(folder/'manifest.json',record)
    return {'path':str(folder),'archiveSha256':checksum,'previousSet':previous_set,
            'manifestDigest':digest(record),'restored':False}
