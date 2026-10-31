"""Public adapter boundaries; explicit fake Process SDK, no model or UI proof."""
import asyncio
import json
from pathlib import Path

import pytest

from ioalab_jiuwen.process import ProcessHost
from ioalab_sdk.runtime.host import AgentDefinition,HostTurn,ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault


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


def test_native_tools_have_fixed_definition_but_only_production_permissions(tmp_path,monkeypatch):
    requests=[]
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            assert request['permissions']['tools']['business']=='allow'
            assert 'ask_user' not in request['permissions']['tools']
            await callbacks['on_event']({'session_id':'same-original-session'})
            await callbacks['on_tool_call']({'session_id':'same-original-session','payload':{'name':'business','arguments':{},'call_id':'original-call'}})
            return {'status':'completed','session_id':'same-original-session'}
    host=ProcessHost(['original-process'],tmp_path);host._capabilities={'available':True,'nativeToolPolicy':{'write_file':'ask'}}
    monkeypatch.setattr(host,'_client',lambda **kwargs:Process())
    definition=AgentDefinition('business','Use only this task',
        (ToolBinding('business','A stage-checked callback',{'type':'object'},lambda *_:None),),native_tools=(('write_file','allow'),))
    for enabled in (False,True):
        result=host.run(HostTurn('op','actor','iid',definition,'data',str(tmp_path/'work'),session_id='same-original-session',
            on_tool=lambda *args:{'rejected':'actual role/user gate still owns this action'},allow_native_tools=enabled))
        assert result.status=='completed'
    assert requests[0]['agent']==requests[1]['agent']
    assert requests[0]['permissions']['tools']['write_file']=='deny'
    assert requests[1]['permissions']['tools']['write_file']=='allow'
    assert requests[0]['agent']['tools']==['business','write_file']
    records=[json.loads(path.read_text()) for path in (tmp_path/'work/runs').glob('*.json')]
    assert len({value['definitionDigest'] for value in records})==1
    host._capabilities['nativeToolPolicy']['write_file']='deny'
    with pytest.raises(RuntimeFault,match='originally configured'):host.run(HostTurn('op','actor','iid',definition,'data',str(tmp_path/'work')))
    assert len(requests)==2


@pytest.mark.parametrize('native',[(('*','allow'),),(('ask_user','allow'),),(('business','allow'),),(('write_file','allow'),('write_file','ask')),(('skill_tool','allow'),)])
def test_invalid_or_unavailable_native_tool_does_not_start_a_process(tmp_path,monkeypatch,native):
    host=ProcessHost(['original-process'],tmp_path);host._capabilities={'available':True,'nativeToolPolicy':{}}
    monkeypatch.setattr(host,'_client',lambda **kwargs:pytest.fail('invalid local tools started a process'))
    definition=AgentDefinition('business','Read data',(ToolBinding('business','SDK callback',{'type':'object'},lambda *_:None),),native_tools=native)
    with pytest.raises(RuntimeFault):host.run(HostTurn('op','actor','iid',definition,'data',str(tmp_path/'work')))


@pytest.mark.parametrize('failure',[None,'run_permissions','policy'])
def test_readiness_requires_run_permissions_and_an_actual_public_policy_snapshot(tmp_path,monkeypatch,failure):
    queried=[]
    class Process:
        async def query(self,name,**kwargs):
            queried.append(name)
            if name=='protocol.capabilities':
                return {'status':'completed','data':{'features':{key:key!=failure for key in ('custom_agent','host_tools','session_resume','interaction_callback','run_permissions')}}}
            if name=='model.list':return {'status':'completed','data':{'models':[{'selection_key':'actual-model'}],'current_selection':'actual-model'}}
            assert name=='permission.get'
            return {'status':'failed' if failure=='policy' else 'completed','data':{'effective':{'tools':[{'name':'write_file','level':'deny'}]}}}
    host=ProcessHost(['original-process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **kwargs:Process())
    result=host.probe()
    assert result['available']==(failure is None)
    assert result['nativeToolPolicy']=={'write_file':'deny'}
    assert queried==['protocol.capabilities','model.list','permission.get']


def test_unlisted_native_tool_keeps_actual_runtime_failure_instead_of_claiming_a_catalog(tmp_path,monkeypatch):
    requests=[]
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            return {'status':'failed','error':{'code':'TOOL_NOT_FOUND'}}
    host=ProcessHost(['original-process'],tmp_path);host._capabilities={'available':True,'nativeToolPolicy':{}}
    monkeypatch.setattr(host,'_client',lambda **kwargs:Process())
    result=host.run(HostTurn('op','actor','iid',AgentDefinition('business','Use owner-configured tool',(),native_tools=(('custom_business_tool','allow'),)),
        'data',str(tmp_path/'work'),allow_native_tools=True))
    assert result.status=='failed' and result.error['code']=='TOOL_NOT_FOUND'
    assert len(requests)==1 and requests[0]['permissions']['tools']['custom_business_tool']=='allow'
