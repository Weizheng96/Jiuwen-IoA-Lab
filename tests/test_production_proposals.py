"""Delivery proposals use the original production delivery owner; only native S01 is scripted."""
from dataclasses import replace
import json

import pytest

from conftest import wait_for
from ioalab_sdk.runtime import SubmissionError
from submission_fixture import FixtureResult
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import QuoteHost,participants
from test_resident_fulfillment import invoke,operation


ORIGINAL={'result.txt':{'text':'actual delivery bytes','mediaType':'text/plain'}}
CHANGED={'result.txt':{'text':'different delivery bytes','mediaType':'text/plain'}}
SCHEMA={'type':'object','properties':{'files':{'type':'object','minProperties':1,'additionalProperties':{
    'type':'object','properties':{'mediaType':{'type':'string'},'text':{'type':'string'},'bytesBase64':{'type':'string'},'artifactPath':{'type':'string'}},
    'required':['mediaType'],'oneOf':[{'required':['text']},{'required':['bytesBase64']},{'required':['artifactPath']}],'additionalProperties':False}}},
    'required':['files'],'additionalProperties':False}


class DeliveryHost(QuoteHost):
    def __init__(self,mode):
        super().__init__();self.mode,self.production,self.schemas=mode,[],[]

    def execute(self,turn):
        if json.loads(turn.input)['phase']!='production':return super().execute(turn)
        assert turn.allow_native_tools is True
        self.turns.append(turn);self.production.append(turn);turn.on_session(turn.session_id)
        if self.mode in ('tool','tool_conflict','tool_error'):
            assert turn.on_tool('provider.task.deliver',{'files':ORIGINAL},'original-delivery-call')['status']=='delivered'
        return FixtureResult('unknown' if self.mode=='unknown' else 'completed',turn.session_id,{})

    def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        result=super().submit(op,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)
        if json.loads(input.text)['phase']!='production':return result
        self.schemas.append(execution.proposal_schema)
        if self.mode=='ordinary':return replace(result,output='我已经完成交付')
        proposal={'files':{}} if self.mode=='invalid' else {'files':CHANGED if self.mode=='tool_conflict' else ORIGINAL}
        error=SubmissionError('resource_unavailable','output','accepted','Original native output cannot be collected') if self.mode in ('error','tool_error') else None
        return replace(result,proposal=proposal,error=error)


def production_job(provider):
    rows=provider.scheduler.store.db.execute("SELECT state,result FROM runtime_jobs WHERE handler='provider.production'").fetchall()
    assert len(rows)==1
    return rows[0][0],json.loads(rows[0][1]) if rows[0][1] else {}


def settled(provider):
    rows=provider.scheduler.store.db.execute("SELECT state FROM runtime_jobs WHERE handler='provider.production'").fetchall()
    return rows and rows[0][0] in ('completed','unknown','failed','rejected') and provider.scheduler.status()['active']==0


@pytest.mark.parametrize('mode',['proposal','tool','tool_conflict','invalid','error','tool_error','unknown','ordinary'])
def test_delivery_tool_and_proposal_share_one_period_effect(platform,tmp_path,mode):
    host=DeliveryHost(mode)
    with participants(platform,tmp_path,agent_host=host) as (provider,assistant,_,template,task):
        quote=candidate(assistant,provider,template,task);selected,_,_=confirm(assistant,quote);ref=selected['data']['contractRef']
        wait_for(lambda:status(assistant,ref));assert status(assistant,ref)['data']['formed']
        key,_=invoke(assistant,ref,'start');wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:settled(provider))
        state,data=production_job(provider)
        endpoints=[value for value in provider.modules[0].execution.contexts.values() if value.store.all('cards')]
        assert len(endpoints)==1
        endpoint=endpoints[0]
        deliveries=[value for value in endpoint.store.all('production_calls').values() if value['name']=='provider.task.deliver']
        card=next(iter(endpoint.store.all('cards').values()))['card']
        delivered=mode in ('proposal','tool','tool_conflict','tool_error')
        assert len(host.production)==1 and host.schemas==[SCHEMA]
        assert len(deliveries)==(1 if delivered else 0) and len(card['signatures']['4'])==(1 if delivered else 0)
        assert not card['signatures']['7']
        if delivered:
            assert deliveries[0]['args']=={'files':ORIGINAL}
            wait_for(lambda:status(assistant,ref)['data']['availablePeriods']==1)
        if mode in ('proposal','tool'):
            assert state=='completed'
            return
        assert state=='unknown'
        if mode in ('tool_conflict','invalid','error','tool_error'):
            assert data['delivered'] is delivered
            assert data['error']['source']==('submission' if mode in ('error','tool_error') else 'proposal')
            assert data['error']['code']=={'tool_conflict':'id_conflict','invalid':'invalid_input','error':'resource_unavailable','tool_error':'resource_unavailable'}[mode]
        else:
            assert data['error']['code']=='unknown_result'
