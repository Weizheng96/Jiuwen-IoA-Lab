"""Frozen, bounded business documents; never exposes the host's global skill reader."""
import os
from pathlib import Path,PurePosixPath,PureWindowsPath
import re
import uuid

from a2c.codec import digest,digest_bytes
from a2c.errors import A2CError
from ioalab_sdk.runtime.files import private_json,private_text,read_json
from ioalab_sdk.runtime.protocol import RuntimeFault

MAX_FILES=128
MAX_FILE=256*1024
MAX_BYTES=2*1024*1024


def fail(message):raise RuntimeFault('identity_conflict',message,'restore_business_skill_snapshot')


def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&1024)


def relative(value):
    if not isinstance(value,str) or not value or len(value)>512:fail('Use a bounded business document path')
    path=PurePosixPath(value.replace('\\','/'));windows=PureWindowsPath(value)
    if path.is_absolute() or windows.drive or windows.root or '..' in path.parts:fail('Business document path is outside its selected skill')
    result=path.as_posix()
    if result!='SKILL.md' and not (result.startswith('references/') and result.endswith('.md') and all(not part.startswith('.') for part in path.parts)):
        fail('Only the main business skill and Markdown references can be read')
    return result


def text(path,root):
    path=Path(path);root=Path(root)
    try:
        if not path.resolve().is_relative_to(root.resolve()):fail('Business document is outside the owner-selected directory')
        for node in (path,*path.parents):
            if node==root.parent:break
            if linked(node):fail('Business document links are not accepted')
        with path.open('rb') as stream:data=stream.read(MAX_FILE+1)
    except OSError:fail('Business document is unavailable; restore its original source or snapshot')
    if not data or len(data)>MAX_FILE:fail('Business document is empty or exceeds its size bound')
    try:return data.decode('utf-8')
    except UnicodeDecodeError:fail('Business documents must be UTF-8')


def documents(directory):
    root=Path(directory)
    if not root.is_absolute() or not root.is_dir() or linked(root):fail('Configure an absolute business skill source directory without links')
    values={'SKILL.md':text(root/'SKILL.md',root)};refs=root/'references'
    if refs.exists():
        if linked(refs):fail('Business reference directory cannot be a link')
        walked=0
        for folder,dirs,files in os.walk(refs,followlinks=False):
            walked+=1
            if walked>MAX_FILES:fail('Business reference tree exceeds its bound')
            dirs[:]=[name for name in dirs if not name.startswith('.')]
            if any(linked(Path(folder)/name) for name in dirs):fail('Business reference directories cannot be links')
            for filename in files:
                if filename.startswith('.') or not filename.endswith('.md'):continue
                path=Path(folder)/filename;key=relative(path.relative_to(root).as_posix())
                values[key]=text(path,root)
                if len(values)>MAX_FILES:fail('Business skill has too many documents')
    if sum(len(value.encode('utf-8')) for value in values.values())>MAX_BYTES:fail('Business skill documents exceed their total bound')
    return values


class FrozenSkills:
    def __init__(self,workspace,names,sources,binding,*,resume=False):
        self.names=tuple(names);self.root=Path(workspace)/'skill-documents';self.binding=binding
        if not self.names or len(self.names)>32 or any(not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',name) for name in self.names) or len({name.casefold() for name in self.names})!=len(self.names):
            fail('Choose distinct owner-configured business skill names')
        if not self.root.exists():
            if resume:fail('Restore the original business documents before resuming this session')
            selected={name:documents(sources[name]) if name in sources else fail('Selected business skill source is not configured') for name in self.names}
            if sum(len(files) for files in selected.values())>MAX_FILES or sum(len(value.encode('utf-8')) for files in selected.values() for value in files.values())>MAX_BYTES:
                fail('Selected business documents exceed their total bound')
            pending=Path(workspace)/('.skill-documents-'+uuid.uuid4().hex);pending.mkdir(parents=True)
            hashes={}
            for name,files in selected.items():
                hashes[name]={}
                for key,value in files.items():
                    private_text(pending/name/key,value);hashes[name][key]=digest_bytes(value.encode('utf-8'))
            manifest={'schema':1,'selection':list(self.names),'binding':binding,'files':hashes}
            private_json(pending/'manifest.json',{**manifest,'digest':digest(manifest)})
            try:os.replace(pending,self.root)
            except OSError:
                if not self.root.exists():raise
        self.verify()

    def verify(self):
        try:
            if linked(self.root) or linked(self.root/'manifest.json') or (self.root/'manifest.json').stat().st_size>MAX_FILE:fail('Invalid original business document manifest')
            manifest=read_json(self.root/'manifest.json')
            if set(manifest)!={'schema','selection','binding','files','digest'} or manifest['schema']!=1 or manifest['selection']!=list(self.names) or manifest['binding']!=self.binding or manifest['digest']!=digest({key:value for key,value in manifest.items() if key!='digest'}):
                fail('Original business document manifest changed')
            if set(manifest['files'])!=set(self.names) or sum(len(files) for files in manifest['files'].values())>MAX_FILES:fail('Original business document selection changed')
            total=0
            for name,files in manifest['files'].items():
                if 'SKILL.md' not in files:fail('Original main business skill is missing')
                for key,expected in files.items():
                    if relative(key)!=key:fail('Original document path changed')
                    data=text(self.root/name/key,self.root);total+=len(data.encode('utf-8'))
                    if digest_bytes(data.encode('utf-8'))!=expected:fail('Original business document bytes changed')
            if total>MAX_BYTES:fail('Original business documents exceed their bound')
            self.manifest=manifest
        except (OSError,ValueError,TypeError,KeyError,AttributeError,A2CError):fail('Restore the original business document snapshot and manifest')

    def read(self,args):
        if not isinstance(args,dict) or set(args)-{'skill','path'} or args.get('skill') not in self.names:fail('Only owner-selected business skills may be read')
        key=relative(args.get('path','SKILL.md'));self.verify()
        if key not in self.manifest['files'][args['skill']]:fail('Document is not in the original selected business skill')
        return {'skill':args['skill'],'path':key,'content':text(self.root/args['skill']/key,self.root),'digest':self.manifest['files'][args['skill']][key]}
