"""Public adapter boundaries; explicit fake Process SDK, no model or UI proof."""
import asyncio
import json
from pathlib import Path
import pytest
from ioalab_jiuwen.process import ProcessHost
from ioalab_sdk.runtime.host import AgentDefinition, ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault


from dataclasses import replace
from types import SimpleNamespace
from ioalab_sdk.runtime import (BindingAck,HostDefinition,HostExecution,HostInput,
    HostToolDeclaration,HostToolResult,SubmissionAttempt)

def _submit(host,op,actor,iid,definition,text,workspace,*,session_id=None,on_session=None,on_tool=None,allow_native_tools=False):
    execution=HostExecution(actor,'profile',iid,iid,'job',
        HostDefinition(definition.name,'1','frozen',definition.instructions),
        workspace=workspace,kind='business' if allow_native_tools else 'observation',
        tools=tuple(HostToolDeclaration(t.name,t.description,t.input_schema,t.output_schema) for t in definition.tools),
        authorization={'nativeTools':dict(definition.native_tools),'nativeToolsEnabled':allow_native_tools})
    if len(definition.native_tools)!=len(dict(definition.native_tools)):
        # Neutral authorization is a mapping (duplicate keys cannot survive).
        # Exercise duplicate declarations at the actual S01 list boundary.
        execution=replace(execution,tools=execution.tools+execution.tools[:1])
    def event(value):
        if value.kind=='session':
            if on_session:on_session(value.result.session_id)
            return BindingAck(value.result.session_id,value.result.request_id)
    def tool(call):return HostToolResult(on_tool(call.name,call.arguments,call.call_id))
    result=host.submit(op,HostInput(text,attempt=SubmissionAttempt('attempt-'+op,'scope',actor,'profile')),
        session_id,execution=execution,on_event=event,on_tool=tool)
    return SimpleNamespace(status=result.state,session_id=result.session_id,request_id=result.request_id,
        error={'code':result.error.code} if result.error else None)

def test_local_tool_alias_is_scoped_and_actual_session_saved_before_dispatch(tmp_path, monkeypatch):
    events = []
    requests = []

    class Process:

        async def run(self, request, **callbacks):
            requests.append(request)
            name = request['host_tools'][0]['name']
            assert request['agent']['tools'] == ['*'] and '.' not in name
            assert 'call_goal' in request['host_tools'][0]['input_schema']['properties']
            await callbacks['on_event']({'session_id': 'real-public-session', 'request_id': request['request_id']})
            result = await callbacks['on_tool_call']({'session_id': 'real-public-session', 'payload': {'name': name, 'arguments': {'text': 'hello', 'call_goal': 'native display-only intent'}, 'call_id': 'native-call'}, 'request_id': request['request_id']})
            assert result == {'ok': True}
            return {'status': 'completed', 'session_id': 'real-public-session', 'usage': {'tokens': 1}, 'type': 'result', 'request_id': request['request_id'], 'exit_code': 0}
    host = ProcessHost(['original-process'], tmp_path)
    host._policy = {}
    monkeypatch.setattr(host, '_client', lambda **kw: Process())

    def session(value):
        events.append(('session', value))

    def tool(name, args, call):
        assert events[0] == ('session', 'real-public-session')
        record = json.loads((tmp_path / 'work/runs' / (requests[-1]['request_id'] + '.json')).read_text())
        assert record['state'] == 'running' and record['sessionId'] == 'real-public-session'
        events.append(('tool', name, args, call))
        return {'ok': True}
    definition = AgentDefinition('inbox', 'Only local.notice is authorized', (ToolBinding('local.notice', 'Display text', {'type': 'object'}, lambda *_: None, 'notification'),))
    for index in range(2):
        result = _submit(host, 'original-operation-'+str(index), 'actor', 'interaction', definition, 'data', str(tmp_path / 'work'), session_id='real-public-session' if index else None, on_session=session, on_tool=tool)
        assert result.status == 'completed' and result.session_id == 'real-public-session'
    assert events[1] == ('tool', 'local.notice', {'text': 'hello'}, 'native-call')
    assert requests[0]['request_id'] != requests[1]['request_id'] and requests[1]['session_id'] == 'real-public-session'
    assert 'Local tool aliases' in requests[0]['agent']['instructions']
    assert definition.tools[0].input_schema == {'type': 'object'}

def test_explicit_business_call_goal_is_preserved_instead_of_silently_stripped(tmp_path, monkeypatch):

    class Process:

        async def run(self, request, **callbacks):
            await callbacks['on_event']({'session_id': 'original-session', 'request_id': request['request_id']})
            result = await callbacks['on_tool_call']({'session_id': 'original-session', 'payload': {'name': 'business', 'arguments': {'call_goal': 'business value'}, 'call_id': 'original-call'}, 'request_id': request['request_id']})
            assert result == {'call_goal': 'business value'}
            return {'status': 'completed', 'session_id': 'original-session', 'type': 'result', 'request_id': request['request_id'], 'exit_code': 0}
    host = ProcessHost(['original-process'], tmp_path)
    host._policy = {}
    monkeypatch.setattr(host, '_client', lambda **kwargs: Process())
    schema = {'type': 'object', 'properties': {'call_goal': {'type': 'string'}}, 'required': ['call_goal'], 'additionalProperties': False}
    definition = AgentDefinition('business', 'Use the explicit business parameter', (ToolBinding('business', 'Business input', schema, lambda *_: None),))
    result = _submit(host, 'op', 'actor', 'iid', definition, 'data', str(tmp_path / 'work'), on_tool=lambda name, args, call: args)
    assert result.status == 'completed' and definition.tools[0].input_schema == schema

def test_lost_native_result_preserves_actual_session_and_never_retries(tmp_path, monkeypatch):

    class Process:

        async def run(self, request, **callbacks):
            await callbacks['on_event']({'session_id': 'saved-before-effect', 'request_id': request['request_id']})
            raise OSError('private transport details')
    host = ProcessHost(['original-process'], tmp_path)
    host._policy = {}
    monkeypatch.setattr(host, '_client', lambda **kw: Process())
    result = _submit(host, 'op', 'actor', 'iid', AgentDefinition('inbox', 'Read data', ()), 'data', str(tmp_path / 'work'))
    assert result.status == 'unknown' and result.session_id == 'saved-before-effect'
    record = json.loads(next((tmp_path / 'work/runs').glob('ioa-*.json')).read_text())
    assert record['state'] == 'unknown' and record['sessionId'] == 'saved-before-effect'
    assert 'private transport details' not in json.dumps(record)
    assert host.cancel(result.request_id)['ended'] is False

def test_native_tools_keep_configured_permissions_in_every_phase(tmp_path, monkeypatch):
    requests = []

    class Process:

        async def run(self, request, **callbacks):
            requests.append(request)
            assert request['permissions']['tools']['business'] == 'allow'
            assert 'ask_user' not in request['permissions']['tools']
            await callbacks['on_event']({'session_id': 'same-original-session', 'request_id': request['request_id']})
            await callbacks['on_tool_call']({'session_id': 'same-original-session', 'payload': {'name': 'business', 'arguments': {}, 'call_id': 'original-call'}, 'request_id': request['request_id']})
            return {'status': 'completed', 'session_id': 'same-original-session', 'type': 'result', 'request_id': request['request_id'], 'exit_code': 0}
    host = ProcessHost(['original-process'], tmp_path)
    host._policy = {'write_file': 'ask'}
    monkeypatch.setattr(host, '_client', lambda **kwargs: Process())
    definition = AgentDefinition('business', 'Use only this task', (ToolBinding('business', 'A stage-checked callback', {'type': 'object'}, lambda *_: None),), native_tools=(('write_file', 'allow'),))
    for enabled in (False, True):
        result = _submit(host, 'op-'+str(enabled), 'actor', 'iid', definition, 'data', str(tmp_path / 'work'), session_id='same-original-session', on_tool=lambda *args: {'rejected': 'actual role/user gate still owns this action'}, allow_native_tools=enabled)
        assert result.status == 'completed'
    assert requests[0]['agent'] == requests[1]['agent']
    # The adapter never overwrites the Runtime's native permissions.
    assert requests[0]['permissions']['tools'] == {'business': 'allow'}
    assert requests[1]['permissions']['tools'] == {'business': 'allow'}
    assert requests[0]['agent']['tools'] == ['*']
    records = [json.loads(path.read_text()) for path in (tmp_path / 'work/runs').glob('ioa-*.json')]
    assert len({value['definitionDigest'] for value in records}) == 1
    host._policy['write_file'] = 'deny'
    result = _submit(host, 'op-new', 'actor', 'iid', definition, 'data', str(tmp_path / 'work'),
                     on_tool=lambda *args: {'rejected': 'SDK business authorization still applies'})
    assert result.status == 'completed'
    assert len(requests) == 3 and 'write_file' not in requests[-1]['permissions']['tools']

@pytest.mark.parametrize('native', [(('*', 'allow'),), (('ask_user', 'allow'),), (('business', 'allow'),), (('write_file', 'allow'), ('write_file', 'ask')), (('skill_tool', 'allow'),)])
def test_invalid_or_unavailable_native_tool_does_not_start_a_process(tmp_path, monkeypatch, native):
    host = ProcessHost(['original-process'], tmp_path)
    host._policy = {}
    monkeypatch.setattr(host, '_client', lambda **kwargs: pytest.fail('invalid local tools started a process'))
    definition = AgentDefinition('business', 'Read data', (ToolBinding('business', 'SDK callback', {'type': 'object'}, lambda *_: None),), native_tools=native)
    result = _submit(host, 'op', 'actor', 'iid', definition, 'data', str(tmp_path / 'work'))
    assert result.error['code'] == 'invalid_input'

def _startable(tmp_path):
    command=tmp_path/'bin'/'jiuwenswarm-process.exe';command.parent.mkdir();command.write_bytes(b'')
    data=tmp_path/'swarm';(data/'config').mkdir(parents=True);(data/'config'/'config.yaml').write_text('models: {}',encoding='utf-8')
    return command,data


@pytest.mark.parametrize('broken,state',[(None,'available'),('command','unavailable'),('data','unavailable'),('config','unavailable')])
def test_check_judges_startability_without_starting_process_or_model(tmp_path,monkeypatch,broken,state):
    command,data=_startable(tmp_path)
    if broken=='command':command.unlink()
    if broken=='config':(data/'config'/'config.yaml').unlink()
    host=ProcessHost([str(command),'--flag'],tmp_path/'missing' if broken=='data' else data)
    monkeypatch.setattr(host,'_client',lambda **kwargs:pytest.fail('S02 started the original Process'))
    value=host.check()
    assert value['state']==state and value['basis']


def test_check_reports_busy_for_own_running_request_and_unknown_for_unfinished_self_check(tmp_path,monkeypatch):
    import threading
    from ioalab_jiuwen import submission
    command,data=_startable(tmp_path)
    host=ProcessHost([str(command)],data)
    monkeypatch.setattr(host,'_client',lambda **kwargs:pytest.fail('S02 started the original Process'))
    host._running['operation']=[object()]
    assert host.check()['state']=='busy'
    host._running.clear()
    release=threading.Event()
    monkeypatch.setattr(submission,'CHECK_SECONDS',0.05)
    monkeypatch.setattr(host,'_startable',lambda:release.wait(5) or {'state':'available','basis':'late'})
    try:assert host.check()['state']=='unknown'
    finally:release.set()
    def unreadable():raise OSError('network drive')
    monkeypatch.setattr(host,'_startable',unreadable)
    assert host.check()['state']=='unknown'


def test_unlisted_native_tool_keeps_actual_runtime_failure_instead_of_claiming_a_catalog(tmp_path, monkeypatch):
    requests = []

    class Process:

        async def run(self, request, **callbacks):
            requests.append(request)
            return {'status': 'failed', 'error': {'code': 'TOOL_NOT_FOUND'}, 'type': 'result', 'request_id': request['request_id'], 'exit_code': 1}
    host = ProcessHost(['original-process'], tmp_path)
    host._policy = {}
    monkeypatch.setattr(host, '_client', lambda **kwargs: Process())
    result = _submit(host, 'op', 'actor', 'iid', AgentDefinition('business', 'Use owner-configured tool', (), native_tools=(('custom_business_tool', 'allow'),)), 'data', str(tmp_path / 'work'), allow_native_tools=True)
    assert result.status == 'unknown' and result.error['code'] == 'TOOL_NOT_FOUND'
    assert len(requests) == 1 and requests[0]['permissions']['tools'] == {}
    assert requests[0]['agent']['tools'] == ['*']
