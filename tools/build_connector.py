"""Build a self-contained skill bundle with SDK wheels, checksums and dependency constraints."""
import argparse
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from packaging.requirements import Requirement

ROOT=Path(__file__).resolve().parents[1]


def constraints():
    sdk=tomllib.loads((ROOT/'src/access/sdk/pyproject.toml').read_text(encoding='utf-8'))['project']
    pending=[('ioalab-sdk',set())]
    seen={}; versions={}
    while pending:
        name,extras=pending.pop()
        key=name.lower().replace('_','-')
        if key in seen and extras<=seen[key]: continue
        seen.setdefault(key,set()).update(extras)
        # Editable-install metadata may still describe the previous SDK release.
        # Lock the package we just built, not its previously installed version.
        if key=='ioalab-sdk':
            versions[sdk['name']]=(ROOT/'src/access/sdk/VERSION').read_text().strip().removeprefix('v')
            requirements=sdk['dependencies']
        else:
            dist=md.distribution(name);versions[dist.metadata['Name']]=dist.version
            requirements=dist.requires or []
        for raw in requirements:
            req=Requirement(raw)
            if req.marker and not any(req.marker.evaluate({'extra':e}) for e in extras|{''}): continue
            pending.append((req.name,set(req.extras)))
    return '\n'.join(f'{n}=={v}' for n,v in sorted(versions.items()))+'\n'


def build(output):
    with tempfile.TemporaryDirectory(prefix='ioalab-connector-') as temp:
        temp=Path(temp);bundle=temp/'ioalab-client'
        shutil.copytree(ROOT/'connector/ioalab-client',bundle,ignore=shutil.ignore_patterns('__pycache__','*.pyc','assets','settings.local.json'))
        assets=bundle/'skills/ioalab-delegate/assets';wheels=assets/'wheels';wheels.mkdir(parents=True)
        subprocess.run([sys.executable,str(ROOT/'tools/build_packages.py'),'--out',str(wheels),str(ROOT/'src/access/sdk')],check=True)
        source=json.loads((ROOT/'vendor/wheels/SOURCE.json').read_text(encoding='utf-8'))
        original=ROOT/'vendor/wheels'/source['wheel']
        if hashlib.sha256(original.read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('A2C source wheel digest mismatch')
        shutil.copy2(original,wheels/original.name)
        sdk_version=(ROOT/'src/access/sdk/VERSION').read_text().strip().removeprefix('v')
        lock=constraints()
        manifest={'sdkVersion':sdk_version,'constraintsSha256':hashlib.sha256(lock.encode()).hexdigest(),
                  'wheels':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(wheels.glob('*.whl'))]}
        (assets/'runtime.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        (assets/'requirements.lock').write_bytes(lock.encode())
        output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in bundle.rglob('*'):
                if path.is_file(): archive.write(path,path.relative_to(temp))
        # Directly importable via Jiuwen's public skills.import_local / Skills UI.
        standalone=output.with_name('ioalab-skill.zip')
        skill=bundle/'skills/ioalab-delegate'
        with zipfile.ZipFile(standalone,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in skill.rglob('*'):
                if path.is_file(): archive.write(path,Path('ioalab-delegate')/path.relative_to(skill))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'dist/ioalab-client.zip'))
    print(build(parser.parse_args().output))
