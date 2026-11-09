"""Finite model-side IPC adapter; SDK credentials and confirmation DB are inaccessible.

Installed only into the ephemeral trial Skill copy. This is not a user channel.
"""
import argparse
import json
import os
from pathlib import Path
import uuid


SAFE = {'runtime.status','identity.status','operations.get','operations.resume','confirmations.get'}


def arguments(path, *, root=Path('/epoch/native')):
    """Persist an operation before sending; only caller-owned argument files."""
    if path is None:return {},str(uuid.uuid4())
    path = path.absolute()
    root = root.resolve()
    if ('..' in path.parts or not path.is_relative_to(root) or any(item.is_symlink() for item in (path,*path.parents))
            or path.name in ('model-bridge.json','settings.local.json') or '/.ioalab/' in path.as_posix()
            or not path.is_file() or path.stat().st_size > 128*1024):
        raise ValueError('Use a new arguments file within this trial workspace')
    value = json.loads(path.read_text('utf-8'))
    if not isinstance(value,dict):raise ValueError('Arguments must be an object')
    operation = value.get('operationId')
    if operation is None:
        operation = str(uuid.uuid4())
        value['operationId'] = operation
        temporary = path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    if not isinstance(operation,str) or not 1 <= len(operation) <= 128:
        raise ValueError('Invalid operationId')
    return {key:item for key,item in value.items() if key != 'operationId'},operation


def forward(body):
    from ioalab_sdk.runtime import LocalClient
    if not isinstance(body,dict) or set(body) != {'operation','arguments','operationId'}:
        raise ValueError('Invalid model bridge request')
    operation = body['operation']
    # Match actual exported role operations; never wildcard newly added methods.
    allowed = SAFE | {
        'assistant.delegation.begin','assistant.delegation.prepare','assistant.delegation.authorize',
        'assistant.delegation.renew','assistant.delegation.status','assistant.delegation.close',
        'assistant.delegation.intent','assistant.delegation.intent_status','assistant.delegation.request',
        'assistant.delegation.inspect','assistant.delegation.search','assistant.delegation.confirm',
        'assistant.delegation.compare','assistant.delegation.start','assistant.delegation.answer_step',
        'assistant.delegation.fetch','assistant.delegation.verify','assistant.delegation.accept',
        'assistant.delegation.pay','assistant.delegation.termination_quote','assistant.delegation.terminate',
    }
    if operation == 'runtime.start':operation = 'runtime.status'
    if operation not in allowed or not isinstance(body['arguments'],dict):
        raise ValueError('Model bridge operation is outside the fixed assistant scope')
    if not isinstance(body['operationId'],str) or not 1 <= len(body['operationId']) <= 128:
        raise ValueError('Stable operationId required')
    client = LocalClient(Path('/epoch/assistant/resident'),timeout=30)
    status = client.call('runtime.status')
    from ioalab_sdk.runtime import RuntimeConfig
    from ioalab_sdk.runtime import require_configuration
    require_configuration(status['data'],RuntimeConfig.load(Path('/epoch/assistant/config.json')),assembly='ioalab-jiuwen')
    from ioalab_sdk.runtime import require_matching
    require_matching(status['data'].get('installation'),assembly='ioalab-jiuwen')
    return LocalClient(Path('/epoch/assistant/resident'),timeout=30,boot_id=status['data']['bootId']).call(
        operation,body['arguments'],operation_id=body['operationId'])


def main():
    import httpx
    parser = argparse.ArgumentParser()
    parser.add_argument('operation')
    parser.add_argument('--args-file',type=Path)
    parser.add_argument('--config',type=Path)
    args = parser.parse_args()
    if args.config and args.config.resolve() != Path('/epoch/native/.ioalab/settings.local.json'):
        parser.error('Only this trial Skill configuration is supported')
    try:
        if args.operation not in SAFE | {'runtime.start'} and not args.args_file:
            raise ValueError('Create a persistent arguments file before a mutating operation')
        values,operation_id = arguments(args.args_file)
        key = json.loads(Path('/epoch/native/model-bridge.json').read_text())['key']
    except (OSError,ValueError,TypeError):
        print(json.dumps({'status':'rejected','error':{'code':'invalid_input','message':'Use the original local arguments file; SDK configuration and secrets are not arguments'}}))
        return 1
    try:
        response = httpx.post('http://127.0.0.1:8767/model',json={'operation':args.operation,
            'arguments':values,'operationId':operation_id},headers={'Authorization':'Bearer '+key},timeout=35,trust_env=False)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError,ValueError):
        result = {'operationId':operation_id,'status':'unknown','error':{'code':'unknown_result',
            'message':'Original operation needs reconciliation; do not replace its operationId','recovery':'operations.get'}}
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get('status') in ('completed','waiting','accepted','running') else 1


if __name__ == '__main__':raise SystemExit(main())
