"""Validate a self-contained, content-addressed production release using stdlib only."""
from __future__ import annotations

import email.parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
import zipfile

FORMAT = 'ioalab.deployment-release/1'
UNITS = {
    'core': frozenset({'ioalab', 'a2c', 'a2x-registry'}),
    'participant': frozenset({'ioalab-sdk', 'ioalab-jiuwen', 'ioalab-official', 'a2c', 'jiuwenswarm-process-sdk'}),
    'swarm': frozenset({'workswarm', 'openjiuwen', 'a4p', 'intelli-router'}),
}
TARGETS = ('linux-x86_64',)
HEX = re.compile(r'[a-f0-9]{64}')
PIN = re.compile(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def linked(path):
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def regular(root, name):
    root = Path(root).absolute()
    if not isinstance(name, str) or '\\' in name or ':' in name:
        raise ValueError('Release paths must be relative POSIX paths')
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('Release path escapes its directory')
    current = root
    if linked(current):
        raise ValueError('A release directory cannot be a link')
    for part in relative.parts:
        current = current / part
        if linked(current):
            raise ValueError('Release inputs cannot contain links')
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise ValueError('Release input must be a regular file inside its directory')
    return current


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    if Path(path).stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Deployment metadata is too large')
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique)


def wheel_metadata(path):
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(members) != 1:
            raise ValueError('Expected one wheel distribution')
        value = email.parser.Parser().parsestr(archive.read(members[0]).decode('utf-8'))
    if not value.get('Name') or not value.get('Version'):
        raise ValueError('Wheel distribution metadata is incomplete')
    return {'name': canonical(value['Name']), 'version': value['Version'],
            'requirements': value.get_all('Requires-Dist', []),
            'requiresPython': value.get('Requires-Python', '')}


def pins(text):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = PIN.fullmatch(line)
        if not match:
            raise ValueError('Production locks allow only exact package versions')
        name, version = canonical(match[1]), match[2]
        if name in result:
            raise ValueError('Duplicate package in production lock')
        result[name] = version
    if not result:
        raise ValueError('Empty production lock')
    return result


def current_target():
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 11):
        raise ValueError('This release requires CPython 3.11')
    system, machine = platform.system(), platform.machine().lower()
    if machine not in ('amd64', 'x86_64') or sys.maxsize <= 2**32:
        raise ValueError('Generate and verify a separate lock for this CPU architecture')
    if system != 'Linux':
        raise ValueError('Production releases run only on Linux; Windows is not a deployment target')
    return 'linux-x86_64'


def file_record(root, path):
    path = Path(path)
    name = path.relative_to(root).as_posix()
    return {'path': name, 'sha256': sha(regular(root, name))}


def checked(root, record, *, wheel=False):
    keys = {'path', 'sha256', 'name', 'version'} if wheel else {'path', 'sha256'}
    if not isinstance(record, dict) or set(record) != keys or not isinstance(record['sha256'], str) or not HEX.fullmatch(record['sha256']):
        raise ValueError('Invalid release file record')
    path = regular(root, record['path'])
    if sha(path) != record['sha256']:
        raise ValueError('Release input checksum mismatch: ' + record['path'])
    if wheel:
        meta = wheel_metadata(path)
        if not path.name.endswith('.whl') or meta['name'] != record['name'] or meta['version'] != record['version']:
            raise ValueError('Wheel metadata differs from the release manifest')
    return path


def official_catalog(root, document):
    """The verified release seed declares accounts and catalog IDs, not live identities."""
    seed = read_json(checked(root, document['assets']['seed']))
    domains = seed.get('domains') if isinstance(seed, dict) else None
    if not isinstance(domains, list) or not domains:
        raise ValueError('Release seed must declare the official catalog')
    accounts, seen = {}, set()
    for domain in domains:
        if not isinstance(domain, dict):
            raise ValueError('Invalid release catalog domain')
        account, services = domain.get('account'), domain.get('services')
        if (not isinstance(account, str) or not account.strip() or account in accounts
                or not isinstance(services, list) or not services):
            raise ValueError('Invalid or duplicate release catalog account')
        ids = []
        for service in services:
            identifier = service.get('id') if isinstance(service, dict) else None
            if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
                raise ValueError('Invalid or duplicate release catalog service')
            seen.add(identifier)
            ids.append(identifier)
        accounts[account] = tuple(ids)
    return accounts


def load(root, *, require_target=False):
    root = Path(root).absolute()
    value = read_json(regular(root, 'release.json'))
    if not isinstance(value, dict) or set(value) != {'format', 'releaseId', 'target', 'python', 'units', 'assets'}:
        raise ValueError('Invalid deployment release manifest')
    body = {key: data for key, data in value.items() if key != 'releaseId'}
    if value['format'] != FORMAT or value['target'] not in TARGETS or value['python'] != '3.11' or digest(body) != value['releaseId']:
        raise ValueError('Release content identity is invalid')
    if root.name != value['releaseId']:
        raise ValueError('Release directory must match its content identity')
    if require_target and value['target'] != current_target():
        raise ValueError('Release target differs from this interpreter and operating system')
    if not isinstance(value['units'], dict) or set(value['units']) != set(UNITS):
        raise ValueError('Release must contain exactly three isolated runtime units')
    for name, unit in value['units'].items():
        if not isinstance(unit, dict) or set(unit) != {'wheels', 'lock', 'inputSha256'} or not HEX.fullmatch(str(unit['inputSha256'])):
            raise ValueError('Invalid runtime unit')
        versions = {}
        if not isinstance(unit['wheels'], list):
            raise ValueError('Runtime wheels must be listed')
        for wheel in unit['wheels']:
            checked(root, wheel, wheel=True)
            if wheel['name'] in versions:
                raise ValueError('Duplicate local runtime package')
            versions[wheel['name']] = wheel['version']
        if set(versions) != UNITS[name]:
            raise ValueError('Runtime package crosses an environment boundary')
        ordinary = pins(checked(root, unit['lock']).read_text(encoding='utf-8'))
        if set(ordinary).intersection(set().union(*UNITS.values())):
            raise ValueError('Local components cannot be replaced by mirror packages')
        if name == 'participant' and len(ordinary) + len(versions) > 64:
            raise ValueError('Participant release exceeds the SDK installation identity limit')
    assets = value['assets']
    if not isinstance(assets, dict) or not {'seed', 'assistantSkill', 'providerSkill', 'sources'}.issubset(assets):
        raise ValueError('Release initialization, skills and source attribution are required')
    for record in assets.values():
        checked(root, record)
    return value
