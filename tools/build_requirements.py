"""Resolve runtime dependency constraints from the projects being built."""
import importlib.metadata as md
from pathlib import Path
import tomllib
from packaging.requirements import Requirement

ROOT=Path(__file__).resolve().parents[1]

def constraints(projects=None):
    local={}
    for path in projects or [ROOT/'src/client/sdk']:
        project=tomllib.loads((path/'pyproject.toml').read_text(encoding='utf-8'))['project']
        local[project['name'].lower().replace('_','-')]=(project,(path/'VERSION').read_text().strip().removeprefix('v'))
    pending=[(name,set()) for name in local]
    seen={}; versions={}
    while pending:
        name,extras=pending.pop()
        key=name.lower().replace('_','-')
        if key in seen and extras<=seen[key]: continue
        seen.setdefault(key,set()).update(extras)
        # Editable-install metadata may still describe the previous SDK release.
        # Lock the package we just built, not its previously installed version.
        if key in local:
            project,version=local[key];versions[project['name']]=version
            requirements=project['dependencies']
        else:
            dist=md.distribution(name);versions[dist.metadata['Name']]=dist.version
            requirements=dist.requires or []
        for raw in requirements:
            req=Requirement(raw)
            if req.marker and not any(req.marker.evaluate({'extra':e}) for e in extras|{''}): continue
            pending.append((req.name,set(req.extras)))
    return '\n'.join(f'{n}=={v}' for n,v in sorted(versions.items()))+'\n'
