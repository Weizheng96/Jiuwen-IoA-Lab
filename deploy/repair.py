"""Explicit controller-only recovery of a stopped, failed first bootstrap.

Uses the original Job, configuration and installed set. No business data edits,
new installation roots, source rebinding or implicit force option are provided.
"""
import copy
from pathlib import Path
import uuid

from . import activation, units
from .environment import writer
from .jobs import Job, source_identity, source_files
from .layout import MARKER
from .linux import Accounts, Systemd, protected_file, regular
from .release import digest, read_json
from .workspace import save


CONTROLLER_FILES = frozenset({
    'deploy/__main__.py', 'deploy/jobs.py', 'deploy/installation.py',
    'deploy/repair.py', 'deploy/units.py', 'deploy/layout.py',
    'tools/check_scope.py',
})


def changed_files(previous, current):
    """Compare the same complete input manifest used by source_identity."""
    if source_identity(previous['root']) != previous:
        raise RuntimeError('The original failed installation source changed')
    if source_identity(current['root']) != current:
        raise RuntimeError('The repair controller source changed')
    before, after = source_files(previous['root']), source_files(current['root'])
    changes = {name: {'before': before.get(name), 'after': after.get(name)}
               for name in sorted(set(before) | set(after))
               if before.get(name) != after.get(name)}
    if not changes or set(changes) - CONTROLLER_FILES:
        raise RuntimeError('Repair permits only reviewed deployment controller changes')
    if any(value['after'] is None for value in changes.values()):
        raise RuntimeError('Repair cannot remove original deployment controller inputs')
    return changes


def initial_input(layout, expected):
    value = read_json(regular(layout.private / 'initial.json'))
    if (value.get('deploymentId') != layout.inspect()['deploymentId']
            or value.get('inputDigest') != expected
            or digest(value.get('input')) != expected):
        raise RuntimeError('Repair must retain the original complete installation input')
    return value


def stopped_ownership(layout, metadata, initial):
    """Read-only use of existing ownership checks; no account/path creation."""
    import pwd
    owner = layout.inspect()['deploymentId']
    if metadata.get('deploymentId') != owner or metadata.get('initialized'):
        raise RuntimeError('Repair requires the original incomplete deployment')
    for root in (layout.root, layout.state, layout.config, layout.private):
        if root.resolve() != root.absolute() or not protected_file(root):
            raise RuntimeError('The original protected deployment roots changed')
    for root in (layout.root, layout.state, layout.config):
        if not protected_file(regular(root / MARKER)):
            raise RuntimeError('The original deployment marker ownership changed')
    # ensure/reserve cannot create anything after these ready/owned preconditions.
    # Work on a private copy and use a no-write callback, reusing UID/GECOS/group
    # and state-leaf checks instead of maintaining another account algorithm.
    account_record = copy.deepcopy(metadata)
    accounts = Accounts(layout, owner, account_record, lambda: None)
    for role in ('core', 'agents'):
        record = account_record.get('accounts', {}).get(role, {})
        if record.get('phase') != 'ready':
            raise RuntimeError('Repair requires both original ready service accounts')
        try:
            pwd.getpwnam(record['name'])
        except (KeyError, TypeError):
            raise RuntimeError('Repair cannot recreate a missing original service account') from None
        accounts.ensure(role)
    for name, record in account_record.get('paths', {}).items():
        if record.get('phase') != 'owned':
            raise RuntimeError('Repair cannot adopt an unfinished state ownership transfer')
        accounts.reserve(Path(name), record['role'], create=False)
    from .configuration import platform
    plans = units.plans(layout, administrator=initial['input']['admin_assistant']['enabled'],
                        core=platform(initial['input']['platform_config']),trial=initial['input'].get('trial'))
    if set(metadata.get('units', {})) != {row['unit'] for row in plans.values()}:
        raise RuntimeError('Repair requires the complete original managed unit mapping')
    manager = Systemd(owner, metadata['units'], lambda: None)
    for name, entry in metadata['units'].items():
        live = manager.owned(name)
        if (entry.get('phase') != 'ready'
                or live.get('ActiveState') not in ('inactive', 'failed')
                or not manager.empty(live.get('ControlGroup', ''))):
            raise RuntimeError('Stop and verify all original service groups before repair')


def evidence(parent, source, *, candidate=None, stopped=True):
    request = parent.request()
    observed = parent.observe()
    if (request['action'] != 'install'
            or observed.get('status') != 'stopped'
            or observed.get('stage') != 'bootstrap'
            or observed.get('outcome') != 'failed'):
        raise RuntimeError('Repair requires a stopped failed first bootstrap operation')
    progress = read_json(regular(parent.folder / 'progress.json'))
    active = activation.current(parent.layout)
    if active is None or active != progress.get('candidate') or (candidate is not None and active != candidate):
        raise RuntimeError('The original complete active installation candidate changed')
    initial = initial_input(parent.layout, request['inputDigest'])
    metadata = read_json(regular(parent.layout.private / 'installation.json'))
    if stopped and metadata.get('initialized'):
        raise RuntimeError('A completed deployment must use the ordinary upgrade path')
    if stopped:
        stopped_ownership(parent.layout, metadata, initial)
    return {'parentOperation': parent.operation, 'parentSource': request['source'],
            'candidate': active, 'changes': changed_files(request['source'], source)}


def operation_id(parent, source):
    return uuid.uuid5(uuid.UUID(parent), source['digest']).hex


def pointer_for(job, request):
    return {key: value for key, value in request.items() if key in ('operationId', 'action', 'source', 'inputDigest')}


def submit(layout, source_root, parent_operation):
    source = source_identity(source_root)
    parent = Job(layout, parent_operation)
    selected = Job(layout, operation_id(parent_operation, source))
    pointer_path = regular(layout.private / 'current-job.json')
    with writer(layout.private / 'launcher.lock'):
        pointer = read_json(pointer_path)
        # Live observation must not contend for the worker's installer lock.
        if pointer.get('operationId') == selected.operation:
            request = selected.request()
            if pointer != pointer_for(selected, request):
                raise RuntimeError('The complete original repair pointer changed')
            repair = request.get('repair', {})
            expected = evidence(parent, source, candidate=repair.get('candidate'), stopped=False)
            if (request.get('action') != 'repair-bootstrap' or request.get('source') != source
                    or request.get('inputDigest') != parent.request()['inputDigest']
                    or repair != expected):
                raise RuntimeError('The original repair request or complete input changed')
        else:
            with writer(layout.private / 'installer.lock'):
                if pointer != pointer_for(parent, parent.request()):
                    raise RuntimeError('Another original operation owns this deployment')
                repair = evidence(parent, source)
                selected.create('repair-bootstrap', source, parent.request()['inputDigest'], repair=repair)
                # Request is immutable and durable before the only pointer update.
                save(pointer_path, pointer_for(selected, selected.request()))
    return selected.submit()


def worker_admission(job, request):
    """Second check under worker's installer lock, before any bootstrap writes."""
    if request['action'] != 'repair-bootstrap':
        return
    repair = request.get('repair', {})
    parent = Job(job.layout, repair.get('parentOperation'))
    pointer = read_json(regular(job.layout.private / 'current-job.json'))
    if (pointer != pointer_for(job, request)
            or job.operation != operation_id(parent.operation, request['source'])
            or request['inputDigest'] != parent.request()['inputDigest']):
        raise RuntimeError('This worker is not the original complete repair operation')
    # Once progress exists, only the same fixed-source job can resume its stage.
    progress = job.folder / 'progress.json'
    expected = evidence(parent, request['source'], candidate=repair.get('candidate'), stopped=not progress.exists())
    if repair != expected:
        raise RuntimeError('Repair evidence changed before the original worker continued')


def initial_progress(request, plans):
    if request['action'] != 'repair-bootstrap':
        return {'stage': 'candidate'}
    return {'stage': 'bootstrap', 'candidate': request['repair']['candidate'],
            'requiredRunning': list(plans)}
