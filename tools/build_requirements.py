"""Resolve runtime dependency constraints from the projects being built."""
import importlib.metadata as md
from pathlib import Path
import tomllib
from packaging.requirements import Requirement
from packaging.markers import default_environment

ROOT=Path(__file__).resolve().parents[1]

def constraints(projects=None):
    local={}
    for path in projects or [ROOT/'src/client/sdk']:
        project=tomllib.loads((path/'pyproject.toml').read_text(encoding='utf-8'))['project']
        local[project['name'].lower().replace('_','-')]=(project,(path/'VERSION').read_text().strip().removeprefix('v'))
    # Credential backends differ across the three supported host systems.
    # Resolve their metadata even when maintaining the bundle on another OS.
    environments=[{**default_environment(),'sys_platform':system,'platform_system':label,'os_name':os_name}
                  for system,label,os_name in [('darwin','Darwin','posix'),('linux','Linux','posix'),('win32','Windows','nt')]]
    pending=[(name,set(),False) for name in local]
    seen={}; versions={}
    while pending:
        name,extras,credential_backend=pending.pop()
        key=name.lower().replace('_','-')
        credential_backend=credential_backend or key=='keyring'
        visit=(key,credential_backend)
        if visit in seen and extras<=seen[visit]: continue
        seen.setdefault(visit,set()).update(extras)
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
            if req.marker and not any(req.marker.evaluate({**environment,'extra':e})
                                     for environment in (environments if credential_backend else [default_environment()]) for e in extras|{''}): continue
            pending.append((req.name,set(req.extras),credential_backend))
    return '\n'.join(f'{n}=={v}' for n,v in sorted(versions.items()))+'\n'
