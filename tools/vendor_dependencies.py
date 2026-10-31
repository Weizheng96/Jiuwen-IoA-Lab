"""Maintainer-only: archive pinned local checkouts and build portable deployment wheels.

Run on a connected development machine. Deployment never invokes this script.
Checkout paths are supplied in a local JSON map; vendor/SOURCES.json owns the pins.
"""
import argparse
import email.parser
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'vendor'
MANIFEST=VENDOR/'SOURCES.json'


def digest(blob):return hashlib.sha256(blob).hexdigest()
def run(argv,**kwargs):return subprocess.run([str(a) for a in argv],check=True,**kwargs)
def git(repo,*args):return subprocess.check_output(['git','-c','safe.directory='+repo.as_posix(),'-C',str(repo),*args],env={**os.environ,'GIT_LFS_SKIP_SMUDGE':'1'})
def save(data):MANIFEST.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


def unpack(raw,folder):
    folder.mkdir(parents=True,exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            target=(folder/member.name).resolve()
            if not target.is_relative_to(folder.resolve()) or member.issym() or member.islnk():raise ValueError('Unsafe upstream archive member: '+member.name)
        archive.extractall(folder,filter='data')


def wheel_info(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist();meta=next(n for n in names if n.endswith('.dist-info/METADATA'))
        info=email.parser.Parser().parsestr(archive.read(meta).decode())
        return {'file':path.relative_to(VENDOR).as_posix(),'sha256':digest(path.read_bytes()),'package':info['Name'],'version':info['Version']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkouts',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--only',nargs='*')
    args=parser.parse_args()
    roots=json.loads(args.checkouts.read_text('utf-8'));data=json.loads(MANIFEST.read_text('utf-8'))
    args.work.mkdir(parents=True,exist_ok=True)
    for item in data['components']:
        name=item['id']
        if args.only and name not in args.only:continue
        if item.get('sourceComponent'):
            item['wheelInfo']=wheel_info(VENDOR/item['wheel']);save(data);continue
        repo=Path(roots[name]).resolve();commit=item['commit']
        if git(repo,'rev-parse',commit).decode().strip()!=commit:raise ValueError('Full pinned commit required')
        rootnames=git(repo,'ls-tree','--name-only',commit).decode().splitlines()
        license_names=[n for n in rootnames if n.upper().startswith(('LICENSE','COPYING','NOTICE','OPEN_SOURCE_SOFTWARE_NOTICE'))]
        paths=[] if item['subdirectory']=='.' else [item['subdirectory'],*license_names]
        raw=git(repo,'archive','--format=tar',commit,*paths)
        stage=args.work/name
        unpack(raw,stage)
        licenses=VENDOR/'licenses'/name;licenses.mkdir(parents=True,exist_ok=True)
        license_files=[]
        for file in stage.rglob('*'):
            if file.is_file() and file.suffix.lower() not in ('.py','.pyc') and file.name.upper().startswith(('LICENSE','COPYING','NOTICE','OPEN_SOURCE_SOFTWARE_NOTICE')):
                relative=file.relative_to(stage);dest=licenses/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,dest)
                license_files.append(dest.relative_to(VENDOR).as_posix())
        if not license_files:
            note=licenses/'NOTICE.md'
            note.write_text('# Source attribution\n\nUpstream: '+item['repository']+'\n\nCommit: '+commit+'\n\nDeclared license: '+item['license']+'. This snapshot has no standalone license text; retain the original package/source declaration.\n',encoding='utf-8',newline='\n')
            license_files.append(note.relative_to(VENDOR).as_posix())
        item['licenseFiles']={n:digest((VENDOR/n).read_bytes()) for n in license_files}
        if item['delivery']=='directory':
            item['sourceDirectory']='A2X-registry'
            item['sourceFiles']={p.relative_to(stage).as_posix():digest(p.read_bytes()) for p in sorted(stage.rglob('*')) if p.is_file()}
            item['excludedOptionalSubmodules']=['database: upstream benchmark datasets; IoA platform generates its own data']
        else:
            archive=VENDOR/'sources'/(name+'-'+commit[:12]+'.tar.gz');archive.parent.mkdir(exist_ok=True)
            archive.write_bytes(gzip.compress(raw,mtime=0))
            item['archive']=archive.relative_to(VENDOR).as_posix();item['archiveSha256']=digest(archive.read_bytes());item['sourceTarSha256']=digest(raw)
        if item.get('frontend'):
            expected=json.loads((ROOT/'personal-assistant/swarm.lock.json').read_text('utf-8'))['sourceArchiveSha256']
            if digest(raw)!=expected:raise ValueError('Pinned Jiuwen source archive changed')
            frontend=stage/'jiuwenswarm/channels/web/frontend'
            npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
            if not npm:raise RuntimeError('Maintainer needs Node/npm to prepare the bundled web UI')
            run([npm,'ci','--no-audit','--no-fund'],cwd=frontend)
            run([npm,'run','build'],cwd=frontend)
            if not (frontend/'dist/index.html').is_file():raise ValueError('Original Jiuwen frontend is missing')
            item['frontendBuild']={'packageLockSha256':digest((frontend/'package-lock.json').read_bytes()),'commands':['npm ci --no-audit --no-fund','npm run build'],'node':subprocess.check_output(['node','--version'],text=True).strip(),'assetFiles':len([p for p in (frontend/'dist').rglob('*') if p.is_file()])}
        if item['delivery']=='build-wheel':
            output=args.work/(name+'-wheels');output.mkdir()
            epoch=git(repo,'show','-s','--format=%ct',commit).decode().strip()
            run([sys.executable,'-m','pip','wheel','--no-deps','--wheel-dir',output,stage/item['subdirectory']],env={**os.environ,'SOURCE_DATE_EPOCH':epoch})
            wheels=list(output.glob('*.whl'))
            if len(wheels)!=1 or not wheels[0].name.endswith('-none-any.whl'):raise ValueError('Expected one portable Python wheel')
            dest=VENDOR/'wheels'/wheels[0].name;shutil.copyfile(wheels[0],dest);item['wheel']=dest.relative_to(VENDOR).as_posix()
        if item.get('wheel'):item['wheelInfo']=wheel_info(VENDOR/item['wheel'])
        save(data);print('Prepared:',name,flush=True)
    if not args.only:
        lock=ROOT/'personal-assistant/requirements-swarm.lock';lines=lock.read_text('utf-8').splitlines()
        wheels={i['wheelInfo']['package'].lower().replace('_','-'):i['wheel'] for i in data['components'] if 'wheelInfo' in i}
        resolved=[]
        for line in lines:
            if ' @ git+' in line:
                name=line.split(' @ ',1)[0].strip().lower().replace('_','-')
                if name not in wheels:raise ValueError('Unbundled VCS dependency: '+name)
                line='./vendor/'+wheels[name]
            resolved.append(line)
        lock.write_text('\n'.join(resolved)+'\n',encoding='utf-8',newline='\n')
        spec=ROOT/'personal-assistant/swarm.lock.json';swarm=json.loads(spec.read_text('utf-8'))
        swarm['requirementsSha256']=digest(lock.read_bytes());swarm['frontend']='Original prebuilt web UI included in the local workswarm wheel; deployment requires no Node/npm'
        swarm['manifest']='vendor/SOURCES.json';spec.write_text(json.dumps(swarm,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Pinned sources and prepared wheels are local; review vendor/SOURCES.json before committing.')


if __name__=='__main__':main()
