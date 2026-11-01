"""Derive target-specific lock inputs from the actual isolated-unit wheel metadata."""
from __future__ import annotations

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from deploy.release import TARGETS, UNITS, canonical, digest, wheel_metadata


def inputs(unit, target, wheels):
    if unit not in UNITS or target not in TARGETS:
        raise ValueError('Unknown deployment unit or target')
    pool = {}
    for path in wheels:
        meta = wheel_metadata(path)
        if meta['name'] in pool:
            raise ValueError('Each local component requires exactly one wheel')
        pool[meta['name']] = meta
    if not UNITS[unit].issubset(pool):
        raise ValueError('A local dependency wheel is missing')
    linux = target.startswith('linux-')
    environment = default_environment()
    environment.update(python_version='3.11', python_full_version='3.11.0', implementation_version='3.11.0',
                       implementation_name='cpython', platform_python_implementation='CPython',
                       sys_platform='linux' if linux else 'win32', platform_system='Linux' if linux else 'Windows',
                       os_name='posix' if linux else 'nt', platform_machine='x86_64' if linux else 'AMD64',
                       platform_release='', platform_version='')
    ordinary, local_urls = set(), set()
    extras = {name: set() for name in UNITS[unit]}
    pending, seen = list(UNITS[unit]), {}
    while pending:
        name = pending.pop()
        active = extras[name]
        if name in seen and active <= seen[name]:
            continue
        seen[name] = set(active)
        meta = pool[name]
        if meta['requiresPython'] and Version('3.11.0') not in SpecifierSet(meta['requiresPython']):
            raise ValueError('Local component does not support the release interpreter')
        for raw in meta['requirements']:
            required = Requirement(raw)
            key = canonical(required.name)
            if required.marker and not any(required.marker.evaluate({**environment, 'extra': extra}) for extra in active | {''}):
                continue
            if key in pool:
                if key not in UNITS[unit]:
                    raise ValueError('Dependency crosses runtime boundary: ' + unit + ' -> ' + key)
                if Version(pool[key]['version']) not in required.specifier:
                    raise ValueError('Local dependency version does not satisfy wheel metadata')
                if required.url:
                    local_urls.add((name, key))
                before = set(extras[key])
                extras[key].update(required.extras)
                if key not in seen or before != extras[key]:
                    pending.append(key)
            else:
                if required.url:
                    raise ValueError('Unbundled direct URL dependency: ' + key)
                ordinary.add(key + ('[' + ','.join(sorted(required.extras)) + ']' if required.extras else '') + str(required.specifier))
    body = {'unit': unit, 'target': target, 'python': '3.11', 'requirements': sorted(ordinary),
            'local': sorted((name, pool[name]['version'], sorted(pool[name]['requirements']), pool[name]['requiresPython']) for name in UNITS[unit]),
            'localUrls': sorted(local_urls)}
    return {'requirements': body['requirements'], 'inputSha256': digest(body)}
