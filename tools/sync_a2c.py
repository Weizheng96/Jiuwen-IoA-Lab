"""Build the vendored A2C wheel from an exact clean Git commit snapshot.

    python tools/sync_a2c.py --src D:/project/AgentContractProtocol/src

Requires the build module and fixed build tools. No source checkout or network
Git access is performed; ordinary build dependencies may use the chosen mirror.
"""
from __future__ import annotations
import argparse
import email.parser
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
WHEELS=ROOT/'vendor/wheels'


def sh(args,cwd):
    return subprocess.run(args,cwd=cwd,check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()


def snapshot(src,target):
    """Use committed bytes, rejecting dirty source and unsafe Git file types."""
    src,target=Path(src).resolve(),Path(target)
    repo=Path(sh(['git','rev-parse','--show-toplevel'],src))
    relative=src.relative_to(repo).as_posix()
    if sh(['git','status','--porcelain','--untracked-files=all','--',relative],repo):
        raise ValueError('Commit the A2C source before synchronizing its wheel')
    commit=sh(['git','rev-parse','HEAD'],repo)
    version=sh(['git','show',commit+':设计/VERSION'],repo)
    entries=subprocess.run(['git','ls-tree','-rz',commit,'--',relative],cwd=repo,check=True,capture_output=True).stdout
    hashes={}
    for entry in entries.split(b'\0'):
        if not entry:continue
        description,name=entry.split(b'\t',1)
        mode,kind,oid=description.split();name=name.decode('utf-8')
        if mode not in (b'100644',b'100755') or kind!=b'blob':raise ValueError('Only regular committed source files are allowed')
        local=PurePosixPath(name).relative_to(PurePosixPath(relative))
        if not local.parts or any(part in ('','..') for part in local.parts):raise ValueError('Unsafe source member')
        blob=subprocess.run(['git','cat-file','blob',oid.decode()],cwd=repo,check=True,capture_output=True).stdout
        path=target/Path(*local.parts);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(blob)
        hashes[local.as_posix()]=hashlib.sha256(blob).hexdigest()
    if not {'pyproject.toml','VERSION'}<=set(hashes):raise ValueError('Not an A2C SDK source directory')
    return {'repo':repo,'relative':relative,'commit':commit,'design_version':version,'hashes':hashes}


def verify_wheel(wheel,source):
    expected=source['hashes']
    with zipfile.ZipFile(wheel) as archive:
        names=archive.namelist()
        modules={name for name in names if name.startswith('a2c/') and name.endswith('.py')}
        if modules!={name for name in expected if name.startswith('a2c/') and name.endswith('.py')}:
            raise ValueError('Built modules differ from the committed source')
        for name in names:
            if name.startswith('a2c/') and not name.endswith('/'):
                if name not in expected or hashlib.sha256(archive.read(name)).hexdigest()!=expected[name]:
                    raise ValueError('Built package member differs from its committed bytes: '+name)
        metadata=[name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata)!=1:raise ValueError('Expected one wheel metadata file')
        package=email.parser.Parser().parsestr(archive.read(metadata[0]).decode('utf-8'))
        if package['Name']!='a2c':raise ValueError('Not the A2C package')
        if 'v'+package['Version']!=source['design_version']:raise ValueError('SDK version differs from its committed design')
    return package['Version']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--src',required=True)
    args=parser.parse_args();src=Path(args.src).resolve()
    req=ROOT/'requirements.txt';old=req.read_text(encoding='utf-8')
    if len(re.findall(r'^\./vendor/wheels/a2c-[^\s]+\.whl$',old,re.M))!=1:
        raise ValueError('requirements.txt must name exactly one local A2C wheel')
    with tempfile.TemporaryDirectory(prefix='ioalab-a2c-') as td:
        source_dir=Path(td)/'source';out=Path(td)/'wheel'
        source=snapshot(src,source_dir)
        subprocess.run([sys.executable,'-m','build','--wheel','--outdir',str(out)],cwd=source_dir,
            env={**os.environ,'SOURCE_DATE_EPOCH':'315532800','PYTHONUTF8':'1'},check=True)
        wheels=list(out.glob('a2c-*.whl'))
        if len(wheels)!=1:raise ValueError('Expected exactly one A2C wheel')
        built=wheels[0];version=verify_wheel(built,source)
        if (sh(['git','rev-parse','HEAD'],source['repo'])!=source['commit'] or
            sh(['git','status','--porcelain','--untracked-files=all','--',source['relative']],source['repo'])):
            raise ValueError('A2C source changed during the build; vendor files were not replaced')
        WHEELS.mkdir(parents=True,exist_ok=True)
        dst=WHEELS/built.name;shutil.copyfile(built,dst)
        info={'package':'a2c','version':version,'wheel':dst.name,'sha256':hashlib.sha256(dst.read_bytes()).hexdigest(),
            'source':{'repo':'https://github.com/Weizheng96/AgentContractProtocol','commit':source['commit'],
                      'subdirectory':source['relative'],'design_version':source['design_version']},
            'built':time.strftime('%Y-%m-%d'),'tool':'tools/sync_a2c.py'}
        (WHEELS/'SOURCE.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        new,count=re.subn(r'^\./vendor/wheels/a2c-[^\s]+\.whl$',f'./vendor/wheels/{dst.name}',old,flags=re.M)
        assert count==1
        req.write_text(new,encoding='utf-8',newline='\n')
        # The SDK, core and product Skill all consume this exact local dependency.
        for name in ('src/platform/pyproject.toml','src/access/sdk/pyproject.toml','src/access/skills/_shared/requirements.lock'):
            path=ROOT/name;before=path.read_text(encoding='utf-8')
            after,count=re.subn(r'a2c==[0-9]+\.[0-9]+\.[0-9]+',f'a2c=={version}',before)
            if count!=1:raise ValueError('Expected exactly one A2C version pin: '+name)
            path.write_text(after,encoding='utf-8',newline='\n')
        manifest_path=ROOT/'vendor/SOURCES.json';manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        components=[item for item in manifest['components'] if item['id']=='a2c']
        if len(components)!=1:raise ValueError('Expected one vendored A2C component')
        components[0].update(commit=source['commit'],subdirectory=source['relative'],wheel='wheels/'+dst.name)
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        checkouts=Path(td)/'checkouts.json';checkouts.write_text(json.dumps({'a2c':str(source['repo'])}),encoding='utf-8')
        subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'tools/vendor_dependencies.py'),
            '--checkouts',str(checkouts),'--work',str(Path(td)/'vendor-stage'),'--only','a2c'],cwd=ROOT,check=True)
        index=ROOT/'设计/README.md';before=index.read_text(encoding='utf-8')
        after,count=re.subn(r'(依据的 A2C 设计版本：)v[0-9]+\.[0-9]+\.[0-9]+',r'\g<1>'+source['design_version'],before)
        if count!=1:raise ValueError('Expected one upstream design reference')
        index.write_text(after,encoding='utf-8',newline='\n')
        for previous in WHEELS.glob('a2c-*.whl'):
            if previous!=dst:previous.unlink()
    print(json.dumps({'wheel':str(dst.relative_to(ROOT)),'version':version,'sourceCommit':source['commit'],
        'sha256':info['sha256'],'exactCommittedBytes':True},ensure_ascii=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
