"""Real SMTP wire capture plus fail-closed calendar and side-effect recovery."""
import sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
import httpx
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'demo'))
from smtp_capture import Capture
from ioalab_sdk.calendar import SMTPNotification,GraphCalendar,invitation
from ioalab_sdk.assistant import AssistantHandlers
from ioalab_sdk.messaging import MailboxClient
from _support import PASSWORD,new_user

class CalendarFixture:
    def __init__(self,busy=False): self.value=busy;self.calls=0
    def busy(self,start,end): self.calls+=1;return self.value

def appointment():
    d=datetime.now(timezone.utc)+timedelta(days=1)
    while d.weekday()>4: d+=timedelta(days=1)
    start=d.replace(hour=2,minute=0,second=0,microsecond=0)
    return {'title':'IoA消息中心联调会议','start':start.isoformat(),'end':(start+timedelta(minutes=30)).isoformat(),'timeZone':'Asia/Shanghai'}

def test_invitation_smtp_and_no_duplicate_effect(platform,tmp_path):
    a,b=new_user(platform),new_user(platform)
    receiver=MailboxClient(platform.s.base_url,a['username'],PASSWORD,tmp_path/'a',connect_identity=True)
    sender=MailboxClient(platform.s.base_url,b['username'],PASSWORD,tmp_path/'b',connect_identity=True)
    capture=Capture(tmp_path/'mail').start();calendar=CalendarFixture()
    try:
        notify=SMTPNotification('127.0.0.1',capture.port,'notification@example.test',test=True)
        handler=AssistantHandlers(receiver,calendar=calendar,notification=notify)
        handler.publish([b['username']]);message=sender.send(a['username'],'meeting.request',appointment())
        receiver.sync();receiver.process(handler)
        result=sender.get(message['id'])['result']
        assert result['status']=='invite_submitted' and result['calendar']=='awaiting_acceptance',result
        assert len(capture.messages)==1
        email=capture.messages[0]['message'];ics=next(p for p in email.walk() if p.get_content_type()=='text/calendar').get_content()
        assert 'METHOD:REQUEST' in ics and 'ORGANIZER:mailto:notification@example.test' in ics
        assert 'ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:mailto:zhengw.cc@outlook.com' in ics
        assert 'UID:ioa-'+message['id'] in ics
        # Handler recovery returns the persisted send receipt without another SMTP attempt.
        assert handler.meeting(message)==result
        receiver.process(handler);assert len(capture.messages)==1
        other=sender.send(a['username'],'meeting.request',appointment());receiver.sync();receiver.process(handler)
        assert sender.get(other['id'])['result']['reason']=='local_pending_invitation'
    finally: capture.close();receiver.close();sender.close()

def test_calendar_conflicts_authorization_and_unknown_send(platform,tmp_path):
    a,b=new_user(platform),new_user(platform)
    receiver=MailboxClient(platform.s.base_url,a['username'],PASSWORD,tmp_path/'a',connect_identity=True)
    sender=MailboxClient(platform.s.base_url,b['username'],PASSWORD,tmp_path/'b',connect_identity=True)
    class BrokenMail:
        calls=0
        def send(self,*args): self.calls+=1;raise TimeoutError('Delivery may have occurred')
    mail=BrokenMail();calendar=CalendarFixture(True)
    try:
        handler=AssistantHandlers(receiver,calendar=calendar,notification=mail);handler.publish()
        m=sender.send(a['username'],'meeting.request',appointment());receiver.sync();receiver.process(handler)
        result=sender.get(m['id'])['result'];assert result.get('reason')=='calendar_conflict' and mail.calls==0,result
        calendar.value=False
        m=sender.send(a['username'],'meeting.request',appointment());receiver.sync();receiver.process(handler)
        assert sender.get(m['id'])['result']['status']=='unknown' and mail.calls==1
        assert handler.meeting(m)['status']=='unknown' and mail.calls==1
        # Auth missing is not interpreted as an empty calendar.
        with pytest.raises(RuntimeError,match='authorization missing'):
            GraphCalendar('configured-app',tmp_path/'missing-token.json','zhengw.cc@outlook.com').busy(datetime.now(timezone.utc),datetime.now(timezone.utc)+timedelta(hours=1))
    finally:receiver.close();sender.close()

def test_graph_calendar_view_personal_account_pagination(tmp_path):
    requests=[]
    def reply(req):
        requests.append(req)
        if req.url.path.endswith('/me'): return httpx.Response(200,json={'mail':'zhengw.cc@outlook.com'})
        if req.url.params.get('page')=='2': return httpx.Response(200,json={'value':[{'showAs':'busy','start':{'dateTime':'2026-10-27T02:00:00.0000000','timeZone':'UTC'},'end':{'dateTime':'2026-10-27T03:00:00.0000000','timeZone':'UTC'}}]})
        return httpx.Response(200,json={'value':[{'showAs':'free'}],'@odata.nextLink':'https://graph.microsoft.com/v1.0/me/calendarView?page=2'})
    calendar=GraphCalendar('app',tmp_path/'token.json','zhengw.cc@outlook.com',http=httpx.Client(transport=httpx.MockTransport(reply)))
    calendar.save({'access_token':'test-only','expires_at':9999999999})
    assert calendar.busy(datetime(2026,10,27,2,30,tzinfo=timezone.utc),datetime(2026,10,27,3,30,tzinfo=timezone.utc))
    assert any('calendarView' in r.url.path for r in requests)
    assert not any('getSchedule' in str(r.url) for r in requests)

def test_calendar_injection_and_unicode_folding():
    now=datetime.now(timezone.utc);ics=invitation('fixed@ioa','标题'*100+'\nATTENDEE:mailto:evil@example.test',now,now+timedelta(minutes=30),'notice@example.test','receiver@example.test')
    assert not any(line.startswith('ATTENDEE:mailto:evil') for line in ics.split('\r\n'))
    assert all(len(line.encode())<=75 for line in ics.split('\r\n'))

def test_receiver_restart_keeps_revoked_interface_policy(platform,tmp_path):
    a=new_user(platform);box=MailboxClient(platform.s.base_url,a['username'],PASSWORD,tmp_path,connect_identity=True)
    try:
        handler=AssistantHandlers(box);handler.publish([])
        definition=box.interfaces()[0];name=definition.pop('name');definition.pop('revision');definition.pop('allowedIds',None);definition['enabled']=False
        box.publish(name,definition)
        handler.publish()  # Startup with no new policy must not broaden access.
        saved=next(i for i in box.interfaces() if i['name']==name)
        assert saved['enabled'] is False and saved['allowedSenders']==[]
    finally:box.close()
