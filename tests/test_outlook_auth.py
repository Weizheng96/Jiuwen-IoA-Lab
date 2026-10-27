"""Microsoft account/resource boundaries and SMTP OAuth2, without external mail."""
import json
import smtplib
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ioalab_sdk.calendar import GraphCalendar, OutlookSMTPAuth, SMTPNotification, SMTP_SCOPES


def device_transport(*,mail='other@example.test',access='new-access',requests=None):
    def handle(request):
        if requests is not None: requests.append(request)
        if request.url.path.endswith('/devicecode'):
            return httpx.Response(200,json={'device_code':'test-device','message':'Local test only','expires_in':60,'interval':0})
        if request.url.path.endswith('/token'):
            return httpx.Response(200,json={'access_token':access,'refresh_token':'new-refresh','expires_in':3600})
        return httpx.Response(200,json={'mail':mail})
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_wrong_calendar_login_preserves_existing_authorization(tmp_path):
    path=tmp_path/'calendar-token.json';path.write_text('previous authorization')
    calendar=GraphCalendar('test-app',path,'owner@example.test',http=device_transport())
    with pytest.raises(RuntimeError,match='does not match'):
        calendar.login(prompt=lambda message:None)
    assert path.read_text()=='previous authorization'


def test_smtp_refresh_has_separate_resource_and_account_binding(tmp_path):
    requests=[];path=tmp_path/'smtp-token.json'
    oauth=OutlookSMTPAuth('test-app',path,'sender@example.test',http=device_transport(requests=requests))
    oauth.save({'access_token':'expired','refresh_token':'old-refresh','expires_at':0})
    assert oauth.token()=='new-access'
    from urllib.parse import parse_qs
    fields=parse_qs(requests[0].content.decode())
    assert fields['scope']==[SMTP_SCOPES] and 'Calendars.Read' not in fields['scope'][0]
    assert fields['grant_type']==['refresh_token']
    assert json.loads(path.read_text())['ioalabBinding']['account']=='sender@example.test'
    other=GraphCalendar('test-app',path,'owner@example.test',http=device_transport())
    with pytest.raises(RuntimeError,match='changed'):other.token()


class SMTPServer:
    calls=[]
    reject=False
    def __init__(self,*args,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def starttls(self,**kwargs):self.calls.append('tls')
    def auth(self,mechanism,callback):
        self.calls.append((mechanism,callback(),callback(b'challenge')))
        if self.reject:raise smtplib.SMTPAuthenticationError(535,b'Authentication unsuccessful')
    def login(self,*args):raise AssertionError('Password fallback is forbidden')
    def send_message(self,message):self.calls.append(('mail',message));return {}


@pytest.fixture
def smtp(monkeypatch):
    SMTPServer.calls=[];SMTPServer.reject=False
    monkeypatch.setattr(smtplib,'SMTP',SMTPServer)
    return SMTPServer


@pytest.mark.parametrize('rejected',[False,True])
def test_smtp_login_validates_account_before_saving(tmp_path,smtp,rejected):
    path=tmp_path/'smtp-token.json';path.write_text('previous authorization')
    requests=[]
    oauth=OutlookSMTPAuth('test-app',path,'sender@example.test',http=device_transport(requests=requests))
    smtp.reject=rejected
    if rejected:
        with pytest.raises(smtplib.SMTPAuthenticationError):oauth.login(prompt=lambda message:None)
        assert path.read_text()=='previous authorization'
    else:
        oauth.login(prompt=lambda message:None)
        assert json.loads(path.read_text())['access_token']=='new-access'
    from urllib.parse import parse_qs
    assert set(parse_qs(requests[0].content.decode())['scope'][0].split())=={'offline_access','https://outlook.office.com/SMTP.Send'}
    assert smtp.calls[0]=='tls'
    assert smtp.calls[1]==('XOAUTH2','user=sender@example.test\x01auth=Bearer new-access\x01\x01','')
    assert not any(isinstance(c,tuple) and c[0]=='mail' for c in smtp.calls)


def test_oauth_smtp_check_sends_no_mail_and_invitation_uses_authenticated_sender(tmp_path,smtp,capsys):
    oauth=OutlookSMTPAuth('test-app',tmp_path/'smtp.json','sender@example.test')
    oauth.save({'access_token':'local-token','expires_at':time.time()+3600})
    notification=SMTPNotification('smtp-mail.outlook.com',587,'sender@example.test',oauth=oauth)
    assert notification.check()['messageSent'] is False
    assert not any(isinstance(c,tuple) and c[0]=='mail' for c in smtp.calls)
    start=datetime.now(timezone.utc)+timedelta(days=1)
    receipt=notification.send('test-uid','A meeting',start,start+timedelta(minutes=30),'owner@example.test')
    message=next(c[1] for c in smtp.calls if isinstance(c,tuple) and c[0]=='mail')
    assert message['From']=='sender@example.test' and message['To']=='owner@example.test'
    assert 'ORGANIZER:mailto:sender@example.test' in message.get_payload()[1].get_content()
    assert receipt['status']=='invite_submitted' and receipt['calendar']=='awaiting_acceptance'
    assert 'local-token' not in capsys.readouterr().out


def test_outlook_refuses_password_and_token_transmission_to_other_hosts(tmp_path):
    with pytest.raises(ValueError,match='requires OAuth2'):
        SMTPNotification('smtp-mail.outlook.com',587,'sender@example.test',username='sender@example.test',password='test-only')
    oauth=OutlookSMTPAuth('test-app',tmp_path/'smtp.json','sender@example.test')
    for host,port in [('other.example.test',587),('smtp-mail.outlook.com',25),('smtp-mail.outlook.com',465)]:
        with pytest.raises(ValueError):SMTPNotification(host,port,'sender@example.test',oauth=oauth)
    with pytest.raises(ValueError,match='match'):
        SMTPNotification('smtp-mail.outlook.com',587,'other@example.test',oauth=oauth)
