"""Build an immutable production release; no Git, npm, credentials or runtime data are copied."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from . import skills
from .dependencies import inputs
from .release import FORMAT, TARGETS, UNITS, digest, file_record, load, read_json, sha, wheel_metadata
from .vendor import validate
from .wheels import build_wheel

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ('src/platform', 'vendor/A2X-registry', 'src/access/sdk', 'src/hosts/jiuwen', 'src/official')


def build_wheels(root, output):
    root, output = Path(root), Path(output)
    manifest = validate(root)
    output.mkdir(parents=True, exist_ok=True)
    for project in PROJECTS:
        build_wheel(root / project, output)
    for component in manifest['components']:
        if component.get('wheelInfo'):
            info = component['wheelInfo']
            original = root / 'vendor' / info['file']
            if sha(original) != info['sha256']:
                raise ValueError('Vendor wheel changed during the build')
            shutil.copy2(original, output / original.name)
    return sorted(output.glob('*.whl'))


def stable_zip(path):
    with zipfile.ZipFile(path) as archive:
        content = [(name, archive.read(name)) for name in sorted(archive.namelist())]
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in content:
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, value)


def assemble(root, folder, target, locks, wheels, skills):
    root, folder, locks = Path(root), Path(folder), Path(locks)
    pool = {}
    for path in wheels:
        metadata = wheel_metadata(path)
        if metadata['name'] in pool:
            raise ValueError('Duplicate local deployment wheel')
        destination = folder / 'wheels' / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        pool[metadata['name']] = {**file_record(folder, destination), 'name': metadata['name'], 'version': metadata['version']}
    units = {}
    for unit, names in UNITS.items():
        plan = inputs(unit, target, wheels)
        record = read_json(locks / (unit + '.json'))
        if (record.get('format') != 'ioalab.deployment-lock/1' or record.get('unit') != unit
                or record.get('target') != target or record.get('python') != '3.11'
                or record.get('inputSha256') != plan['inputSha256']
                or record.get('lockSha256') != sha(locks / (unit + '.lock'))):
            raise ValueError('Rebuild the target lock from the current wheel dependency metadata: ' + unit)
        destination = folder / 'locks' / (unit + '.lock')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(locks / (unit + '.lock'), destination)
        units[unit] = {'wheels': [pool[name] for name in sorted(names)], 'lock': file_record(folder, destination),
                       'inputSha256': plan['inputSha256']}
    assets = {}
    sources = [('seed', root / 'deploy/default-seed.json', 'seed.json'),
               ('sources', root / 'vendor/SOURCES.json', 'attribution/SOURCES.json')]
    for role in ('assistant', 'provider'):
        original = Path(skills) / ('ioalab-' + role + '-skill.zip')
        # Both product ZIPs must ship the same participant wheels as the release.
        with zipfile.ZipFile(original) as archive:
            bundled = {Path(name).name: archive.read(name) for name in archive.namelist() if '/assets/wheels/' in name and name.endswith('.whl')}
        expected = {Path(pool[name]['path']).name: pool[name]['sha256'] for name in UNITS['participant']}
        import hashlib
        if {name: hashlib.sha256(value).hexdigest() for name, value in bundled.items()} != expected:
            raise ValueError('Product skill and production participant wheels differ')
        sources.append((role + 'Skill', original, 'skills/' + original.name))
    for path in sorted((root / 'vendor/licenses').rglob('*')):
        if path.is_file():
            relative = path.relative_to(root / 'vendor/licenses').as_posix()
            sources.append(('license:' + relative, path, 'attribution/licenses/' + relative))
    for key, original, name in sources:
        destination = folder / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
        if key.endswith('Skill'):
            stable_zip(destination)
        assets[key] = file_record(folder, destination)
    body = {'format': FORMAT, 'target': target, 'python': '3.11', 'units': units, 'assets': assets}
    document = {**body, 'releaseId': digest(body)}
    (folder / 'release.json').write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return document


def build(output, target, *, root=ROOT):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ioa-build-', dir=output) as temporary:
        work = Path(temporary)
        wheels = build_wheels(root, work / 'input-wheels')
        skills.build(work / 'skills', root=root)
        folder = work / 'release'
        folder.mkdir()
        document = assemble(root, folder, target, root / 'deploy/locks' / target, wheels, work / 'skills')
        staged = work / document['releaseId']
        folder.rename(staged)
        load(staged)
        destination = output / document['releaseId']
        if destination.exists():
            if load(destination) != document:
                raise ValueError('An existing content-addressed release differs; preserve and inspect it')
        else:
            staged.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'dist/deployment')
    parser.add_argument('--target', required=True, choices=TARGETS)
    parser.add_argument('--wheels-only', action='store_true', help='build lock inputs without assembling a release')
    args = parser.parse_args()
    if args.wheels_only:
        for path in build_wheels(ROOT, args.out):
            print(path)
    else:
        print(build(args.out, args.target))


if __name__ == '__main__':
    main()
