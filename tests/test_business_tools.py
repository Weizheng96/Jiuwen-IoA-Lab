"""Installed local factories and real WS/SMTP; model doubles are explicit."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid

import pytest
from ioalab_jiuwen.tools import ConfiguredTools
from ioalab_tools.appointments import Appointments,INPUT
from ioalab_tools.schedule import LocalSchedule
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_assistant import appointment
from test_assistant_role import runtime_at,user_decision,send_and_read
from conftest import wait_for
from smtp_capture import Capture


def uid():return str(uuid.uuid4())


def schedule(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    calendar=LocalSchedule(tmp_path/'schedule.json');calendar.init();return calendar


def options(calendar,port=1025):
    return {'calendar':{'backend':'local','schedule_file':str(calendar.path)},
            'notification':{'transport':'smtp-capture','host':'127.0.0.1','port':port,'sender':'notice@example.test'},
            'recipient':'owner@example.test'}


def context(**updates):
    return {'operationId':uid(),'agentId':'owner-agent','messageId':uid(),'interactionId':uid(),'sessionId':'actual-test-session',**updates}


def test_installed_factory_is_explicit_scoped_and_has_no_business_side_effect(tmp_path):
    calendar=schedule(tmp_path);before={p.name for p in tmp_path.iterdir()}
    identities=({'key':'personal','role':'assistant','name':'owner'},{'key':'service','role':'service','name':'service'})
    tools=ConfiguredTools([{'factory':'calendar','profile':'personal','options':options(calendar)}],identities,home=tmp_path/'sdk',base_dir=tmp_path)
    try:
        assert set(tools.for_profile('personal'))=={'calendar.book','calendar.status'}
        assert tools.for_profile('service')=={}
        assert {p.name for p in tmp_path.iterdir()}==before
        declarations=json.dumps([tool.policy() for tool in tools.for_profile('personal').values()])
        assert 'owner@example.test' not in declarations and 'password' not in declarations
    finally:tools.close()
    for spec in ({'factory':'module:run','profile':'personal','options':{}},
                 {'factory':'not-installed','profile':'personal','options':{}},
                 {'factory':'calendar','profile':'other','options':options(calendar)},
                 {'factory':'calendar','profile':'personal','options':{**options(calendar),'requires_steps':['real_name']}}):
        with pytest.raises(RuntimeFault):ConfiguredTools([spec],identities,home=tmp_path/'sdk',base_dir=tmp_path)


def test_unknown_invitation_preserves_reservation_and_never_resends_or_changes_original_input(tmp_path):
    class Notification:
        calls=0
        def send(self,*args):self.calls+=1;raise TimeoutError('An external effect may already have happened')
    calendar=schedule(tmp_path);notice=Notification();service=Appointments(calendar,notice,'owner@example.test')
    payload=appointment();original=context()
    with pytest.raises(RuntimeFault,match='automatic resend disabled'):service.book(payload,original)
    with pytest.raises(RuntimeFault,match='automatic resend'):service.book(payload,original)
    with pytest.raises(RuntimeFault,match='input changed'):service.book({**payload,'title':'different'},original)
    with pytest.raises(RuntimeFault,match='unresolved invitation'):service.book(payload,{**original,'operationId':uid(),'messageId':uid()})
    record=service.records()[original['operationId']]
    assert record['state']=='unknown' and notice.calls==1
    status=service.status({'reservationId':record['reservationId']},original)
    assert status['status']=='unknown' and status['inviteSubmitted'] is None
    with pytest.raises(RuntimeFault):service.status({'reservationId':record['reservationId']},context())


def test_concurrent_reservation_is_visible_before_notification_returns(tmp_path):
    entered=threading.Event();release=threading.Event()
    class Notification:
        calls=0
        def send(self,*args):
            self.calls+=1;entered.set();assert release.wait(10)
            return {'status':'invite_submitted','uid':args[0],'testDouble':True}
    calendar=schedule(tmp_path);notice=Notification()
    first=Appointments(calendar,notice,'owner@example.test');second=Appointments(LocalSchedule(calendar.path),notice,'owner@example.test')
    payload=appointment();original=context()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(first.book,payload,original)
        try:
            assert entered.wait(3)
            assert first.records()[original['operationId']]['state']=='sending'
            denied=second.book(payload,context())
            assert denied['status']=='conflict' and denied['inviteSubmitted'] is False and notice.calls==1
        finally:release.set()
        receipt=pending.result(timeout=5)
    assert receipt['status']=='invite_submitted' and receipt['calendar']=='awaiting_acceptance'
    assert first.book(payload,original)==receipt and notice.calls==1


class CalendarHost:
    def __init__(self):self.turns=[];self.contexts=[]
    def capabilities(self):return {'available':True,'explicitTestDouble':True}
    def cancel(self,operation):return True
    def run(self,turn):
        self.turns.append(turn)
        session=turn.session_id or 'isolated-calendar-'+uid();turn.on_session(session)
        payload=json.loads(turn.input.split('\n',1)[1])['input']
        actual=turn.on_tool('calendar.book',payload,'original-book')
        assert turn.on_tool('calendar.book',payload,'original-book')==actual
        assert turn.on_tool('calendar.book',payload,'model-repeated-book')==actual
        turn.on_tool('ioa_reply',{'output':actual},'original-reply')
        return HostResult('completed',session,{'explicitTestDouble':True})


@pytest.mark.parametrize('real',[False,True])
def test_resident_meeting_conflict_retry_same_uuid_session_and_real_smtp_capture(platform,tmp_path,real):
    import os
    if real and not os.environ.get('IOALAB_REAL_BUSINESS_DATA_DIR'):pytest.skip('requires explicitly configured original Jiuwen')
    calendar=schedule(tmp_path/'calendar');payload=appointment()
    initial=calendar.show();document=initial['document']
    document['events']=[{'id':'busy','title':'PRIVATE-CALENDAR-TITLE','start':payload['start'],'end':payload['end']}]
    calendar.replace(document,initial['revision'])
    capture=Capture(tmp_path/'smtp').start();host=CalendarHost()
    if real:
        from ioalab_jiuwen.process import ProcessHost
        original=ProcessHost(json.loads(os.environ['IOALAB_REAL_BUSINESS_COMMAND']),os.environ['IOALAB_REAL_BUSINESS_DATA_DIR'],model=os.environ.get('IOALAB_REAL_BUSINESS_MODEL'))
        assert original.probe()['available']
        class RecordingHost:
            def __init__(self):self.turns=[]
            def capabilities(self):return original.capabilities()
            def cancel(self,operation):return original.cancel(operation)
            def run(self,turn):self.turns.append(turn);return original.run(turn)
        host=RecordingHost()
    tools=ConfiguredTools([{'factory':'calendar','profile':'personal','options':options(calendar,capture.port)}],
                          ({'key':'personal','role':'assistant','name':'owner'},),home=tmp_path/'receiver/state',base_dir=tmp_path)
    output={'type':'object','properties':{
        'status':{'type':'string','enum':['conflict','invite_submitted']},
        'reason':{'type':'string','enum':['busy','outside_rules']},
        'suggestedStart':{'type':'string','format':'date-time'},'suggestedEnd':{'type':'string','format':'date-time'},
        'timeZone':{'type':'string','minLength':1},'calendar':{'type':'string','enum':['awaiting_acceptance']},
        'reservationId':{'type':'string','minLength':1},'inviteSubmitted':{'type':'boolean'}},
        'required':['status','inviteSubmitted'],'additionalProperties':False}
    try:
        with runtime_at(platform,tmp_path/'sender') as (sender,client,_,user):
            with runtime_at(platform,tmp_path/'receiver',host,list(tools.for_profile('personal').values())) as (receiver,inbox,module,target):
                listed=inbox.call('assistant.tools.list')['data']['tools']
                assert {tool['name'] for tool in listed}=={'calendar.book','calendar.status'}
                draft=inbox.call('assistant.rules.draft',{'declaration':{'name':'meeting.request','description':'预约会议',
                    'inputSchema':INPUT,'outputSchema':output,'allowedSenders':[user['username']],'tools':['calendar.book']},
                    'instructions':'每条请求只按原input调用一次calendar.book，将工具的完整返回值原样作为ioa_reply输出；冲突仅返回建议，等待发信方重约。'})
                assert draft['status']=='completed',draft
                preview=inbox.call('assistant.rules.preview',{'draftRef':draft['data']['draftRef']})['data']
                action=uid();args={'draftRef':draft['data']['draftRef'],'contentDigest':preview['contentDigest'],'expectedRevision':0}
                waiting=inbox.call('assistant.rules.publish',args,operation_id=action)
                assert waiting['status']=='waiting'
                user_decision(receiver,waiting['data']['confirmationRef'])
                assert inbox.call('assistant.rules.publish',args,operation_id=action)['status']=='completed'
                interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
                def send(args,action):
                    sent,value=send_and_read(client,args,operation=action)
                    if not value.get('result'):
                        completed=[]
                        def arrived():
                            value=client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef'],'waitSeconds':2})['data']
                            if value.get('result'):completed.append(value);return True
                            return False
                        wait_for(arrived,timeout=120);value=completed[0]
                    return sent,value
                original_action=uid();sent,first=send({'interfaceRef':interface,'input':payload},original_action)
                assert first['state']=='completed' and first['result']['output']['status']=='conflict' and not capture.messages
                result=first['result']['output']
                next_payload={**payload,'start':result['suggestedStart'],'end':result['suggestedEnd']}
                second_args={'interactionRef':first['interactionRef'],'input':next_payload}
                action=uid();_,second=send(second_args,action)
                assert second['state']=='completed' and second['interactionRef']==first['interactionRef']
                assert second['result']['output']['status']=='invite_submitted' and second['result']['output']['calendar']=='awaiting_acceptance'
                assert host.turns[0].session_id is None and host.turns[1].session_id
                # The SDK may ask a completed round to finish its missing structured reply.
                # It must keep the same job/Session and cannot produce another effect.
                assert len({turn.operation_id for turn in host.turns})==2
                assert {turn.interaction_id for turn in host.turns}=={first['interactionRef']}
                assert {turn.session_id for turn in host.turns[1:]}=={host.turns[1].session_id}
                wait_for(lambda:len(capture.messages)==1)
                assert client.call('assistant.messaging.send',second_args,operation_id=action)['status']=='accepted'
                assert len(capture.messages)==1
                assert 'PRIVATE-CALENDAR-TITLE' not in json.dumps([first,second])
                email=capture.messages[0]['message']
                ics=next(part for part in email.walk() if part.get_content_type()=='text/calendar').get_content()
                assert 'METHOD:REQUEST' in ics and 'ORGANIZER:mailto:notice@example.test' in ics
                records=module.tools['calendar.book'].invoke.__self__.records()
                conflict=next(item for item in records.values() if item['state']=='conflict')
                assert first['result']['output']==conflict['result']
                record=next(item for item in records.values() if item['state']=='submitted')
                assert second['result']['output']==record['result']
                assert record['context']['interactionId']==first['interactionRef'] and record['receipt']['uid'] in ics
                assert record['context']['sessionId']==host.turns[1].session_id
                if real and os.environ.get('IOALAB_REAL_BUSINESS_REPORT'):
                    from pathlib import Path
                    from a2c.codec import digest_bytes
                    from ioalab_tools import appointments
                    from ioalab_jiuwen import tools as composition
                    from ioalab_sdk.assistant import role
                    sources={label:digest_bytes(Path(module.__file__).read_bytes()) for label,module in (
                        ('src/official/tools/ioalab_tools/appointments.py',appointments),
                        ('src/hosts/jiuwen/ioalab_jiuwen/tools.py',composition),
                        ('src/access/sdk/ioalab_sdk/assistant/role.py',role))}
                    Path(os.environ['IOALAB_REAL_BUSINESS_REPORT']).write_text(json.dumps({
                        'scope':'Original Jiuwen, actual installed calendar factory, HTTP/WS resident roles and real loopback SMTP; scripted local-browser user confirmation',
                        'model':host.capabilities()['model'],'interactionId':first['interactionRef'],'sessionId':record['context']['sessionId'],
                        'first':first['result']['output'],'second':second['result']['output'],'smtpMessages':len(capture.messages),
                        'originalToolReceipts':{'first':conflict['result'],'second':record['result']},
                        'reservationState':record['state'],'invitationReceipt':record['receipt'],'sources':sources,
                        'hostRounds':len(host.turns),'messageJobs':len({turn.operation_id for turn in host.turns}),
                        'limits':['No external email delivery','No Outlook acceptance','No native web menu or new film proof']},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    finally:tools.close();capture.close()
