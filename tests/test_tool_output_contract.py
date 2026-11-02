"""Public tool output contracts preserve receipts and stop unknown business effects."""
from dataclasses import replace
import json

import pytest
from jsonschema import FormatChecker

from a2c.codec import digest
from ioalab_jiuwen.process import ProcessHost
from ioalab_jiuwen.tools import ConfiguredTools
from ioalab_sdk.runtime.host import AgentDefinition,HostResult,HostTurn,ToolBinding,validate_output
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_assistant_role import INPUT,ScriptedHost,publish,runtime_at,send_and_read
from test_resident_delegation import QuoteHost,participants
from test_resident_contracts import candidate,confirm,status
from test_resident_fulfillment import invoke,operation
from conftest import wait_for


OUTPUT={'type':'object','properties':{'status':{'type':'string','enum':['actual'],
    'description':'Actual tool effect; never infer a different business state.'}},
    'required':['status'],'additionalProperties':False}


def binding(schema=OUTPUT,invoke=lambda *_:None):
    return ToolBinding('business','Owner-configured tool',{'type':'object'},invoke,'external',output_schema=schema)


@pytest.mark.parametrize('schema',[True,False,[],{'type':'unknown'},
    {'$ref':'https://external.invalid/schema'},
    {'properties':{'value':{'$dynamicRef':'external.json#value'}}},
    {'$ref':'#/$defs/missing'}, {'format':'unavailable-custom-format'}, {'description':'中'*6000}])
def test_invalid_or_nonlocal_output_schema_is_rejected_when_binding_is_created(schema):
    with pytest.raises(RuntimeFault) as error:binding(schema)
    assert error.value.code=='invalid_input'


def test_self_contained_contract_is_public_frozen_from_input_and_validates_without_conversion():
    schema={'type':'object','properties':{'status':{'$ref':'#/$defs/state'}},
            'required':['status'],'additionalProperties':False,'$defs':{'state':{'type':'string','enum':['actual']}}}
    tool=binding(schema);before=digest(tool.policy())
    result={'status':'actual'}
    assert validate_output(tool,result) is result
    assert tool.declaration()['output_schema']==schema==tool.policy()['output_schema']
    schema['$defs']['state']['enum'].append('invented')
    assert digest(tool.policy())==before
    disclosed=tool.declaration();disclosed['output_schema']['required'].clear()
    assert tool.output_schema['required']==['status']
    with pytest.raises(RuntimeFault) as error:validate_output(tool,{'status':'invented'})
    assert error.value.code=='invalid_tool_output' and error.value.recovery=='inspect_tool_effect'
    assert digest(replace(tool,output_schema={'type':'object'}).policy())!=before


def test_unprovided_contract_preserves_json_but_cannot_invent_serializability():
    tool=binding(None);result={'unchanged':['anything',None,False]}
    assert 'output_schema' not in tool.declaration() and validate_output(tool,result) is result
    for invalid in ({'non_json':object()},{'invalid_unicode':'\ud800'}):
        with pytest.raises(RuntimeFault) as error:validate_output(tool,invalid)
        assert error.value.code=='invalid_tool_output'


def test_date_time_contract_checks_real_rfc3339_instead_of_ignoring_format():
    tool=binding({'type':'object','properties':{'at':{'type':'string','format':'date-time'}},'required':['at']})
    valid={'at':'2026-11-02T18:00:00+08:00'}
    assert validate_output(tool,valid) is valid
    for value in ('not-a-date','2026-11-02','2026-02-30T18:00:00Z','2026-11-02T18:00:00'):
        with pytest.raises(RuntimeFault) as error:validate_output(tool,{'at':value})
        assert error.value.code=='invalid_tool_output'


def test_required_format_without_an_installed_checker_is_rejected_before_execution(monkeypatch):
    monkeypatch.delitem(FormatChecker.checkers,'date-time',raising=False)
    with pytest.raises(RuntimeFault) as error:binding({'type':'string','format':'date-time'})
    assert error.value.code=='invalid_input' and 'date-time' in str(error.value)


@pytest.mark.parametrize('result',[{'status':'actual'},{'status':'invented'}])
def test_direct_notification_uses_shared_validation_and_leaves_business_receipt_with_owner(tmp_path,result):
    observed={};effects=[]
    def notify(args,context):
        if context['operationId'] not in observed:
            effects.append(args);observed[context['operationId']]=result
        return observed[context['operationId']]
    configured=ConfiguredTools([],(),home=tmp_path/'state',base_dir=tmp_path)
    tool=binding(invoke=notify)
    message={'input':{},'context':{'agentId':'owner','operationId':'original'}}
    try:
        if result['status']=='actual':
            assert configured.notify(message,{'owner':tool}) is result
        else:
            for _ in range(2):
                with pytest.raises(RuntimeFault) as error:configured.notify(message,{'owner':tool})
                assert error.value.code=='invalid_tool_output'
        assert observed=={'original':result} and effects==[{}]
    finally:configured.close()


def test_native_host_keeps_supported_fields_exposes_output_in_description_and_hashes_contract(tmp_path,monkeypatch):
    requests=[]
    class Native:
        async def run(self,request,**callbacks):
            requests.append(request)
            for declaration in request['host_tools']:
                assert set(declaration)=={'name','description','input_schema'}
            await callbacks['on_event']({'session_id':'session-'+str(len(requests))})
            return {'status':'completed','session_id':'session-'+str(len(requests))}
    host=ProcessHost(['original-process'],tmp_path);host._capabilities={'available':True}
    monkeypatch.setattr(host,'_client',lambda **kwargs:Native())
    tool=binding()
    for index,selected in enumerate((tool,replace(tool,output_schema={'type':'object'}))):
        definition=AgentDefinition('business','Use real outputs',(selected,))
        result=host.run(HostTurn('op'+str(index),'actor','interaction',definition,'input',str(tmp_path/str(index))))
        assert result.status=='completed'
    description=requests[0]['host_tools'][0]['description']
    assert json.dumps(OUTPUT,ensure_ascii=False,sort_keys=True,separators=(',',':')) in description
    records=[json.loads(next((tmp_path/str(index)/'runs').glob('*.json')).read_text()) for index in (0,1)]
    assert records[0]['definitionDigest']!=records[1]['definitionDigest']
    assert tool.input_schema=={'type':'object'}


def test_assistant_preserves_invalid_receipt_poisoned_session_and_confirmed_contract(platform,tmp_path):
    effects=[];original={'status':'invented'}
    class Host(ScriptedHost):
        def run(self,turn):
            self.turns.append(turn);turn.on_session('actual-output-session')
            with pytest.raises(RuntimeFault) as error:turn.on_tool('reserve_slot',{'slot':'first'},'original')
            assert error.value.code=='invalid_tool_output'
            for name,args in [('reserve_slot',{'slot':'second'}),('ioa_reply',{'output':{'status':'actual'}})]:
                with pytest.raises(RuntimeFault) as blocked:turn.on_tool(name,args,'new-call')
                assert blocked.value.code=='unknown_result'
            return HostResult('completed','actual-output-session',{})
    host=Host()
    def reserve(args,context):effects.append(context);return original
    tool=ToolBinding('reserve_slot','Preserve output',INPUT,reserve,'reservation',output_schema=OUTPUT)
    with runtime_at(platform,tmp_path/'sender') as (_,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,module,target):
            assert inbox.call('assistant.tools.list')['data']['tools'][0]['output_schema']==OUTPUT
            publish(receiver,inbox,user['username'])
            version=module.rules.version('slot.request',1)
            assert version['declaration']['tools']==['reserve_slot']
            module.rules.tools[tool.name]=replace(tool,output_schema={'type':'object'})
            with pytest.raises(RuntimeFault) as changed:module.rules.version('slot.request',1)
            assert changed.value.code=='not_ready'
            module.rules.tools[tool.name]=tool
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            _,result=send_and_read(client,{'interfaceRef':interface,'input':{'slot':'first'}})
            assert result['state']=='unknown' and result['result']['error']['code']=='invalid_tool_output'
            wait_for(lambda:receiver.scheduler.status()['states'].get('unknown')==1)
            saved=[value for key,value in receiver.store.checkpoints(module.namespace).items() if key.startswith('tool:')]
            assert len(saved)==1 and saved[0]['observedResult']==original
            assert saved[0]['state']=='unknown' and saved[0]['error']['code']=='invalid_tool_output' and 'result' not in saved[0]
            again=client.call('assistant.messaging.send',{'interactionRef':result['interactionRef'],'input':{'slot':'second'}})
            assert again['status']=='accepted'
            wait_for(lambda:receiver.scheduler.status()['states'].get('queued')==1)
            assert len(effects)==len(host.turns)==1 and host.cancelled


def test_provider_preserves_invalid_receipt_and_blocks_delivery_without_reexecuting_business(platform,tmp_path):
    effects=[];original={'status':'invented'}
    def execute(args,context):effects.append(context);return original
    tool=binding(invoke=execute)
    class Host(QuoteHost):
        def run(self,turn):
            if json.loads(turn.input)['phase']!='production':return super().run(turn)
            self.turns.append(turn);turn.on_session(turn.session_id)
            with pytest.raises(RuntimeFault) as error:turn.on_tool(tool.name,{},'original')
            assert error.value.code=='invalid_tool_output'
            blocked=turn.on_tool('provider.task.deliver',{'files':{'result.txt':{'text':'not delivered','mediaType':'text/plain'}}},'after-invalid')
            assert blocked['status']=='rejected'
            assert turn.on_tool(tool.name,{},'retry-with-new-id')['status']=='rejected'
            return HostResult('completed',turn.session_id,{})
    with participants(platform,tmp_path,agent_host=Host(),provider_tools={'service':{tool.name:tool}}) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);selected,_,_=confirm(assistant,quote);ref=selected['data']['contractRef']
        wait_for(lambda:status(assistant,ref));assert status(assistant,ref)['data']['formed']
        key,_=invoke(assistant,ref,'start');wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:provider.scheduler.status()['states'].get('unknown')==1)
        assert len(effects)==1 and not status(assistant,ref)['data']['card']['signatures']['4']
        endpoint=provider.modules[0].execution.contexts[effects[0]['interactionId']]
        calls=list(endpoint.store.all('production_calls').values())
        saved=next(value for value in calls if value['name']==tool.name)
        assert len(calls)==1 and saved['observedResult']==original and saved['state']=='unknown' and 'result' not in saved
        assert saved['error']['code']=='invalid_tool_output'
        jobs=provider.scheduler.store.db.execute("SELECT result FROM runtime_jobs WHERE handler='provider.production'").fetchall()
        assert json.loads(jobs[0][0])['error']['code']=='invalid_tool_output'
