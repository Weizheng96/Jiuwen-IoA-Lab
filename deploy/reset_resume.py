"""Bounded source handoff for a stopped test-reset initialization failure."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import re
import uuid

from . import activation
from .files import path_without_links, save, writer
from .jobs import Job, source_identity
from .release import read_json


def binding(parent):
    request = parent.request()
    progress = read_json(parent.folder/'progress.json')
    if (request['action'] != 'test-reset' or parent.observe().get('status') != 'stopped'
            or progress.get('outcome') != 'failed' or progress.get('stage') != 'test-reset'
            or progress.get('testReset', {}).get('phase') != 'initializing'):
        raise RuntimeError('Source handoff requires a stopped failed test-reset initialization')
    if source_identity(request['source']['root']) != request['source']:
        raise RuntimeError('Restore the original failed source before handoff')
    return {'parentOperation': parent.operation, 'parentSource': request['source'],
            'parentCandidate': progress['candidate']}


def submit(layout, source_root, parent_operation):
    from .repair import initial_input, pointer_for
    source = source_identity(source_root)
    parent = Job(layout, parent_operation)
    operation = uuid.uuid5(uuid.UUID(parent_operation), 'test-reset:'+source['digest']).hex
    child = Job(layout, operation)
    pointer_path = layout.private/'current-job.json'
    with writer(layout.private/'launcher.lock'):
        pointer = read_json(pointer_path)
        if pointer.get('operationId') == child.operation:
            request = child.request()
            if (request['source'] != source or request.get('resumeTestReset', {}).get('parentOperation') != parent_operation
                    or pointer != pointer_for(child, request)):
                raise RuntimeError('The original reset handoff changed')
        else:
            with writer(layout.private/'installer.lock'):
                if pointer != pointer_for(parent, parent.request()):
                    raise RuntimeError('Another original operation owns this deployment')
                resume = binding(parent)
                initial = initial_input(layout, parent.request()['inputDigest'])
                metadata = read_json(layout.private/'installation.json')
                if (source['digest'] == resume['parentSource']['digest'] or metadata.get('initialized')
                        or initial['input'].get('trial') is not None
                        or activation.current(layout) != resume['parentCandidate']
                        or not (layout.state/'core/.core.lock').is_file()):
                    raise RuntimeError('Reset handoff requires the original incomplete core, candidate and no trial')
                child.create('test-reset', source, parent.request()['inputDigest'], resume=resume)
                save(pointer_path, pointer_for(child, child.request()))
    return child.submit()


def admit(job, request):
    resume = request.get('resumeTestReset')
    if resume is None: return
    if (request['action'] != 'test-reset' or set(resume) != {'parentOperation', 'parentSource', 'parentCandidate'}
            or job.operation != uuid.uuid5(uuid.UUID(resume['parentOperation']), 'test-reset:'+request['source']['digest']).hex):
        raise RuntimeError('Invalid original reset handoff')
    progress = job.folder/'progress.json'
    if progress.exists() and read_json(progress).get('stage') == 'completed': return
    parent = Job(job.layout, resume['parentOperation'])
    if binding(parent) != resume or parent.request()['inputDigest'] != request['inputDigest']:
        raise RuntimeError('Parent reset evidence changed')
    if not progress.exists() and activation.current(job.layout) != resume['parentCandidate']:
        raise RuntimeError('The parent reset candidate changed')


@contextmanager
def partial_leases(installer):
    """Hold all existing writers, allowing only verified unprepared role gaps."""
    from .test_server import role_processes
    layout, metadata = installer.layout, installer.metadata
    if not installer.request.get('resumeTestReset') or role_processes(metadata):
        raise RuntimeError('Reset handoff requires released original role processes')
    agents = layout.state/'agents'
    locks = [layout.state/'core/.core.lock']
    configurations = []
    official = read_json(path_without_links(layout.config/'agents/official.json'))
    home = path_without_links(official['data_dir'])
    if not home.is_relative_to(agents): raise RuntimeError('Official state escaped its original role')
    if (home/'deployment.lock').exists(): locks.append(home/'deployment.lock')
    elif metadata.get('officialPrepared'): raise RuntimeError('Restore the original coordinator lock')
    state = read_json(path_without_links(home/'state.json')) if (home/'state.json').exists() else {}
    accounts = state.get('accounts', {})
    if not isinstance(accounts, dict) or (not accounts and metadata.get('officialPrepared')):
        raise RuntimeError('Restore the original official mapping')
    for name in sorted(accounts):
        if not re.fullmatch(r'ISP_\d{3}', name): raise RuntimeError('Invalid original official mapping')
        configurations.append((home/'accounts'/name/'config.json', bool(metadata.get('officialPrepared'))))
    if installer.config.input['admin_assistant']['enabled']:
        configurations.append((agents/'admin-assistant/config.json', bool(metadata.get('administratorPrepared'))))
    for config, prepared in configurations:
        config = path_without_links(config)
        if not config.is_file():
            if prepared: raise RuntimeError('Restore the prepared SDK configuration')
            continue
        value = read_json(config); directory = Path(value['data_dir'])
        directory = path_without_links(directory if directory.is_absolute() else config.parent/directory)
        if not directory.is_relative_to(agents): raise RuntimeError('SDK state escaped its original role')
        lock = path_without_links(directory/'client.lock')
        if lock.exists(): locks.append(lock)
        elif prepared: raise RuntimeError('Restore the prepared SDK execution lock')
    with ExitStack() as held:
        for path in sorted(set(locks)):
            path = path_without_links(path)
            if not path.is_file(): raise RuntimeError('Restore the original actual writer lock')
            held.enter_context(writer(path))
        if role_processes(metadata): raise RuntimeError('A role writer reappeared during reset handoff')
        yield


def finish(job, request):
    resume = request.get('resumeTestReset')
    if resume is None: return
    from .test_server import remove
    parent = Job(job.layout, resume['parentOperation'])
    evidence = job.folder/'resumed-parent.json'
    if evidence.exists():
        saved = read_json(path_without_links(evidence))
        original = saved.get('request', {})
        if (original.get('operationId') != parent.operation or original.get('source') != resume['parentSource']
                or original.get('deploymentId') != job.owner
                or saved.get('progress', {}).get('candidate') != resume['parentCandidate']):
            raise RuntimeError('Restore the original parent reset evidence')
    else:
        if parent.observe().get('status') != 'stopped':
            raise RuntimeError('Parent reset writers must remain stopped')
        save(evidence, {'request': parent.request(), 'progress': read_json(parent.folder/'progress.json')})
    if read_json(job.layout.private/'current-job.json').get('operationId') != job.operation:
        raise RuntimeError('Another operation owns the current reset')
    if parent.folder.exists(): remove(parent.folder)
