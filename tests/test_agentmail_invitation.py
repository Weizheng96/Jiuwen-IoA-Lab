"""Native invitations retain idempotency and never write the recipient calendar."""
from datetime import datetime,timedelta,timezone
import json
import re
import httpx
import pytest
from ioalab_sdk.agentmail import AgentMailNotification


def test_native_invitation_uses_organizer_calendar_and_stable_client_id():
    requests=[]
    def handle(request):
        requests.append(request)
        if request.method=='GET':return httpx.Response(200,json={'inbox_id':'test@agentmail.to'})
        return httpx.Response(201,json={'event':{'event_id':'stored-event','uid':'stored-event@agentmail.to','organizer_email':'test@agentmail.to'},'operation_id':'operation'})
    notification=AgentMailNotification('test@agentmail.to','test-key',http=httpx.Client(base_url='https://api.agentmail.to/v0/',transport=httpx.MockTransport(handle)))
    assert notification.check()['messageSent'] is False and len(requests)==1
    start=datetime(2026,10,28,10,tzinfo=timezone(timedelta(hours=8)));end=start+timedelta(minutes=15)
    request_id='ioa-stable-request@jiuwen-ioa-lab'
    first=notification.send(request_id,'Test',start,end,'recipient@example.test')
    assert first['uid']=='stored-event@agentmail.to' and first['requestId']==request_id
    assert first['calendar']=='awaiting_acceptance' and first['delivery']=='pending_provider_send'
    body=json.loads(requests[-1].content)
    assert body['start']=='2026-10-28T02:00:00' and body['timezone']=='UTC'
    assert body['attendees']==[{'email':'recipient@example.test','role':'required'}]
    assert body['send_invites'] is True and re.fullmatch('[A-Za-z0-9._~-]{1,128}',body['client_id'])
    assert body['metadata']['ioa_request_id']==request_id
    assert requests[-1].url.host=='api.agentmail.to' and '/test%40agentmail.to/calendar/events' in requests[-1].url.raw_path.decode()
    notification.send(request_id,'Test',start,end,'recipient@example.test')
    assert requests[-1].content==requests[-2].content


def test_provider_failure_does_not_fall_back_to_smtp():
    requests=[]
    def handle(request):requests.append(request);return httpx.Response(403,json={'code':'missing_permission'})
    sender=AgentMailNotification('test@agentmail.to','test-key',http=httpx.Client(base_url='https://api.agentmail.to/v0/',transport=httpx.MockTransport(handle)))
    start=datetime.now(timezone.utc)+timedelta(days=1)
    with pytest.raises(RuntimeError,match='403'):sender.send('fixed','Test',start,start+timedelta(minutes=15),'recipient@example.test')
    assert len(requests)==1
