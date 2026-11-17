"""Install both bound skills through host-native local skill directories."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from .layout import binding, configuration_path
from .skills import export_skills, skill_manifest, entry_summary

NAMES = ('ioalab-delegate', 'ioalab-provider')
HOSTS = ('jiuwen', 'codex', 'claude_code')


def owner(host):
    import importlib
    from .runtime import HOSTS
    if host not in HOSTS:raise ValueError('Select a supported host')
    return importlib.import_module(HOSTS[host]).native_skills


def destination(host):
    return owner(host).destination()


def checked(path, expected):
    if (not isinstance(expected, dict) or set(expected) != {'format', 'binding', 'sourceBinding', 'files'}
            or expected['format'] != 'ioalab.skill/2' or not isinstance(expected['files'], dict)
            or not 1 <= len(expected['files']) <= 256): return False
    if any(not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts
           or not isinstance(value, str) or len(value) != 64 for name, value in expected['files'].items()): return False
    if path.is_symlink() or not path.is_dir(): return False
    record = path/'.ioalab-skill.json'
    try:
        if record.is_symlink() or json.loads(record.read_text(encoding='utf-8')) != expected: return False
        items = list(path.rglob('*'))
        if any(item.is_symlink() for item in items): return False
        actual = {item.relative_to(path).as_posix() for item in items if item.is_file()}
        if actual != set(expected['files']) | {'.ioalab-skill.json'}: return False
        return all(not (path/name).is_symlink() and hashlib.sha256((path/name).read_bytes()).hexdigest() == digest
                   for name, digest in expected['files'].items())
    except (OSError, ValueError): return False


def recorded(path):
    """Ownership record of a verified, unmodified IoA Lab skill directory, else None."""
    record = path/'.ioalab-skill.json'
    try: prior = None if record.is_symlink() else json.loads(record.read_text(encoding='utf-8'))
    except (OSError, ValueError): return None
    return prior if checked(path, prior) else None


def same_owner(prior, expected):
    return (isinstance(prior, dict) and prior.get('binding') == expected['binding']
            and prior.get('sourceBinding') == expected['sourceBinding'])


def exported_record(path):
    """Manifest of an unmodified role export whose bytes still match it, else None."""
    try:
        if path.is_symlink(): return None
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            roots = {name.split('/', 1)[0] for name in names}
            if len(roots) != 1: return None
            root = roots.pop()
            prior = json.loads(archive.read(root+'/.ioalab-skill.json'))
            files = {name[len(root)+1:]: archive.read(name) for name in names if name != root+'/.ioalab-skill.json'}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile): return None
    if not isinstance(prior, dict) or prior.get('format') != 'ioalab.skill/2': return None
    return prior if prior == skill_manifest(files, prior.get('binding'), prior.get('sourceBinding')) else None


def export_owned(output, expected, **options):
    """Export afresh; replace only this client's own unmodified earlier export of the same binding."""
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.ioalab-export-', dir=output))
    try:
        products, replaced = [], False
        for fresh in export_skills(staging, **options):
            final = output/fresh.name
            if final.exists() or final.is_symlink():
                if final.is_symlink() or final.read_bytes() != fresh.read_bytes():
                    if not same_owner(exported_record(final), expected):
                        raise RuntimeError('Existing skill export differs; preserve it and choose a new output directory')
                    os.replace(fresh, final); replaced = True
            else: fresh.rename(final)
            products.append(final)
        return products, replaced
    finally: shutil.rmtree(staging, ignore_errors=True)


def place(target, name, files, expected, *, replace):
    """Stage a complete directory and rename it into place; a replaced original survives until the swap succeeds."""
    path = target/name
    target.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.ioalab-', dir=target))
    preserved = False
    try:
        staged = temporary/name; staged.mkdir()
        for key, data in files.items():
            dest = staged/key; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
        (staged/'.ioalab-skill.json').write_text(json.dumps(expected, ensure_ascii=True), encoding='utf-8')
        if not replace:
            # rename (not replace) refuses a concurrently created destination.
            staged.rename(path); return
        # The retired copy stays nested in staging, so a host never discovers it as another skill.
        retired = temporary/'retired'
        path.rename(retired)
        try: staged.rename(path)
        except OSError:
            try: retired.rename(path)
            except OSError:
                preserved = True
                raise RuntimeError('Skill replacement failed; the original skill is preserved at '+str(retired)) from None
            raise
    finally:
        if not preserved: shutil.rmtree(temporary, ignore_errors=True)


def install(host, *, config=None, output=None, source=None, python=None, host_ref=None, public_summary=None):
    adapter = owner(host)
    target = adapter.destination()
    config = configuration_path(config)
    if public_summary is None:public_summary=entry_summary(config)
    source_binding = {'hostType': host, 'hostRef': host_ref or host}
    owned = {'binding': binding(config, python), 'sourceBinding': source_binding}
    packages, exports_replaced = export_owned(output or config.parent/'role-skills'/(host_ref or host), owned,
        source=source, python=python, config=config, host=host, host_ref=host_ref or host, public_summary=public_summary)
    results = []
    for name, package in zip(NAMES, packages):
        item = {'name': name, 'package': str(package), 'path': str(target/name) if target else None,
                'installed': False, 'replaced': False, 'newSessionVerified': False}
        item['entry'] = adapter.entry(name)
        if target is None:
            item.update(status='manual_required', instructions=adapter.manual_steps(package,name))
            results.append(item); continue
        try:
            if any(parent.is_symlink() for parent in [target, *target.parents]):
                raise RuntimeError('Linked skill directories require explicit owner review')
            with zipfile.ZipFile(package) as archive:
                files = {key[len(name)+1:]: archive.read(key) for key in archive.namelist() if key.startswith(name+'/')}
            recorded_export = json.loads(files.pop('.ioalab-skill.json'))
            if any(Path(key).is_absolute() or '..' in Path(key).parts for key in files):
                raise RuntimeError('Invalid skill archive path')
            expected = skill_manifest(files, owned['binding'], source_binding)
            if recorded_export != expected: raise RuntimeError('Skill export does not match this client binding')
            path = target/name
            if path.exists() or path.is_symlink():
                if not checked(path, expected):
                    # Only this client's own verified, unmodified copy with the same binding is replaced.
                    if path.is_symlink() or not same_owner(recorded(path), expected):
                        raise RuntimeError('Existing skill differs; preserve it and explicitly resolve the conflict')
                    place(target, name, files, expected, replace=True)
                    item.update(replaced=True, reloadRequired=True)
            else: place(target, name, files, expected, replace=False)
            item.update(status='installed', installed=True)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
            item.update(status='manual_required', reason=str(error), instructions=adapter.manual_steps(package,name))
        results.append(item)
    return {'host': host, 'binding': owned['binding'], 'skills': results, 'exportsReplaced': exports_replaced,
            'reloadRequired': any(item.get('reloadRequired', False) for item in results),
            'installed': all(item['installed'] for item in results), 'newSessionVerified': False}


def status(host, *, config=None, python=None, host_ref=None):
    native = destination(host)
    result = []
    for name in NAMES:
        path = native/name if native else None
        valid = False
        if path:
            try:
                manifest = path/'.ioalab-skill.json'
                if not any(parent.is_symlink() for parent in [manifest, *manifest.parents]):
                    record = json.loads(manifest.read_text(encoding='utf-8'))
                    valid = (isinstance(record, dict) and record.get('binding') == binding(config, python)
                             and record.get('sourceBinding') in (None,{'hostType':host,'hostRef':host_ref or host}) and checked(path, record))
            except (OSError, ValueError): pass
        result.append({'name': name, 'path': str(path) if path else None, 'installed': valid,
                       'sourceBinding': record.get('sourceBinding') if valid else None, 'entry': owner(host).entry(name),
                       'newSessionVerified': False})
    return result
