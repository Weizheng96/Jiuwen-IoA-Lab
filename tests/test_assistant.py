"""Calendar transport-specific safety; resident business effects are tested separately."""
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from ioalab_tools.calendar import GraphCalendar,invitation


def appointment():
    d=datetime.now(timezone.utc)+timedelta(days=1)
    while d.weekday()>4: d+=timedelta(days=1)
    start=d.replace(hour=2,minute=0,second=0,microsecond=0)
    return {'title':'IoA消息中心联调会议','start':start.isoformat(),'end':(start+timedelta(minutes=30)).isoformat(),'timeZone':'Asia/Shanghai'}


def test_graph_calendar_view_personal_account_pagination(tmp_path):
    requests=[]
    def reply(req):
        requests.append(req)
        if req.url.path.endswith('/me'): return httpx.Response(200,json={'mail':'owner@example.test'})
        if req.url.params.get('page')=='2': return httpx.Response(200,json={'value':[{'showAs':'busy','start':{'dateTime':'2026-10-27T02:00:00.0000000','timeZone':'UTC'},'end':{'dateTime':'2026-10-27T03:00:00.0000000','timeZone':'UTC'}}]})
        return httpx.Response(200,json={'value':[{'showAs':'free'}],'@odata.nextLink':'https://graph.microsoft.com/v1.0/me/calendarView?page=2'})
    calendar=GraphCalendar('app',tmp_path/'token.json','owner@example.test',http=httpx.Client(transport=httpx.MockTransport(reply)))
    calendar.save({'access_token':'test-only','expires_at':9999999999})
    assert calendar.busy(datetime(2026,10,27,2,30,tzinfo=timezone.utc),datetime(2026,10,27,3,30,tzinfo=timezone.utc))
    assert any('calendarView' in r.url.path for r in requests)
    assert not any('getSchedule' in str(r.url) for r in requests)


def test_calendar_injection_and_unicode_folding():
    now=datetime.now(timezone.utc);ics=invitation('fixed@ioa','标题'*100+'\nATTENDEE:mailto:evil@example.test',now,now+timedelta(minutes=30),'notice@example.test','receiver@example.test')
    assert not any(line.startswith('ATTENDEE:mailto:evil') for line in ics.split('\r\n'))
    assert all(len(line.encode())<=75 for line in ics.split('\r\n'))


def test_missing_graph_authorization_never_implies_an_empty_calendar(tmp_path):
    with pytest.raises(RuntimeError,match='authorization missing'):
        GraphCalendar('configured-app',tmp_path/'missing-token.json','owner@example.test').busy(
            datetime.now(timezone.utc),datetime.now(timezone.utc)+timedelta(hours=1))
