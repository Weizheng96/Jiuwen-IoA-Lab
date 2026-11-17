"""Check actual Process transport declarations against its reviewed upstream wheel."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from ioalab_jiuwen import ProcessHost
from ioalab_sdk.runtime import (BindingAck, HostDefinition, HostExecution, HostInput,
                               HostToolDeclaration, HostToolResult, SubmissionAttempt)


def test_process_inherits_configured_tools_using_reviewed_agent_spec(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'vendor/SOURCES.json').read_text(encoding='utf-8'))
    component = next(item for item in manifest['components'] if item['id'] == 'workswarm')
    wheel = root / 'vendor' / component['wheelInfo']['file']
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == component['wheelInfo']['sha256']
    program = '''import json,sys
sys.path.insert(0,sys.argv[1])
from jiuwenswarm.channels.process_cli.protocol.model import AgentSpec
try:
    result=AgentSpec.from_dict(json.load(sys.stdin))
    print(json.dumps({'accepted':True,'tools':result.to_dict()['tools']}))
except (TypeError,ValueError) as error:
    print(json.dumps({'accepted':False,'errorType':type(error).__name__}))
'''

    def validate(value):
        result = subprocess.run([sys.executable, '-I', '-c', program, str(wheel)],
                                input=json.dumps(value), text=True, capture_output=True, timeout=15, check=True)
        return json.loads(result.stdout)

    assert validate({'name': 'schema_probe', 'instructions': 'No model', 'tools': '*'}) == {
        'accepted': False, 'errorType': 'TypeError'}
    requests, effects = [], []

    class Process:
        async def run(self, request, **callbacks):
            requests.append(request)
            assert validate(request['agent']) == {'accepted': True, 'tools': ['*']}
            assert request['permissions']['tools'] == {request['host_tools'][0]['name']: 'allow'}
            record = {'type': 'result', 'request_id': request['request_id'], 'session_id': 'upstream-schema-session',
                      'status': 'completed', 'exit_code': 0}
            await callbacks['on_event']({**record, 'type': 'event'})
            await callbacks['on_tool_call']({**record, 'payload': {
                'name': request['host_tools'][0]['name'], 'arguments': {}, 'call_id': 'sdk-echo-call'}})
            return record

    host = ProcessHost(['public-process'], tmp_path)
    monkeypatch.setattr(host, '_client', lambda **_: Process())
    attempt = SubmissionAttempt('schema-attempt', 'original-scope', 'actor', 'profile')
    execution = HostExecution('actor', 'profile', 'business', 'interaction', 'job',
        HostDefinition('schema_probe', '1', 'original-definition', 'Use existing host abilities'),
        tools=(HostToolDeclaration('sdk.echo', 'Harmless controlled callback', {'type': 'object'}),),
        authorization={'nativeTools': {}, 'nativeToolsEnabled': True},
        deadline=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(), workspace=str(tmp_path / 'work'))

    def event(value):
        if value.kind == 'session': return BindingAck(value.result.session_id, value.result.request_id)

    def echo(call):
        effects.append(call.name)
        return HostToolResult(value={'echo': True})

    result = host.submit('schema-operation', HostInput('Echo once', attempt=attempt),
                         execution=execution, on_event=event, on_tool=echo)
    assert result.state == 'completed' and result.terminal is not None
    assert effects == ['sdk.echo'] and len(requests) == 1
