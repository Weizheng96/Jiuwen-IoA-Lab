"""Opt-in original sender/receiver models, actual SDK and local invitation effects."""
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

import pytest
from ioalab_jiuwen.tools import ConfiguredTools
from ioalab_sdk.runtime.files import private_json
from ioalab_sdk.runtime.host import AgentDefinition,HostTurn,ToolBinding
from ioalab_sdk.runtime.ipc import LocalClient
from test_assistant import appointment
from test_assistant_role import runtime_at
from test_business_tools import schedule,options,Capture
from model_rule_authoring import configure_with_original_model
from conftest import wait_for


ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_BUSINESS_DATA_DIR'),reason='Opt-in original sender and receiver models')
def test_original_sender_discovers_interface_and_reschedules_without_scripted_second_input(platform,tmp_path):
    from ioalab_jiuwen.process import ProcessHost
    def original():
        host=ProcessHost(json.loads(os.environ['IOALAB_REAL_BUSINESS_COMMAND']),os.environ['IOALAB_REAL_BUSINESS_DATA_DIR'],model=os.environ.get('IOALAB_REAL_BUSINESS_MODEL'))
        assert host.probe()['available'],host.capabilities()
        return host
    sender_host=original();receiver_host=original();author_host=original();receiver_turns=[];run_receiver=receiver_host.run
    def receive(turn):
        receiver_turns.append(turn)
        return run_receiver(turn)
    receiver_host.run=receive
    calendar=schedule(tmp_path/'calendar');initial=appointment()
    version=calendar.show();document=version['document']
    document['events']=[{'id':'busy','title':'PRIVATE-CALENDAR-TITLE','start':initial['start'],'end':initial['end']}]
    calendar.replace(document,version['revision'])
    capture=Capture(tmp_path/'smtp').start()
    tools=ConfiguredTools([{'factory':'calendar','profile':'personal','options':options(calendar,capture.port)}],
        ({'key':'personal','role':'assistant','name':'王经理的个人助手'},),home=tmp_path/'receiver/state',base_dir=tmp_path)
    trace=[];sessions=[];workflow=str(uuid.uuid4())
    try:
        with runtime_at(platform,tmp_path/'sender') as (sender,_,_,user):
            rpc=LocalClient(sender.config.home,timeout=35)
            with runtime_at(platform,tmp_path/'receiver',receiver_host,list(tools.for_profile('personal').values())) as (receiver,inbox,module,target):
                # Separate owner configuration Session: every declaration and
                # instruction is model-authored; approval remains an explicit driver.
                authoring=configure_with_original_model(receiver,inbox,author_host,user['username'],tmp_path/'owner-authoring')
                assert authoring['status']=='completed' and not receiver_turns
                schemas={
                    'assistant.messaging.interfaces':{'type':'object','properties':{'account':{'type':'string'}},'required':['account'],'additionalProperties':False},
                    'assistant.messaging.send':{'type':'object','properties':{'interfaceRef':{'type':'string'},'interactionRef':{'type':'string'},'input':{'type':'object'},'ttlSeconds':{'type':'integer'}},'required':['input'],'additionalProperties':False},
                    'assistant.messaging.status':{'type':'object','properties':{'messageRef':{'type':'string'},'waitSeconds':{'type':'integer','minimum':0,'maximum':30}},'required':['messageRef'],'additionalProperties':False}}
                def call(name,arguments,call_id):
                    assert name in schemas
                    # Stable retries within this request; the bridge never selects business input.
                    key=str(uuid.uuid5(uuid.NAMESPACE_URL,workflow+name+json.dumps(arguments,sort_keys=True,separators=(',',':'))))
                    result=rpc.call(name,arguments,operation_id=key)
                    trace.append({'name':name,'arguments':arguments,'callId':call_id,'result':result})
                    private_json(tmp_path/'sender-tools.json',trace)
                    return result
                bindings=tuple(ToolBinding(name,'IoA Lab SDK operation '+name,schema,lambda *_:None) for name,schema in schemas.items())
                flow=(ROOT/'src/access/skills/assistant/references/messaging.md').read_text(encoding='utf-8')
                definition=AgentDefinition('ioa_message_sender','按用户需求、授权范围和以下IoA Lab流程使用实际SDK工具。只依据真实回执决定下一步，不编造对方结果。\n'+flow,bindings)
                local_start=datetime.fromisoformat(initial['start']).astimezone(ZoneInfo('Asia/Shanghai'))
                day=local_start.strftime('%Y-%m-%d')
                request=f'请向王经理的平台账号{target["username"]}预约“{initial["title"]}”，优先北京时间{day} 10:00到10:30；如果冲突，可以按对方建议在当天09:00到12:00之间改约一次，保持30分钟，取得实际预约结果后告诉我。'
                result=sender_host.run(HostTurn(workflow,sender.identities[0].agent_id,workflow,definition,request,
                    str(tmp_path/'sender-model'),timeout_seconds=300,on_session=lambda sid:sessions.append(sid),on_tool=call))
                assert result.status=='completed',result
                sends={item['result']['data']['messageRef']:item for item in trace if item['name']=='assistant.messaging.send' and item['result']['status']=='accepted'}
                assert len(sends)==2,trace
                ids=list(sends);queries=[item['result']['data'] for item in trace if item['name']=='assistant.messaging.status' and item['result']['status']=='completed']
                first=next(value for value in queries if value['messageRef']==ids[0] and value.get('result'))
                second=next(value for value in queries if value['messageRef']==ids[1] and value.get('result'))
                assert first['result']['output']['status']=='conflict'
                assert second['result']['output']['status']=='invite_submitted' and second['result']['output']['calendar']=='awaiting_acceptance'
                assert first['interactionRef']==second['interactionRef']==sends[ids[1]]['arguments']['interactionRef']
                assert all(datetime.fromisoformat(sends[ids[0]]['arguments']['input'][key])==datetime.fromisoformat(initial[key]) for key in ('start','end'))
                proposed=sends[ids[1]]['arguments']['input'];suggestion=first['result']['output']
                assert all(datetime.fromisoformat(proposed[key])==datetime.fromisoformat(suggestion['suggested'+key.title()]) for key in ('start','end'))
                start=datetime.fromisoformat(proposed['start']).astimezone(ZoneInfo('Asia/Shanghai'));end=datetime.fromisoformat(proposed['end']).astimezone(ZoneInfo('Asia/Shanghai'))
                assert start.date()==local_start.date()==end.date() and start.hour>=9 and (end.hour,end.minute)<=(12,0)
                assert end-start==timedelta(minutes=30) and proposed['title']==initial['title']
                assert len(set(sessions))==1 and len({turn.session_id for turn in receiver_turns if turn.session_id})==1
                assert authoring['sessionId'] not in set(sessions)|{turn.session_id for turn in receiver_turns}
                assert len({turn.operation_id for turn in receiver_turns})==2
                wait_for(lambda:len(capture.messages)==1)
                assert 'PRIVATE-CALENDAR-TITLE' not in json.dumps([first,second])
                records=module.tools['calendar.book'].invoke.__self__.records()
                assert sum(value['state']=='submitted' for value in records.values())==1
                assert sum(value['state']=='conflict' for value in records.values())==1
                assert trace[0]['name']=='assistant.messaging.interfaces'
                first_read=next(i for i,item in enumerate(trace) if item['name']=='assistant.messaging.status' and ((item['result']['data'].get('result') or {}).get('output') or {}).get('status')=='conflict')
                second_send=next(i for i,item in enumerate(trace) if item is sends[ids[1]])
                assert first_read<second_send
                report={'scope':'Original owner-authoring, sender and receiver Process models; actual product workflows, published rules, autonomous rescheduling over resident HTTP/WS; local SMTP only',
                    'model':sender_host.capabilities()['model'],'interactionId':first['interactionRef'],'messageRefs':ids,
                    'senderSessionId':sessions[0],'receiverSessionId':next(turn.session_id for turn in receiver_turns if turn.session_id),
                    'first':first['result']['output'],'second':second['result']['output'],'smtpMessages':1,
                    'senderToolCalls':[item['name'] for item in trace],'secondBusinessInputWrittenBy':'original sender model',
                    'ownerRuleAuthoring':{'evidenceFile':authoring['evidenceFile'],'sessionId':authoring['sessionId'],
                        'productFlowDigest':authoring['productFlowDigest'],'toolCalls':authoring['toolCalls'],
                        'approvalPerformedBy':authoring['approval']['performedBy']},
                    'limits':['User approval uses an isolated automated driver through the real local confirmation endpoint',
                        'Typed SDK callback experiment, not native Skill import, frontend menu, external email or Outlook acceptance']}
                if os.environ.get('IOALAB_REAL_AUTONOMOUS_REPORT'):private_json(Path(os.environ['IOALAB_REAL_AUTONOMOUS_REPORT']),report)
    finally:tools.close();capture.close()
