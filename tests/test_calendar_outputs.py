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
