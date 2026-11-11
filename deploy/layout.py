"""One deployment owns three disjoint roots; runtime data never belongs to an environment."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import uuid

from .release import read_json
from .files import path_without_links

FORMAT = 'ioalab.deployment-layout/1'
MARKER = '.ioalab-deployment.json'


def user_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', value) or value == 'root':
        raise ValueError('Use a dedicated lowercase system account name')
    return value


@dataclass(frozen=True)
class Layout:
    root: Path
    state: Path
    config: Path
    core_user: str = 'ioalab-core'
    agents_user: str = 'ioalab-agents'

    def __post_init__(self):
        for key in ('root', 'state', 'config'):
            path = path_without_links(getattr(self, key))
            if path == path.parent:
                raise ValueError('Use dedicated deployment directories, not a filesystem root')
            object.__setattr__(self, key, path)
        for value in (self.core_user, self.agents_user): user_name(value)
        if self.core_user == self.agents_user:
            raise ValueError('Core and participants require different system accounts')
        paths = (self.root, self.state, self.config)
        if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i+1:]):
            raise ValueError('Deployment roots must be disjoint')

    @property
    def identity(self):
        return {'format': FORMAT, 'root': str(self.root), 'state': str(self.state), 'config': str(self.config),
                'users': {'core': self.core_user, 'agents': self.agents_user}}

    @property
    def private(self): return self.state / '.deployment'

    @property
    def environments(self): return self.root / 'envs'

    def claim(self):
        """Resume a partial claim only through an existing matching deployment identity."""
        previous = []
        for path in (self.root, self.state, self.config):
            if path.exists():
                if not path.is_dir(): raise ValueError('A deployment root is not a directory')
                marker = path / MARKER
                if marker.exists():
                    path_without_links(marker)
                    record = read_json(marker)
                    if (not isinstance(record, dict) or set(record) != set(self.identity) | {'deploymentId'}
                            or any(record.get(k) != v for k, v in self.identity.items())):
                        raise ValueError('Deployment root ownership differs; preserve the original directories')
                    try:
                        if uuid.UUID(record['deploymentId']).hex != record['deploymentId']: raise ValueError()
                    except (ValueError, TypeError, AttributeError):
                        raise ValueError('Invalid deployment ownership identifier') from None
                    previous.append(record)
                elif any(path.iterdir()):
                    raise ValueError('Nonempty deployment directory has no ownership record; it will not be adopted')
        if previous and any(record != previous[0] for record in previous):
            raise ValueError('Deployment roots belong to different instances')
        record = previous[0] if previous else {**self.identity, 'deploymentId': uuid.uuid4().hex}
        for path in (self.root, self.state, self.config):
            path.mkdir(mode=0o755, parents=True, exist_ok=True)
            marker = path / MARKER
            if marker.exists():
                path_without_links(marker)
                if read_json(marker) != record:
                    raise ValueError('Another deployment claimed this directory; preserve its ownership')
            else:
                write_initial(marker, (json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'))
        self.private.mkdir(mode=0o700, exist_ok=True)
        return record

    def inspect(self):
        records = [read_json(path / MARKER) for path in (self.root, self.state, self.config)]
        record = records[0]
        if (any(item != record for item in records)
                or any(record.get(k) != v for k, v in self.identity.items())):
            raise ValueError('Deployment roots do not share the recorded identity')
        return record


def write_initial(path, content):
    """Initialization never overwrites an existing operator file."""
    path = path_without_links(path)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError('Initial configuration differs from the existing file; preserve and review it')
        return
    path.parent.mkdir(parents=True, mode=0o750, exist_ok=True)
    import os
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(content); output.flush(); os.fsync(output.fileno())


def environment_text(values):
    if any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or not isinstance(value, str)
           or any(ord(c) < 32 or ord(c) == 127 for c in value) for key, value in values.items()):
        raise ValueError('Environment files require literal single-line strings')
    return '# Literal data; do not source this file in a shell.\n' + ''.join(
        key + '=' + json.dumps(value, ensure_ascii=False) + '\n' for key, value in sorted(values.items()))


def read_environment(path):
    """Read our documented literal subset, never shell expansion or execution."""
    values = {}
    for line in Path(path).read_text(encoding='utf-8').split('\n'):
        if not line.strip() or line.lstrip().startswith('#'): continue
        key, separator, value = line.partition('=')
        if not separator or key in values or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError('Invalid managed environment data')
        try: decoded = json.loads(value)
        except ValueError: raise ValueError('Use double-quoted literal strings in managed environment files') from None
        if not isinstance(decoded, str) or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
            raise ValueError('Invalid managed environment value')
        values[key] = decoded
    return values
