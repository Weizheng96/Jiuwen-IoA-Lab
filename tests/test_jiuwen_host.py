"""Public adapter boundaries; explicit fake Process SDK, no model or UI proof."""
import asyncio
import json
from pathlib import Path

import pytest

from ioalab_jiuwen.process import ProcessHost
from ioalab_sdk.runtime.host import AgentDefinition,HostTurn,ToolBinding


def test_local_tool_alias_is_scoped_and_actual_session_saved_before_dispatch(tmp_path,monkeypatch):
    events=[];requests=[]
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            name=request['host_tools'][0]['name']
            assert name==request['agent']['tools'][0] and '.' not in name
            assert 'call_goal' in request['host_tools'][0]['input_schema']['properties']
            await callbacks['on_event']({'session_id':'real-public-session'})
            result=await callbacks['on_tool_call']({'session_id':'real-public-session','payload':{'name':name,'arguments':{'text':'hello','call_goal':'native display-only intent'},'call_id':'native-call'}})
            assert result=={'ok':True}
            return {'status':'completed','session_id':'real-public-session','usage':{'tokens':1}}
    host=ProcessHost(['original-process'],tmp_path)
    host._capabilities={'available':True}
    monkeypatch.setattr(host,'_client',lambda **kw:Process())
    def session(value):events.append(('session',value))
    def tool(name,args,call):
        assert events[0]==('session','real-public-session')
        record=json.loads((tmp_path/'work/runs'/(requests[-1]['request_id']+'.json')).read_text())
        assert record['state']=='running' and record['sessionId']=='real-public-session'
        events.append(('tool',name,args,call))
        return {'ok':True}
    definition=AgentDefinition('inbox','Only local.notice is authorized',(ToolBinding('local.notice','Display text',{'type':'object'},lambda *_:None,'notification'),))
    for index in range(2):
        result=host.run(HostTurn('original-operation','actor','interaction',definition,'data',str(tmp_path/'work'),
                                 session_id='real-public-session' if index else None,on_session=session,on_tool=tool))
        assert result.status=='completed' and result.session_id=='real-public-session'
    assert events[1]==('tool','local.notice',{'text':'hello'},'native-call')
    assert requests[0]['request_id']!=requests[1]['request_id'] and requests[1]['session_id']=='real-public-session'
    assert 'Local tool aliases' in requests[0]['agent']['instructions']
    assert definition.tools[0].input_schema=={'type':'object'}


def test_explicit_business_call_goal_is_preserved_instead_of_silently_stripped(tmp_path,monkeypatch):
    class Process:
        async def run(self,request,**callbacks):
            await callbacks['on_event']({'session_id':'original-session'})
            result=await callbacks['on_tool_call']({'session_id':'original-session','payload':{'name':'business','arguments':{'call_goal':'business value'},'call_id':'original-call'}})
            assert result=={'call_goal':'business value'}
            return {'status':'completed','session_id':'original-session'}
    host=ProcessHost(['original-process'],tmp_path);host._capabilities={'available':True}
    monkeypatch.setattr(host,'_client',lambda **kwargs:Process())
    schema={'type':'object','properties':{'call_goal':{'type':'string'}},'required':['call_goal'],'additionalProperties':False}
    definition=AgentDefinition('business','Use the explicit business parameter',(ToolBinding('business','Business input',schema,lambda *_:None),))
    result=host.run(HostTurn('op','actor','iid',definition,'data',str(tmp_path/'work'),on_tool=lambda name,args,call:args))
    assert result.status=='completed' and definition.tools[0].input_schema==schema


def test_lost_native_result_preserves_actual_session_and_never_retries(tmp_path,monkeypatch):
    class Process:
        async def run(self,request,**callbacks):
            await callbacks['on_event']({'session_id':'saved-before-effect'})
            raise OSError('private transport details')
    host=ProcessHost(['original-process'],tmp_path)
    host._capabilities={'available':True}
    monkeypatch.setattr(host,'_client',lambda **kw:Process())
    result=host.run(HostTurn('op','actor','iid',AgentDefinition('inbox','Read data',()),'data',str(tmp_path/'work')))
    assert result.status=='unknown' and result.session_id=='saved-before-effect'
    record=json.loads(next((tmp_path/'work/runs').glob('*.json')).read_text())
    assert record['state']=='unknown' and record['sessionId']=='saved-before-effect'
    assert 'private transport details' not in json.dumps(record)
    assert not host.cancel('op')
