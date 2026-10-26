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
import zipfile
from packaging.requirements import Requirement

ROOT=Path(__file__).resolve().parents[1]


def constraints():
    pending=[('ioalab-sdk',set())]
    seen={}; versions={}
    while pending:
        name,extras=pending.pop()
        key=name.lower().replace('_','-')
        if key in seen and extras<=seen[key]: continue
        seen.setdefault(key,set()).update(extras)
        dist=md.distribution(name);versions[dist.metadata['Name']]=dist.version
        for raw in dist.requires or []:
            req=Requirement(raw)
            if req.marker and not any(req.marker.evaluate({'extra':e}) for e in extras|{''}): continue
            pending.append((req.name,set(req.extras)))
    return '\n'.join(f'{n}=={v}' for n,v in sorted(versions.items()))+'\n'


def build(output):
    with tempfile.TemporaryDirectory(prefix='ioalab-connector-') as temp:
        temp=Path(temp);bundle=temp/'ioalab-client'
        shutil.copytree(ROOT/'connector/ioalab-client',bundle,ignore=shutil.ignore_patterns('__pycache__','*.pyc','assets'))
        assets=bundle/'skills/ioalab-delegate/assets';wheels=assets/'wheels';wheels.mkdir(parents=True)
        subprocess.run([sys.executable,'-m','pip','wheel','--no-index','--no-deps','--no-build-isolation',str(ROOT/'sdk'),'-w',str(wheels)],check=True)
        source=json.loads((ROOT/'vendor/wheels/SOURCE.json').read_text(encoding='utf-8'))
        original=ROOT/'vendor/wheels'/source['wheel']
        if hashlib.sha256(original.read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('A2C source wheel digest mismatch')
        shutil.copy2(original,wheels/original.name)
        sdk_version=(ROOT/'sdk/VERSION').read_text().strip().removeprefix('v')
        lock=constraints()
        manifest={'sdkVersion':sdk_version,'constraintsSha256':hashlib.sha256(lock.encode()).hexdigest(),
                  'wheels':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(wheels.glob('*.whl'))]}
        (assets/'runtime.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        (assets/'requirements.lock').write_bytes(lock.encode())
        output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in bundle.rglob('*'):
                if path.is_file(): archive.write(path,path.relative_to(temp))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'dist/ioalab-client.zip'))
    print(build(parser.parse_args().output))
