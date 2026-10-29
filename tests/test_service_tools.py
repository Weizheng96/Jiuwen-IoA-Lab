"""Unattended Swarm gets only the current job's typed SDK capabilities."""
import json
from swarm import SwarmEngine

def test_process_sdk_host_tools_are_scoped_and_keep_token_in_host(monkeypatch,tmp_path):
    import jiuwenswarm_sdk
    from ioalab_sdk.provider_capability import ProviderJob
    calls=[];seen=[]
    def call(capability,operation,body=None):
        assert capability.url=='http://127.0.0.1/job' and capability.token=='private-test-token'
        calls.append((operation,body));return {'status':'pass' if operation=='selftest' else 'delivered'}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def run(self,request,*,on_event,on_tool_call,cancel,deadline_seconds):
            seen.append(request)
            assert {t['name'] for t in request['host_tools']}==set(request['agent']['tools'])=={'ioa_inspect','ioa_quote','ioa_selftest','ioa_deliver'}
            assert 'private-test-token' not in json.dumps(request)
            for name,arguments in [('ioa_inspect',{}),('ioa_selftest',{'data':{'days':[]}}),('ioa_deliver',{'data':{'days':[]}})]:
                await on_event({'type':'event','event_type':'host_tool.requested','payload':{'name':name,'arguments':arguments}})
                await on_tool_call({'payload':{'name':name,'arguments':arguments}})
            return {'status':'completed','session_id':'real-protocol-test','usage':{'model_calls':3}}
    monkeypatch.setattr(jiuwenswarm_sdk,'Client',FakeClient);monkeypatch.setattr(ProviderJob,'call',call)
    result=SwarmEngine(['python']).run({'id':'job'}, {},tmp_path,'http://127.0.0.1/job','private-test-token')
    assert [c[0] for c in calls]==['inspect','selftest','deliver']
    assert result['usage']['model_calls']==3
    trace=(tmp_path/'swarm-events.jsonl').read_text(encoding='utf-8')
    assert 'ioa_deliver' in trace and 'private-test-token' not in trace
    assert json.loads((tmp_path/'swarm-result.json').read_text())['status']=='completed'


def test_line_fields_preserve_text_and_reject_overwrite():
    import pytest
    from swarm import assemble_text
    data={'format':'example'}
    lines=['class Engine:', '    value = "text"']
    result=assemble_text(data,[{'path':['engineSource'],'lines':lines},{'path':['files','index.js'],'lines':['const x = 1;']}])
    assert result['engineSource']=='class Engine:\n    value = "text"'
    assert result['files']['index.js']=='const x = 1;'
    assert data=={'format':'example'}
    for fields in ([{'path':['format'],'lines':['changed']}],[{'path':['format','nested'],'lines':[]}],[{'path':['x'],'lines':[1]}]):
        with pytest.raises(ValueError):assemble_text(data,fields)


def test_deliver_uses_only_last_passed_candidate(monkeypatch,tmp_path):
    import jiuwenswarm_sdk
    from ioalab_sdk.provider_capability import ProviderJob
    calls=[]
    def call(capability,operation,body=None):
        calls.append((operation,body));return {'status':('fail' if body.get('fail') else 'pass') if operation=='selftest' else 'delivered'}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def run(self,request,*,on_event,on_tool_call,**kwargs):
            await on_tool_call({'payload':{'name':'ioa_selftest','arguments':{'data':{'shop':'test'},'text_fields':[{'path':['engineSource'],'lines':['class Engine:', '    pass']}]}}})
            await on_tool_call({'payload':{'name':'ioa_deliver','arguments':{}}})
            await on_tool_call({'payload':{'name':'ioa_selftest','arguments':{'data':{'fail':True}}}})
            import pytest
            with pytest.raises(ValueError,match='No passed selftest'):
                await on_tool_call({'payload':{'name':'ioa_deliver','arguments':{}}})
            return {'status':'completed','usage':{}}
    monkeypatch.setattr(jiuwenswarm_sdk,'Client',FakeClient);monkeypatch.setattr(ProviderJob,'call',call)
    SwarmEngine(['python']).run({'id':'text-job'},{},tmp_path,'http://localhost/job','test-token')
    assert calls[0][0]=='selftest' and calls[1][0]=='deliver'
    assert calls[0][1]==calls[1][1] and calls[1][1]['engineSource']=='class Engine:\n    pass'
