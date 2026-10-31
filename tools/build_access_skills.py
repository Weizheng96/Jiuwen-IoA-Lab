"""Build both resident product skills with one compatible, GitHub-free runtime manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import zipfile
from packaging.requirements import Requirement

from build_connector import constraints
from build_packages import build_wheel


ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    projects=[ROOT/'src/access/sdk',ROOT/'src/hosts/jiuwen']
    with tempfile.TemporaryDirectory(prefix='ioa-access-skills-') as temporary:
        temporary=Path(temporary);shared=temporary/'shared';wheels=shared/'wheels';wheels.mkdir(parents=True)
        for project in projects:build_wheel(project,wheels)
        sources=json.loads((ROOT/'vendor/SOURCES.json').read_text(encoding='utf-8'))
        versions={'ioalab-sdk':(projects[0]/'VERSION').read_text().strip().removeprefix('v'),
                  'ioalab-jiuwen':(projects[1]/'VERSION').read_text().strip().removeprefix('v')}
        for name in ('a2c','jiuwenswarm-process-sdk'):
            component=next(value for value in sources['components'] if value['id']==name)
            info=component['wheelInfo'];original=ROOT/'vendor'/info['file']
            if sha(original)!=info['sha256']:raise ValueError('Vendored dependency checksum mismatch: '+name)
            shutil.copy2(original,wheels/original.name);versions[info['package']]=info['version']
        lock=(ROOT/'src/access/skills/_shared/requirements.lock').read_text(encoding='utf-8')
        pinned={line.split('==')[0].lower().replace('_','-'):line.split('==')[1] for line in lock.splitlines() if line and not line.startswith('#')}
        if any(pinned.get(name)!=version for name,version in versions.items()):raise ValueError('Update the shared access lock from the verified development environment')
        for project in projects:
            for raw in tomllib.loads((project/'pyproject.toml').read_text(encoding='utf-8'))['project']['dependencies']:
                required=Requirement(raw)
                if required.marker and not required.marker.evaluate():continue
                version=pinned.get(required.name.lower().replace('_','-'))
                if version is None or version not in required.specifier:raise ValueError('Shared access lock does not cover dependency: '+required.name)
        (shared/'requirements.lock').write_bytes(lock.encode('utf-8'))
        manifest={'format':1,'versions':versions,'constraintsSha256':hashlib.sha256(lock.encode()).hexdigest(),
                  'wheels':[{'file':path.name,'sha256':sha(path)} for path in sorted(wheels.glob('*.whl'))],
                  'sourceManifestSha256':sha(ROOT/'vendor/SOURCES.json')}
        (shared/'runtime.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        products=[]
        for folder,name,role in [('assistant','ioalab-delegate','assistant'),('provider','ioalab-provider','service')]:
            skill=temporary/name
            shutil.copytree(ROOT/'src/access/skills'/folder,skill,ignore=shutil.ignore_patterns('settings.local.json','__pycache__','*.pyc','assets','scripts'))
            shutil.copytree(shared,skill/'assets')
            (skill/'assets/skill.json').write_text(json.dumps({'role':role}),encoding='utf-8')
            (skill/'scripts').mkdir()
            shutil.copy2(ROOT/'src/access/skills/_shared/ioalab.py',skill/'scripts/ioalab.py')
            destination=output/('ioalab-'+folder+'-skill.zip')
            with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(skill.rglob('*')):
                    if path.is_file():archive.write(path,Path(name)/path.relative_to(skill))
            products.append(destination)
        return products


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=ROOT/'dist')
    parser.add_argument('--update-lock',action='store_true',help='refresh the shared release lock from a verified development environment')
    args=parser.parse_args()
    if args.update_lock:(ROOT/'src/access/skills/_shared/requirements.lock').write_text(constraints([ROOT/'src/access/sdk',ROOT/'src/hosts/jiuwen']),encoding='utf-8')
    for path in build(args.out):print(path)
