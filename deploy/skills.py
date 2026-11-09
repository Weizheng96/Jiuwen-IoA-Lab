"""Build both resident product skills with one compatible, GitHub-free runtime manifest."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import zipfile
from packaging.requirements import Requirement

from .wheels import build_wheel


ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output,*,root=ROOT):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    root=Path(root).resolve()
    projects=[root/'src/access/sdk',root/'src/hosts/jiuwen',root/'src/official']
    with tempfile.TemporaryDirectory(prefix='ioa-access-skills-') as temporary:
        temporary=Path(temporary);shared=temporary/'shared';wheels=shared/'wheels';wheels.mkdir(parents=True)
        for project in projects:build_wheel(project,wheels)
        sources=json.loads((root/'vendor/SOURCES.json').read_text(encoding='utf-8'))
        versions={tomllib.loads((project/'pyproject.toml').read_text(encoding='utf-8'))['project']['name']:
                  (project/'VERSION').read_text().strip().removeprefix('v') for project in projects}
        for name in ('a2c','jiuwenswarm-process-sdk'):
            component=next(value for value in sources['components'] if value['id']==name)
            info=component['wheelInfo'];original=root/'vendor'/info['file']
            if sha(original)!=info['sha256']:raise ValueError('Vendored dependency checksum mismatch: '+name)
            shutil.copy2(original,wheels/original.name);versions[info['package']]=info['version']
        lock=(root/'src/access/skills/_shared/requirements.lock').read_text(encoding='utf-8')
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
                  'sourceManifestSha256':sha(root/'vendor/SOURCES.json')}
        (shared/'runtime.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        products=[]
        for folder,name,role in [('assistant','ioalab-delegate','assistant'),('provider','ioalab-provider','service')]:
            skill=temporary/name
            shutil.copytree(root/'src/access/skills'/folder,skill,ignore=shutil.ignore_patterns('settings.local.json','__pycache__','*.pyc','assets','scripts'))
            for page in (root/'src/access/skills/_shared/references').glob('*.md'):
                target=skill/'references'/page.name
                if target.exists():raise ValueError('Shared reference has more than one source: '+page.name)
                if page.name=='location.md':
                    content=page.read_bytes();marker=b'__IOALAB_SKILL_NAME__'
                    if content.count(marker)!=1:raise ValueError('One skill name is required in the shared location page')
                    target.write_bytes(content.replace(marker,name.encode('ascii')))
                else:shutil.copy2(page,target)
            shutil.copytree(shared,skill/'assets')
            (skill/'assets/skill.json').write_text(json.dumps({'role':role}),encoding='utf-8')
            (skill/'scripts').mkdir()
            for entry in ('ioalab.py','installer.py'):
                shutil.copy2(root/'src/access/skills/_shared'/entry,skill/'scripts'/entry)
            destination=output/('ioalab-'+folder+'-skill.zip')
            with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(skill.rglob('*')):
                    if path.is_file():archive.write(path,Path(name)/path.relative_to(skill))
            products.append(destination)
        return products
