"""The original host sees readable, stable, collision-free SDK tool names."""
from dataclasses import replace
from types import SimpleNamespace
import re

from ioalab_jiuwen.process import ProcessHost
from ioalab_sdk.runtime import (AgentDefinition,ToolBinding,BindingAck,HostInput,HostToolResult,
    SubmissionAttempt,execution_snapshot)


def test_aliases_are_readable_stable_and_route_to_the_exact_original_name(tmp_path,monkeypatch):
    originals=('provider.task.quote','provider.task.verifier.publish','collision.one','collision-one',
               'ioa_collision_one','VeryLong.'+'part_'*22)
    definition=AgentDefinition('names','Use declared tools',tuple(ToolBinding(name,name,{'type':'object'},lambda *_:None) for name in originals))
    requests=[];seen=[]
    class Client:
        async def run(self,request,**callbacks):
            requests.append(request)
            await callbacks['on_event']({'request_id':request['request_id'],'session_id':'actual-name-session'})
            declarations=request['host_tools'];names=[item['name'] for item in declarations]
            assert len(names)==len(set(names))==len(originals)
            assert names[:2]==['ioa_provider_task_quote','ioa_provider_task_verifier_publish']
            assert names[4]=='ioa_collision_one'
            assert all(re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',name) for name in names)
            assert request['agent']['tools']==['*']
            assert set(names)==set(request['permissions']['tools'])
            for index,name in enumerate(names):
                assert await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'actual-name-session','payload':{'name':name,'arguments':{'call_goal':'metadata'},'call_id':str(index)}})=={'accepted':originals[index]}
            return {'type':'result','request_id':request['request_id'],'status':'completed','session_id':'actual-name-session','exit_code':0}
    host=ProcessHost(['original-process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Client())
    execution=execution_snapshot(SimpleNamespace(agent_id='actor',profile='personal'),'interaction','job',definition,
        definition_digest='original-name-definition',version='1',workspace=str(tmp_path/'work'))
    def events(event):return BindingAck(event.result.session_id,event.result.request_id)
    def callback(call):
        seen.append((call.name,call.arguments))
        return HostToolResult({'accepted':call.name})
    for index,sid in enumerate((None,'actual-name-session')):
        result=host.submit('op-'+str(index),HostInput('input',attempt=SubmissionAttempt('attempt-'+str(index),'scope-'+str(index),'actor','personal')),
            sid,execution=execution,on_event=events,on_tool=callback)
        assert result.state=='completed' and result.terminal
    assert requests[0]['agent']==requests[1]['agent']
    assert [name for name,_ in seen]==list(originals)*2 and all(args=={} for _,args in seen)
