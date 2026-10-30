"""Verify local deployment inputs without Git, npm or network access."""
import argparse
import email.parser
import gzip
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def validate(root=ROOT):
    root=Path(root).resolve();vendor=root/'vendor'
    def local(name):
        path=(vendor/name).resolve()
        if not path.is_relative_to(vendor):raise ValueError('Vendor path escapes its directory')
        return path
    def checked(name,digest):
        path=local(name)
        if not path.is_file() or sha(path)!=digest:raise ValueError('Missing or changed vendored file: '+name)
        return path
    data=json.loads((vendor/'SOURCES.json').read_text('utf-8'));components={i['id']:i for i in data['components']}
    if len(components)!=len(data['components']):raise ValueError('Duplicate vendor component')
    if (root/'.gitmodules').exists() or (vendor/'A2X-registry/.git').exists():raise ValueError('Deployment must not require Git submodules')
    for item in components.values():
        if not re.fullmatch('[a-f0-9]{40}',item['commit']):raise ValueError('Full upstream commit is required')
        origin=components.get(item.get('sourceComponent'),item)
        if not origin.get('licenseFiles'):raise ValueError('Missing upstream license attribution')
        for file,digest in origin['licenseFiles'].items():checked(file,digest)
        if item.get('archive'):
            path=checked(item['archive'],item['archiveSha256'])
            with gzip.open(path,'rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=item['sourceTarSha256']:raise ValueError('Upstream tar digest mismatch')
        for file,digest in item.get('sourceFiles',{}).items():checked(item['sourceDirectory']+'/'+file,digest)
        if item.get('wheelInfo'):
            info=item['wheelInfo'];path=checked(info['file'],info['sha256'])
            with zipfile.ZipFile(path) as archive:
                names=archive.namelist()
                if any(n.startswith('/') or '..' in Path(n).parts for n in names):raise ValueError('Unsafe wheel member')
                for member in archive.infolist():
                    if member.file_size<=256 and archive.read(member).startswith(b'version https://git-lfs.github.com/spec/v1'):
                        raise ValueError('Unmaterialized Git LFS pointer in local wheel')
                meta=next(n for n in names if n.endswith('.dist-info/METADATA'))
                metadata=email.parser.Parser().parsestr(archive.read(meta).decode())
                if metadata['Name']!=info['package'] or metadata['Version']!=info['version']:raise ValueError('Wheel metadata mismatch')
                if item.get('frontend'):
                    prefix='jiuwenswarm/channels/web/frontend/dist/'
                    if prefix+'index.html' not in names:raise ValueError('Jiuwen web UI missing from local wheel')
                    if sum(n.startswith(prefix) and not n.endswith('/') for n in names)<item['frontendBuild']['assetFiles']:raise ValueError('Jiuwen web assets are incomplete')
    lock=root/'personal-assistant/requirements-swarm.lock';spec=json.loads((root/'personal-assistant/swarm.lock.json').read_text('utf-8'))
    if sha(lock)!=spec['requirementsSha256']:raise ValueError('Swarm dependency lock digest mismatch')
    if 'workswarm' in components:
        runtime=components['workswarm']
        if spec['commit']!=runtime['commit'] or spec['sourceArchiveSha256']!=runtime['sourceTarSha256']:raise ValueError('Jiuwen source pin differs between manifests')
    if 'a2c' in components:
        source=json.loads((vendor/'wheels/SOURCE.json').read_text('utf-8'));sdk=components['a2c']
        if source['source']['commit']!=sdk['commit'] or 'wheels/'+source['wheel']!=sdk['wheelInfo']['file'] or source['sha256']!=sdk['wheelInfo']['sha256']:raise ValueError('A2C source/wheel differs between manifests')

    expected={i['wheel'] for i in components.values() if i['delivery']=='build-wheel'};seen=set()
    for line in lock.read_text('utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        if any(token in line.lower() for token in ('git+','github.com','gitcode.com',' @ http',' @ file')):raise ValueError('Remote/VCS dependency in deployment lock')
        if line.startswith('./vendor/'):
            file=line.removeprefix('./vendor/');seen.add(file)
            if file not in expected:raise ValueError('Unlisted local runtime wheel')
        elif not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?:\s*;.+)?',line):raise ValueError('Runtime lock allows only pinned PyPI packages or listed local wheels')
    if expected!=seen:raise ValueError('Runtime lock must include every prepared Git dependency wheel')
    return data


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);args=p.parse_args()
    data=validate(args.root);print('Local source, license, wheel, web UI and dependency lock checks passed:',len(data['components']),'components')
