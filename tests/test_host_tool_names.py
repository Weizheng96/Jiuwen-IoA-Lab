"""The original host sees readable, stable, collision-free SDK tool names."""
from dataclasses import replace
import re

from ioalab_jiuwen.process import ProcessHost
from ioalab_sdk.runtime.host import AgentDefinition,HostTurn,ToolBinding


def test_aliases_are_readable_stable_and_route_to_the_exact_original_name(tmp_path,monkeypatch):
    originals=('provider.task.quote','provider.task.verifier.publish','collision.one','collision-one',
               'ioa_collision_one','VeryLong.'+'part_'*22)
    definition=AgentDefinition('names','Use declared tools',tuple(ToolBinding(name,name,{'type':'object'},lambda *_:None) for name in originals))
    requests=[];seen=[]
    class Client:
        async def run(self,request,**callbacks):
            requests.append(request)
            await callbacks['on_event']({'session_id':'actual-name-session'})
            declarations=request['host_tools'];names=[item['name'] for item in declarations]
            assert len(names)==len(set(names))==len(originals)
            assert names[:2]==['ioa_provider_task_quote','ioa_provider_task_verifier_publish']
            assert names[4]=='ioa_collision_one'
            assert all(re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',name) for name in names)
            assert set(request['agent']['tools'])==set(names)==set(request['permissions']['tools'])
            for index,name in enumerate(names):
                assert await callbacks['on_tool_call']({'session_id':'actual-name-session','payload':{'name':name,'arguments':{'call_goal':'metadata'},'call_id':str(index)}})=={'accepted':originals[index]}
            return {'status':'completed','session_id':'actual-name-session'}
    host=ProcessHost(['original-process'],tmp_path);host._capabilities={'available':True}
    monkeypatch.setattr(host,'_client',lambda **_:Client())
    turn=HostTurn('op','actor','interaction',definition,'input',str(tmp_path/'work'),
                  on_tool=lambda name,args,call:seen.append((name,args)) or {'accepted':name})
    assert host.run(turn).status=='completed'
    assert host.run(replace(turn,session_id='actual-name-session')).status=='completed'
    assert requests[0]['agent']==requests[1]['agent']
    assert [name for name,_ in seen]==list(originals)*2 and all(args=={} for _,args in seen)
