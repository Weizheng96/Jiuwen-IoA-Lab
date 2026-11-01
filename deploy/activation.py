"""Verify complete environment sets and atomically activate one owned current link."""
from __future__ import annotations

import os
from pathlib import Path
import re
import uuid

from .release import UNITS, digest, linked, load, read_json, regular
from .workspace import save, path_without_links

FORMAT = 'ioalab.environment-set/1'


def release_in(layout, release):
    release = path_without_links(release)
    if release.parent != layout.root / 'releases':
        raise ValueError('Release must be a direct child of the owned release root')
    return release, load(release, require_target=True)


def installed(layout, unit, result, document):
    path = path_without_links(result['environment'])
    if not path.is_dir() or path.parent.parent != layout.environments:
        raise ValueError('Environment target escapes its owned installation group')
    marker = read_json(regular(path, 'deployment.json'))
    identity = marker.get('identity')
    if (not isinstance(identity, dict) or identity.get('unit') != unit
            or identity.get('plan') != document['units'][unit]
            or identity.get('target') != document['target']
            or digest(identity) != result['environmentId'] or marker.get('environmentId') != result['environmentId']
            or regular(path, '.ready').read_text(encoding='utf-8') != result['environmentId']):
        raise ValueError('Only a completed environment of this exact release may enter a set')
    group = path.parent
    state = read_json(regular(group, 'state.json'))
    if (group.name != unit + '-' + result['environmentId'][:16]
            or read_json(regular(group, 'owner.json')) != identity
            or state != {'environmentId': result['environmentId'], 'phase': 'ready', 'attempt': path.name}
            or not re.fullmatch(r'a-[a-f0-9]{16}', path.name)):
        raise ValueError('Environment has no matching completed installation attempt')
    if unit == 'participant':
        expected = {'format': 'ioalab.access-installation/1', 'releaseId': result['environmentId'], 'versions': marker['versions']}
        if read_json(regular(path, 'ioalab-release.json')) != expected:
            raise ValueError('Participant SDK installation identity changed')
    return {'environment': str(path), 'environmentId': result['environmentId']}


def check_set(layout, folder):
    root = path_without_links(layout.environments / 'sets')
    folder = Path(folder)
    if (folder.parent != root or not re.fullmatch('[a-f0-9]{64}', folder.name)
            or not folder.is_dir() or linked(folder)):
        raise ValueError('Current environment set must be an owned direct child')
    value = read_json(regular(folder, 'set.json'))
    expected = {'format', 'release', 'releaseId', 'units', 'setId'}
    if (not isinstance(value, dict) or set(value) != expected or value['format'] != FORMAT
            or not isinstance(value['units'], dict) or set(value['units']) != set(UNITS)
            or value['setId'] != folder.name
            or digest({k: v for k, v in value.items() if k != 'setId'}) != value['setId']):
        raise ValueError('Environment set identity is incomplete or changed')
    _, document = release_in(layout, value['release'])
    if document['releaseId'] != value['releaseId']:
        raise ValueError('Environment set release changed')
    if {p.name for p in folder.iterdir()} != {'set.json', *UNITS}:
        raise ValueError('Environment set contains unrecorded entries')
    for unit, entry in value['units'].items():
        target = folder / unit
        if (not isinstance(entry, dict) or set(entry) != {'environment', 'environmentId'}
                or not target.is_symlink() or str(target.resolve(strict=True)) != entry['environment']):
            raise ValueError('Environment set link differs from its recorded target')
        if installed(layout, unit, entry, document) != entry:
            raise ValueError('Environment set does not name the original installation')
    return value


def prepare_set(layout, release, results):
    release, document = release_in(layout, release)
    release_id = document['releaseId']
    if (set(results) != set(UNITS)
            or any(result.get('status') != 'completed' or result.get('unit') != unit
                   or result.get('releaseId') != release_id for unit, result in results.items())):
        raise ValueError('One complete verified release is required for an environment set')
    units = {unit: installed(layout, unit, results[unit], document) for unit in UNITS}
    value = {'format': FORMAT, 'release': str(release), 'releaseId': release_id, 'units': units}
    value['setId'] = digest(value)
    sets = path_without_links(layout.environments / 'sets')
    sets.mkdir(parents=True, exist_ok=True)
    destination = sets / value['setId']
    if destination.exists() or destination.is_symlink():
        if check_set(layout, destination) != value:
            raise ValueError('Existing environment set differs; preserve it')
        return value
    temporary = sets / ('.set-' + uuid.uuid4().hex)
    temporary.mkdir(mode=0o755)
    save(temporary / 'set.json', value)
    for unit, entry in units.items():
        (temporary / unit).symlink_to(entry['environment'], target_is_directory=True)
    temporary.rename(destination)
    check_set(layout, destination)
    return value


def current(layout):
    link = layout.environments / 'current'
    if not link.exists() and not link.is_symlink():
        return None
    if not link.is_symlink():
        raise ValueError('Current is not a managed environment-set link')
    return check_set(layout, link.resolve(strict=True))


def activate(layout, candidate, previous_id):
    """Caller holds the installer lock and has proved business writers released."""
    destination = layout.environments / 'sets' / candidate['setId']
    if check_set(layout, destination) != candidate:
        raise ValueError('Candidate environment set changed')
    actual = current(layout)
    actual_id = actual['setId'] if actual else None
    if actual_id == candidate['setId']:
        return {'setId': actual_id, 'changed': False}
    if actual_id != previous_id:
        raise ValueError('Current environment differs from the saved upgrade operation')
    temporary = layout.environments / ('.current-' + uuid.uuid4().hex)
    temporary.symlink_to(destination, target_is_directory=True)
    os.replace(temporary, layout.environments / 'current')
    if current(layout) != candidate:
        raise RuntimeError('Current changed after activation; inspect the original operation')
    return {'setId': candidate['setId'], 'changed': True}
