"""Export two lightweight native skills bound to the same client interpreter."""
import io
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from .layout import binding, configuration_path


def skill_manifest(files, bound, source_binding=None):
    return {'format': 'ioalab.skill/2', 'binding': bound, 'sourceBinding': source_binding,
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}


def entry_summary(config=None):
    """SDK alone parses account configuration; export sees sanitized locators."""
    from ioalab_sdk.runtime import RuntimeConfig,RuntimeFault
    path=configuration_path(config)
    if not path.is_file():return None
    try:return RuntimeConfig.load(path).public_summary()
    except RuntimeFault as exc:
        raise RuntimeError('SDK native entry metadata is unavailable: '+exc.code) from None


def native_entry_binding(*,python,config,host_ref,summary=None):
    """Hydrate only non-secret SDK metadata; never inspect native/session credentials."""
    from ioalab_sdk.runtime import native_source_descriptor
    python=str(Path(python).absolute());config=str(configuration_path(config))
    profiles={}
    if summary is not None:
        for role,slot in (('assistant','assistant'),('service','provider')):
            matches=[row for row in summary['identities'] if row['role']==role]
            # A role may own several provider profiles. An ambiguous entry is not guessed.
            if len(matches)!=1:continue
            profile=matches[0]['key']
            profiles[slot]={'profile':profile,
                'descriptorFile':str(native_source_descriptor(summary['dataDir'],host_ref,profile)),
                'ensureCommand':[python,'-I','-X','utf8','-m','ioalab_client.runtime','start','--config',config],
                'callerCommand':[python,'-I','-X','utf8','-m','ioalab_client','--role',role,'--config',config,'--native-input']}
    from .commands import source_operations
    from ioalab_sdk.runtime import NATIVE_ENTRY_BIND_OPERATION
    return {'format':'ioalab.native-entry/1','hostRef':host_ref,'profiles':profiles,'sourceOperations':list(source_operations()),'setupOperation':NATIVE_ENTRY_BIND_OPERATION}


def export_skills(output, *, source=None, python=None, config=None, host=None, host_ref=None, public_summary=None):
    if host is not None:
        from .runtime import registry
        registry({'default':host_ref or host,'registrations':{host_ref or host:{'type':host}}})
    elif host_ref is not None:
        raise ValueError('A source registration requires a host type')
    source = Path(source) if source else Path(__file__).parent/'data/skills'
    python = Path(python or sys.executable).absolute()
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    native_files={}
    if host is not None:
        import importlib
        from .runtime import HOSTS
        resources=getattr(importlib.import_module(HOSTS[host]).native_skills,'resource_files',None)
        if not callable(resources):raise RuntimeError('The selected adapter has not supplied its required native entry resources')
        bound=native_entry_binding(python=python,config=config,host_ref=host_ref or host,summary=public_summary)
        from ioalab_sdk.runtime import RuntimeFault
        try:native_files=resources(bound)
        except RuntimeFault as exc:
            raise RuntimeError('The selected adapter refused the native entry binding: '+exc.code) from None
        if not isinstance(native_files,dict) or 'entry-binding.json' not in native_files:
            raise RuntimeError('The adapter entry bundle is incomplete')
        if any(not isinstance(key,str) or not key or Path(key).is_absolute() or Path(key).drive or '\\' in key or '..' in Path(key).parts or
               not isinstance(value,bytes) for key,value in native_files.items()):
            raise RuntimeError('Invalid native entry resource path or content')
    products = []
    for folder, name, role in [('assistant', 'ioalab-delegate', 'assistant'), ('provider', 'ioalab-provider', 'service')]:
        files = {'native/'+key:value for key,value in native_files.items()}
        for path in sorted((source/folder).rglob('*.md')):
            files[path.relative_to(source/folder).as_posix()] = path.read_bytes()
        for path in sorted((source/'_shared/references').glob('*.md')):
            key = 'references/'+path.name
            if key in files: raise ValueError('Duplicate shared skill page: '+key)
            files[key] = path.read_bytes().replace(b'__IOALAB_SKILL_NAME__', name.encode())
        launcher = (source/'_shared/ioalab.py').read_text(encoding='utf-8')
        launcher = launcher.replace('__IOALAB_ROLE__', role)
        launcher = launcher.replace('__IOALAB_PYTHON__', repr(str(python)))
        launcher = launcher.replace('__IOALAB_CONFIG__', repr(str(configuration_path(config))))
        launcher = launcher.replace('__IOALAB_HOST_TYPE__',repr(host))
        launcher = launcher.replace('__IOALAB_HOST_REF__',repr(host_ref or host))
        files['scripts/ioalab.py'] = launcher.encode('utf-8')
        files['.ioalab-skill.json'] = json.dumps(skill_manifest(files, binding(config, python), {"hostType":host,"hostRef":host_ref or host} if host else None),
                                                 sort_keys=True, ensure_ascii=True).encode('utf-8')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for key, content in sorted(files.items()):
                item = zipfile.ZipInfo(name+'/'+key, (1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                archive.writestr(item, content)
        path = output/('ioalab-'+folder+'-skill.zip')
        content = buffer.getvalue()
        if path.exists():
            if path.is_symlink() or path.read_bytes() != content:
                raise RuntimeError('Existing skill export differs; preserve it and choose a new output directory')
        else:
            with path.open('xb') as stream: stream.write(content)
        products.append(path)
    return products
