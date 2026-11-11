"""Official calendar declarations preserve actual receipts and pending states."""
from copy import deepcopy
from datetime import datetime,timedelta

from jsonschema import Draft202012Validator,FormatChecker
import pytest

from ioalab_jiuwen.tools import ConfiguredTools
from ioalab_tools.appointments import Appointments,BOOK_OUTPUT,STATUS_OUTPUT
from ioalab_sdk.runtime.host import ToolBinding,validate_output
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_assistant import appointment
from test_business_tools import context,options,schedule


def test_actual_conflict_without_suggestion_and_submission_keep_original_format(tmp_path,monkeypatch):
    calendar=schedule(tmp_path);payload=appointment()
    previous=calendar.show();document=previous['document']
    document['events']=[{'id':'busy','title':'private-title','start':payload['start'],'end':payload['end']}]
    calendar.replace(document,previous['revision'])
    identities=({'key':'owner','role':'assistant','name':'owner'},)
    tools=ConfiguredTools([{'factory':'calendar','profile':'owner','options':options(calendar)}],
        identities,home=tmp_path/'sdk',base_dir=tmp_path)
    class Notification:
        calls=0
        def send(self,*args):
            self.calls+=1
            return {'status':'invite_submitted','uid':args[0],'explicitTestDouble':True}
    try:
        book=tools.for_profile('owner')['calendar.book'];status=tools.for_profile('owner')['calendar.status']
        service=book.invoke.__self__;notice=Notification();service.notification=notice
        monkeypatch.setattr(service,'_suggest',lambda *_:{})
        first_context=context();conflict=book.invoke(payload,first_context)
        assert conflict=={'status':'conflict','reason':'busy','inviteSubmitted':False} and notice.calls==0
        first=deepcopy(conflict);validate_output(book,conflict);assert conflict==first
        next_payload={**payload,**{key:(datetime.fromisoformat(payload[key])+timedelta(hours=1)).isoformat() for key in ('start','end')}}
        second_context=context(interactionId=first_context['interactionId']);submitted=book.invoke(next_payload,second_context)
        assert submitted['status']=='invite_submitted' and submitted['calendar']=='awaiting_acceptance'
        assert submitted['inviteSubmitted'] is True and notice.calls==1
        before=deepcopy(submitted);validate_output(book,submitted);assert submitted==before
        queried=status.invoke({'reservationId':submitted['reservationId']},second_context)
        validate_output(status,queried)
        assert queried==submitted and notice.calls==1
        records=service.records()
        assert records[first_context['operationId']]['result']==conflict
        assert records[second_context['operationId']]['result']==submitted
    finally:tools.close()


@pytest.mark.parametrize('state',['reserved','sending','submitted','unknown','accepted'])
def test_saved_status_without_terminal_receipt_has_no_implied_invitation_success(tmp_path,monkeypatch,state):
    original=context();service=Appointments(schedule(tmp_path),object(),'owner@example.test')
    monkeypatch.setattr(service,'records',lambda:{original['operationId']:{
        'reservationId':'original-reservation','state':state,'context':original}})
    result=service.status({'reservationId':'original-reservation'},original)
    assert result=={'status':state,'reservationId':'original-reservation','inviteSubmitted':None,'calendar':'unknown'}
    Draft202012Validator(STATUS_OUTPUT,format_checker=FormatChecker()).validate(result)


@pytest.mark.parametrize('schema,value',[
    (BOOK_OUTPUT,{'status':'booked','inviteSubmitted':True}),
    (BOOK_OUTPUT,{'status':'invite_submitted','inviteSubmitted':True,'calendar':'accepted'}),
    (BOOK_OUTPUT,{'status':'conflict','inviteSubmitted':False,'extra':'not-declared'}),
    (BOOK_OUTPUT,{'status':'conflict','inviteSubmitted':False,'suggestedStart':'not-a-date'}),
    (STATUS_OUTPUT,{'status':'unknown','inviteSubmitted':'unknown','calendar':'unknown'}),
])
def test_contract_rejects_invented_success_states_and_undeclared_fields(schema,value):
    tool=ToolBinding('calendar.output','正式日程回执格式',{'type':'object'},lambda *_:None,output_schema=schema)
    with pytest.raises(RuntimeFault) as error:validate_output(tool,value)
    assert error.value.code=='invalid_tool_output'


def test_admin_booking_validates_requester_and_sends_one_invitation_to_both_recipients(tmp_path):
    from tools.media.smtp_capture import Capture
    from ioalab_tools.calendar import SMTPNotification
    capture = Capture(tmp_path/'mail').start()
    service = Appointments(schedule(tmp_path/'calendar'),
        SMTPNotification('127.0.0.1', capture.port, 'notice@example.test', test=True),
        'owner@example.test', requester_email_required=True)
    try:
        original = context()
        payload = appointment()
        for invalid in (payload, {**payload, 'requesterEmail':'bad\r\nrecipient@example.test'}):
            with pytest.raises(RuntimeFault) as exc: service.book(invalid, original)
            assert exc.value.code == 'invalid_input'
        assert not service.root.exists() and not capture.messages
        payload['requesterEmail'] = 'requester@example.test'
        result = service.book(payload, original)
        assert result['inviteSubmitted'] is True and len(capture.messages) == 1
        message = capture.messages[0]['message']
        assert {a.strip() for a in str(message['To']).split(',')} == {'owner@example.test','requester@example.test'}
        calendar = next(part for part in message.walk() if part.get_content_type() == 'text/calendar')
        body = calendar.get_content()
        assert 'METHOD:REQUEST' in body and 'mailto:owner@example.test' in body and 'mailto:requester@example.test' in body
        service.recipient = 'new-owner@example.test'
        assert service.book(payload, {**original, 'operationId':'retry-after-restart'}) == result
        assert len(capture.messages) == 1
        stored = next(iter(service.records().values()))
        assert stored['recipients'] == ['owner@example.test','requester@example.test']
    finally:
        service.close(); capture.close()


def test_agently_uncertain_send_is_never_repeated_under_a_new_operation(tmp_path):
    from ioalab_tools.agently import AgentlyNotification
    import sys
    cli = tmp_path/'mail_boundary.py'
    cli.write_text('''import json,sys
from pathlib import Path
p=Path(__file__).with_suffix('.log')
with p.open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:]==['auth','status']:
 print(json.dumps({'ok':True,'data':{'logged_in':True,'expires_at':'2099-01-01T00:00:00Z'}}))
elif sys.argv[1:]==['+me']:
 print(json.dumps({'ok':True,'data':{'aliases':[{'email':'notice@example.test'}]}}))
else:
 print(json.dumps({'ok':True,'data':{'queued':False}}))
''', encoding='utf-8')
    notice = AgentlyNotification('notice@example.test', [sys.executable,str(cli)], tmp_path/'invites')
    service = Appointments(schedule(tmp_path/'calendar'), notice, 'owner@example.test', requester_email_required=True)
    payload = {**appointment(), 'requesterEmail':'requester@example.test'}
    original = context()
    with pytest.raises(RuntimeFault) as error: service.book(payload, original)
    assert error.value.code == 'unknown_result'
    before = cli.with_suffix('.log').read_text()
    with pytest.raises(RuntimeFault): service.book(payload, {**original, 'operationId':'different-operation'})
    assert cli.with_suffix('.log').read_text() == before
    assert next(iter(service.records().values()))['state'] == 'unknown'


def test_agently_attachment_does_not_prevent_sdk_restart(tmp_path):
    import sys
    from ioalab_sdk.runtime.files import prepare_home
    cli=tmp_path/'mail_cli.py'
    cli.write_text("""import json,sys
if sys.argv[1:]==['auth','status']:
 data={'logged_in':True,'expires_at':'2099-01-01T00:00:00Z'}
elif sys.argv[1:]==['+me']:
 data={'aliases':[{'email':'notice@example.test'}]}
else:data={'queued':True}
print(json.dumps({'ok':True,'data':data}))
""",encoding='utf-8')
    calendar=schedule(tmp_path/'calendar');config=options(calendar)
    config['notification']={'transport':'agently','sender':'notice@example.test','command':[sys.executable,str(cli)]}
    home=tmp_path/'sdk';prepare_home(home)
    identities=({'key':'owner','role':'assistant','name':'owner'},)
    tools=ConfiguredTools([{'factory':'calendar','profile':'owner','options':config}],identities,home=home,base_dir=tmp_path)
    original=context();payload=appointment()
    try:result=tools.for_profile('owner')['calendar.book'].invoke(payload,original)
    finally:tools.close()
    assert result['inviteSubmitted'] is True
    prepare_home(home)  # The actual SDK startup boundary must accept persisted business files.
    restored=ConfiguredTools([{'factory':'calendar','profile':'owner','options':config}],identities,home=home,base_dir=tmp_path)
    try:
        assert restored.for_profile('owner')['calendar.book'].invoke(payload,{**original,'operationId':'restart'})==result
    finally:restored.close()
