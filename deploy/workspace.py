"""Prepare an owned original Swarm workspace without changing an existing user installation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .configuration import load as load_configuration, model as validate_model
from .environment import Environment, writer
from .release import digest, linked, read_json
from .processes import child_environment

FORMAT = 'ioalab.owned-swarm/1'
PHASES = {'initializing', 'workspace', 'awaiting_model', 'configuring', 'configured', 'ready'}

def path_without_links(value):
    path = Path(value).expanduser().absolute()
    if any(part == '..' for part in path.parts):
        raise ValueError('Workspace paths must not contain parent traversal')
    for part in (*reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            if linked(part):
                raise ValueError('Workspace paths must not contain links')
    return path


def save(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


PREPARE = '''from pathlib import Path
import os
from jiuwenswarm.common.utils import prepare_workspace
prepare_workspace(False, preferred_language='zh', workspace_dir=Path(os.environ['JIUWENSWARM_DATA_DIR']))
'''
INITIAL = '''from jiuwenswarm.common.config import update_config
def configure(value):
    value.setdefault('updater', {})['enabled'] = False
    return value
update_config(configure)
'''
MODEL = '''import json,sys
from jiuwenswarm.common.config import load_models_config,upsert_model_resource
value=json.load(sys.stdin)
entry=value['entry']
existing=load_models_config()['by_id'].get(entry['model_id'])
if existing is not None:
    current=existing['entry']; client=current.get('model_client_config', {})
    expected={'model_name':entry['model_name'], 'api_base':entry['api_base'], 'api_key':entry['api_key'], 'client_provider':entry['client_provider']}
    if (existing['source']!='defaults' or any(client.get(k)!=v for k,v in expected.items())
        or current.get('alias')!=entry['alias'] or current.get('is_default') is not True):
        raise RuntimeError('Original model resource conflicts; preserve it for explicit configuration')
else:
    upsert_model_resource(entry)
print('IOALAB_BOOTSTRAP_JSON:'+json.dumps({'configured': True}))
'''
RESOURCE = '''import json,sys
from jiuwenswarm.common.config import load_models_config
value=json.load(sys.stdin)
record=load_models_config()['by_id'].get(value['model_id'])
if not record or record['source']!='defaults':
    raise RuntimeError('Original model resource is absent')
entry=record['entry']; client=entry['model_client_config']
print('IOALAB_BOOTSTRAP_JSON:'+json.dumps({'name':client['model_name'], 'provider':client['client_provider'], 'display_name':entry.get('alias') or client['model_name']}))
'''


class Workspace:
    def __init__(self, python, workspace, deployment_id, *, run_as=None):
        try:
            if uuid.UUID(deployment_id).hex != deployment_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Use a recorded deployment UUID in lowercase hex format') from None
        self.python = Path(python).absolute()
        prefix = self.python.parent.parent.resolve()
        installation = read_json(prefix / 'deployment.json')
        if (installation.get('identity', {}).get('unit') != 'swarm'
                or installation.get('environmentId') != digest(installation.get('identity'))
                or (prefix / '.ready').read_text(encoding='utf-8') != installation['environmentId']):
            raise ValueError('Use a verified isolated Swarm environment')
        self.installation_id = installation['environmentId']
        self.home = path_without_links(workspace)
        if self.home == self.home.parent or self.home.is_relative_to(prefix) or prefix.is_relative_to(self.home):
            raise ValueError('Keep owned workspace and installed environments separate')
        self.command = self.python.parent / ('jiuwenswarm-process.exe' if os.name == 'nt' else 'jiuwenswarm-process')
        if not self.python.is_file() or not self.command.is_file():
            raise ValueError('The verified Swarm entry points are missing')
        self.owner = {'format': FORMAT, 'deploymentId': deployment_id, 'workspace': str(self.home)}
        self.execution = {}
        if run_as is not None:
            if (sys.platform != 'linux' or not isinstance(run_as, dict)
                    or set(run_as) != {'uid', 'gid', 'home', 'name'}
                    or any(type(run_as[k]) is not int or run_as[k] <= 0 for k in ('uid', 'gid'))):
                raise ValueError('Owned workspace queries require the verified Linux service identity')
            from .layout import user_name
            user_name(run_as['name']); path_without_links(run_as['home'])
            self.execution = {'user':run_as['uid'], 'group':run_as['gid'], 'extra_groups':[]}
        self.environment = child_environment()
        self.environment.update(PYTHONUTF8='1', PYTHONNOUSERSITE='1', JIUWENSWARM_DATA_DIR=str(self.home),
                                JIUWENSWARM_CONFIG_DIR=str(self.home / 'config'))
        if run_as is not None:
            self.environment.update(HOME=str(run_as['home']), USER=run_as['name'], LOGNAME=run_as['name'])

    def run(self, stage, command, value=None):
        try:
            result = subprocess.run(list(map(str, command)), cwd=self.home, env=self.environment,
                                    input=json.dumps(value) if value is not None else '',
                                    capture_output=True, text=True, encoding='utf-8', timeout=180, **self.execution)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('Owned Swarm preparation could not finish stage: ' + stage) from None
        if result.returncode:
            # Do not persist upstream tracebacks/stdout, which can include credentials.
            raise RuntimeError('Owned Swarm preparation failed stage: ' + stage + '; preserve the original workspace')
        return result.stdout

    def code(self, stage, source, value=None):
        output = self.run(stage, [self.python, '-I', '-c', source], value)
        records = [line[len('IOALAB_BOOTSTRAP_JSON:'):] for line in output.splitlines() if line.startswith('IOALAB_BOOTSTRAP_JSON:')]
        if len(records) != 1:
            raise RuntimeError('Owned Swarm did not return one preparation receipt: ' + stage)
        return json.loads(records[0])

    def selection(self, model_id):
        resource = self.code('read_model', RESOURCE, {'model_id': model_id})
        request_id = str(uuid.uuid4())
        request = {'schema_version': '0.1', 'type': 'query', 'request_id': request_id, 'operation': 'model.list', 'params': {}}
        output = self.run('model_catalog', [self.command, '--query-json', '-'], request)
        try:
            response = json.loads(output)
        except ValueError:
            raise RuntimeError('Original Swarm returned an invalid public model catalog') from None
        if response.get('status') != 'completed' or response.get('request_id') != request_id:
            raise RuntimeError('Original Swarm model catalog did not complete the original query')
        matches = [entry['selection_key'] for entry in response.get('data', {}).get('models', [])
                   if entry.get('model_name') == resource['name'] and entry.get('provider') == resource['provider']
                   and entry.get('display_name') == resource['display_name'] and entry.get('selection_key')]
        if len(matches) != 1:
            raise RuntimeError('Original model selection is absent or ambiguous; preserve current configuration')
        return matches[0]

    def prepare(self, model=None):
        model = validate_model(model)
        marker = self.home / 'ioalab-workspace.json'
        if self.home.exists() and (not self.home.is_dir() or (any(self.home.iterdir()) and not marker.is_file())):
            raise ValueError('Existing workspace has no deployment ownership; it will not be adopted')
        self.home.mkdir(parents=True, mode=0o700, exist_ok=True)
        with writer(self.home / 'ioalab-setup.lock'):
            if marker.exists():
                if linked(marker):
                    raise ValueError('Workspace ownership must not be a link')
                state = read_json(marker)
                if (any(state.get(k) != v for k, v in self.owner.items()) or state.get('phase') not in PHASES
                        or set(state) != set(self.owner) | {'phase', 'modelId', 'modelInputDigest'}):
                    raise ValueError('Workspace ownership or preparation state does not match')
                reused = state['phase'] == 'ready'
                if state['modelInputDigest'] is not None and state['modelInputDigest'] != digest(model):
                    raise ValueError('Initial model input changed; explicitly configure the existing owned instance')
            else:
                if any(path.name != 'ioalab-setup.lock' for path in self.home.iterdir()):
                    raise ValueError('Unrecorded workspace files exist; preserve and inspect them')
                state = {**self.owner, 'phase': 'initializing', 'modelId': None, 'modelInputDigest': None}
                save(marker, state); reused = False
            if self.execution and state['phase'] != 'ready':
                raise RuntimeError('Complete the original root preparation before handing over the workspace')
            if state['phase'] == 'initializing':
                self.run('prepare_workspace', [self.python, '-I', '-c', PREPARE])
                state['phase'] = 'workspace'; save(marker, state)
            if state['phase'] == 'workspace':
                self.run('initial_config', [self.python, '-I', '-c', INITIAL])
                state['phase'] = 'awaiting_model'; save(marker, state)
            if state['phase'] == 'awaiting_model':
                if model is None:
                    return self.result(None, reused=False)
                state.update(phase='configuring', modelId='mdl_' + uuid.uuid4().hex, modelInputDigest=digest(model))
                save(marker, state)
            if state['phase'] == 'configuring':
                entry = {'model_id': state['modelId'], 'alias': 'IoA official model', 'model_name': model['name'],
                         'api_base': model['base_url'], 'api_key': model['api_key'], 'client_provider': model['provider'], 'is_default': True}
                self.code('configure_model', MODEL, {'entry': entry})
                state['phase'] = 'configured'; save(marker, state)
            selected = self.selection(state['modelId'])
            if state['phase'] != 'ready':
                state['phase'] = 'ready'; save(marker, state)
            return self.result(selected, reused=reused)

    def result(self, selected, *, reused):
        return {'status': 'completed' if selected else 'not_ready', 'reason': None if selected else 'configure_model',
                'environmentId': self.installation_id, 'data_dir': str(self.home), 'command': [str(self.command)],
                'model': selected, 'reused': reused}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True, type=Path)
    parser.add_argument('--environments', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--deployment-id', required=True)
    args = parser.parse_args()
    config = load_configuration(args.config)
    environment = Environment(args.release, 'swarm', args.environments).install(verify=True)
    result = Workspace(environment['python'], args.workspace, args.deployment_id).prepare(config['model'])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
