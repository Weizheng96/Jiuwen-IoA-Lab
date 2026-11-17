"""P41 candidate: original business snapshot, not a professional-definition registry."""
from dataclasses import asdict
from pathlib import Path, PureWindowsPath

from a2c.codec import digest, digest_bytes
from .business_skills import FrozenSkills, text, linked, MAX_FILES, MAX_BYTES
from .files import private_json, private_text, read_json
from .host_submission import HostDefinition, HostResource
from .protocol import RuntimeFault


def freeze_definition(workspace, *, reference, version, instructions, skill_names=(),
                      sources=None, binding, resume=False, expected_digest=None):
    """Freeze trusted profile/Rules input; resume verifies caller's saved digest.

    sources is an explicit, already selected name→directory map. No discovery,
    native capability probe, configuration rewrite or latest-source fallback.
    """
    workspace = Path(workspace)
    if not workspace.is_absolute() or workspace.is_symlink():
        raise RuntimeFault('invalid_input', 'Use the original SDK-owned absolute workspace')
    names = tuple(skill_names)
    if (not all(isinstance(value,str) and value for value in (reference,version)) or
        not isinstance(instructions,str) or not instructions.strip() and not names):
        raise RuntimeFault('invalid_input', 'A professional reference, version and instructions are required')
    path = workspace/'execution-definition.json'
    expected = {'reference':reference,'version':version,'instructions':instructions,
                'skills':list(names),'binding':binding}
    if resume:
        if not isinstance(expected_digest,str) or not expected_digest:
            raise RuntimeFault('identity_conflict', 'Restore the definition digest from the original business snapshot')
        try:
            if linked(path):raise RuntimeFault('identity_conflict', 'Original definition descriptor cannot be a link')
            saved = read_json(path)
        except (OSError, ValueError):
            raise RuntimeFault('identity_conflict', 'Original professional definition snapshot is unavailable') from None
        if saved.get('selection') != expected:
            raise RuntimeFault('identity_conflict', 'Resume the original professional definition version')
        if saved.get('digest')!=expected_digest or digest(saved.get('payload'))!=expected_digest:
            raise RuntimeFault('identity_conflict', 'Original professional definition descriptor changed')
    else:
        if path.exists():
            raise RuntimeFault('identity_conflict', 'Existing definition requires its original saved digest')
        workspace.mkdir(parents=True, exist_ok=True)
    frozen = None
    if any(not isinstance(name,str) or not name for name in names) or len(set(names))!=len(names) or len(names)>32:
        raise RuntimeFault('invalid_input', 'Use distinct selected professional references')
    if resume:
        directory_names=tuple(saved['payload']['skill_names'])
        document_names=tuple(saved['payload']['document_names'])
        if set(directory_names+document_names)!=set(names):
            raise RuntimeFault('identity_conflict', 'Original professional resource selection changed')
    else:
        if any(name not in (sources or {}) for name in names):
            raise RuntimeFault('not_ready', 'A selected professional reference has no trusted source mapping', 'resolve_definition_resource')
        directory_names=tuple(name for name in names if Path(sources[name]).is_dir())
        document_names=tuple(name for name in names if name not in directory_names)
        if any(not Path(sources[name]).is_file() for name in document_names):
            raise RuntimeFault('not_ready', 'A selected professional document is unavailable', 'resolve_definition_resource')
    if directory_names:
        frozen = FrozenSkills(workspace,directory_names,sources or {},binding,resume=resume)
    resources = []
    paths = {}
    if frozen:
        scope = digest(binding)
        for name, files in frozen.manifest['files'].items():
            for relative, checksum in files.items():
                resource_id = digest({'definitionRef':reference,'version':version,'scope':scope,
                                      'skill':name,'path':relative,'contentDigest':checksum})
                resources.append(HostResource(resource_id,name+'/'+relative,'text/markdown',checksum,scope,
                                               'sdk-definition:'+resource_id))
                paths[resource_id]=(Path('skill-documents')/name/relative).as_posix()
    for name in document_names:
        target=workspace/'definition-documents'/(digest({'name':name}).removeprefix('sha256:')+'.md')
        if not resume:
            source=Path(sources[name])
            if not source.is_absolute() or source.suffix.lower()!='.md':
                raise RuntimeFault('invalid_input', 'Select an absolute professional Markdown document')
            private_text(target,text(source,source.parent))
        content=text(target,workspace)
        checksum=digest_bytes(content.encode('utf-8'))
        scope=digest(binding)
        resource_id=digest({'definitionRef':reference,'version':version,'scope':scope,
                            'document':name,'contentDigest':checksum})
        resources.append(HostResource(resource_id,name,'text/markdown',checksum,scope,'sdk-definition:'+resource_id))
        paths[resource_id]=target.relative_to(workspace).as_posix()
    # Preserve the existing aggregate FrozenSkills budget across both source forms.
    snapshot_paths=[frozen.root/name/relative for name,files in frozen.manifest['files'].items() for relative in files] if frozen else []
    snapshot_paths.extend(workspace/'definition-documents'/(digest({'name':name}).removeprefix('sha256:')+'.md') for name in document_names)
    if len(snapshot_paths)>MAX_FILES or sum(item.stat().st_size for item in snapshot_paths)>MAX_BYTES:
        raise RuntimeFault('invalid_input', 'Professional snapshot exceeds the existing document budget')
    payload = {'selection':expected,'documents':frozen.manifest['digest'] if frozen else None,
               'skill_names':list(directory_names),'document_names':list(document_names),
               'paths':paths,
               'resources':[asdict(item) for item in resources]}
    checksum = digest(payload)
    if resume:
        if saved.get('payload') != payload or saved.get('digest') != checksum or checksum != expected_digest:
            raise RuntimeFault('identity_conflict', 'Original professional snapshot integrity changed')
    else:private_json(path,{'selection':expected,'payload':payload,'digest':checksum})
    return HostDefinition(reference,version,checksum,instructions,tuple(resources))


def read_definition_resource(definition, resource, *, binding, workspace):
    """Scoped SDK reader candidate, not a model-supplied path signer."""
    if not isinstance(resource,HostResource) or resource not in definition.resources or resource.access_scope != digest(binding):
        raise RuntimeFault('forbidden', 'Resource is outside the original professional snapshot')
    workspace=Path(workspace)
    descriptor=workspace/'execution-definition.json'
    if not workspace.is_absolute() or linked(workspace) or linked(descriptor):
        raise RuntimeFault('forbidden', 'Use the original unlinked SDK definition cache')
    saved=read_json(descriptor)
    payload=saved.get('payload')
    if (saved.get('digest')!=definition.digest or digest(payload)!=definition.digest or
        payload.get('selection',{}).get('binding')!=binding or
        payload.get('resources')!=[asdict(item) for item in definition.resources] or
        resource.locator!='sdk-definition:'+resource.resource_id):
        raise RuntimeFault('identity_conflict', 'Original professional resource mapping changed')
    relative=payload.get('paths',{}).get(resource.resource_id)
    if (not isinstance(relative,str) or Path(relative).is_absolute() or PureWindowsPath(relative).drive or
        '..' in Path(relative).parts):
        raise RuntimeFault('forbidden', 'Original professional resource has no confined snapshot mapping')
    data=text(workspace/relative,workspace).encode('utf-8')
    if digest_bytes(data)!=resource.digest:
        raise RuntimeFault('identity_conflict', 'Original professional document bytes changed')
    return data
