"""Real A2C lifecycle gates business effects; external actions are explicit doubles."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import uuid
import json
import threading

import pytest
from a2c import objects
from ioalab_sdk.runtime.host import ToolBinding
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_delegation import participants
from test_resident_contracts import candidate,confirm,status
from test_resident_fulfillment import ProductionHost,configure_template,VERIFIER,invoke,operation
from test_assistant_role import user_decision
from conftest import wait_for


@pytest.mark.parametrize('unknown',[False,True])
def test_service_business_action_needs_original_user_step_runs_unlocked_and_never_replays_unknown(platform,tmp_path,monkeypatch,unknown):
    configure_template(monkeypatch);effects=[];checked=[];owner={}
    schema={'type':'object','properties':{'handle':{'type':'string'}},'required':['handle'],'additionalProperties':False}
    def register(args,context):
        endpoint=owner['runtime'].modules[0].execution.contexts[context['interactionId']]
        def acquire():
            acquired=endpoint.lock.acquire(timeout=1)
            if acquired:endpoint.lock.release()
            return acquired
        with ThreadPoolExecutor(max_workers=1) as pool:assert pool.submit(acquire).result(timeout=2)
        effects.append((args,context))
        if unknown:raise OSError('Explicit test double: external result lost after actual call')
        return {'status':'registered','explicitTestDouble':True}
    tool=ToolBinding('professional.register','执行已授权的专业环节',schema,register,'external','1',('real_name',))
    class Host(ProductionHost):
        def execute(self,turn):
            data=json.loads(turn.input)
            if data['phase']!='production':
                session=turn.session_id or 'isolated-business-'+str(uuid.uuid4())
                turn.on_session(session)
                denied=turn.on_tool(tool.name,{'handle':'shop'},'quote-cannot-register')
                assert denied['status']=='rejected' and not effects
                return super().execute(replace(turn,session_id=session))
            if not data['steps']:
                denied=turn.on_tool(tool.name,{'handle':'shop'},'before-user')
                assert denied['status']=='rejected' and denied['error']['code']=='requires_authorization' and not effects
                checked.append(True)
            else:
                assert data['steps'][-1]['answer']['granted']
                result=turn.on_tool(tool.name,{'handle':'shop'},'after-user')
                assert result['status']=='registered'
                assert turn.on_tool(tool.name,{'handle':'shop'},'after-user')==result
                with pytest.raises(RuntimeFault,match='input changed'):turn.on_tool(tool.name,{'handle':'changed'},'after-user')
            return super().execute(turn)
    with participants(platform,tmp_path,agent_host=Host(),verifier_source=VERIFIER,provider_tools={'service':{tool.name:tool}}) as (provider,assistant,host,template,task):
        owner['runtime']=provider
        catalog=LocalClient(provider.config.home).call('provider.tools.list',{'profile':'service'})
        assert catalog['status']=='completed' and catalog['data']['tools'][0]['requiresSteps']==['real_name']
        quote=candidate(assistant,provider,template,task);selected,_,_=confirm(assistant,quote);ref=selected['data']['contractRef']
        wait_for(lambda:status(assistant,ref));assert status(assistant,ref)['data']['formed']
        key,_=invoke(assistant,ref,'start');wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:status(assistant,ref)['data']['stepRequest'])
        assert checked==[True] and not effects
        request=status(assistant,ref)['data']['stepRequest']
        key,pending=invoke(assistant,ref,'answer_step',stepRequestDigest=objects.object_digest(request))
        user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        LocalClient(assistant.config.home).call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':objects.object_digest(request)},operation_id=key)
        wait_for(lambda:len(effects)==1)
        actual=effects[0][1]
        assert actual['contractRef']==ref and actual['period']==1 and actual['interactionId']==status(assistant,ref)['data']['interactionId']
        if unknown:
            wait_for(lambda:provider.scheduler.status()['states'].get('unknown')==1)
            assert not status(assistant,ref)['data']['card']['signatures']['4']
            assert not provider.scheduler.error
            threading.Event().wait(.3)
            assert len(effects)==1 and platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',status(assistant,ref)['data']['admissionId'])['state']=='committed'
        else:
            wait_for(lambda:status(assistant,ref)['data']['availablePeriods']==1)
            assert len(effects)==1
