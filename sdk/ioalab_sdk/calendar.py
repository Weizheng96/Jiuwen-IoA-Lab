"""Read-only personal Outlook availability and standards-based invitation email."""
from __future__ import annotations
import json
import os
import smtplib
import ssl
import time
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from email.message import EmailMessage
from email.utils import parseaddr,formatdate
from pathlib import Path
import httpx

SCOPES='offline_access Calendars.Read User.Read'
SMTP_SCOPES='offline_access https://outlook.office.com/SMTP.Send'
MICROSOFT_SMTP={'smtp-mail.outlook.com','smtp.office365.com'}

class MicrosoftDeviceAuth:
    """One resource and one account per local device-code authorization."""
    def __init__(self,client_id,token_path,*,scopes,account,tenant='consumers',http=None):
        if not client_id: raise ValueError('Configure the Microsoft application Client ID before login')
        if tenant not in ('consumers','common'): raise ValueError('Personal Outlook requires consumers or common authority')
        self.client_id,self.path,self.tenant=client_id,Path(token_path),tenant
        self.scopes,self.account=scopes,account
        self.http=http or httpx.Client(timeout=30,trust_env=False)

    @property
    def binding(self): return {'clientId':self.client_id,'account':self.account.casefold(),'scopes':self.scopes,'tenant':self.tenant}

    @property
    def authority(self): return f'https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0'

    def save(self,token):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({**token,'ioalabBinding':self.binding}),encoding='utf-8')
        try: os.chmod(temp,0o600)
        except OSError: pass
        os.replace(temp,self.path)

    def login(self,prompt=print):
        device=self.http.post(self.authority+'/devicecode',data={'client_id':self.client_id,'scope':self.scopes}).raise_for_status().json()
        prompt(device['message'])
        until=time.monotonic()+device['expires_in'];interval=device.get('interval',5)
        while time.monotonic()<until:
            time.sleep(interval)
            r=self.http.post(self.authority+'/token',data={'client_id':self.client_id,'grant_type':'urn:ietf:params:oauth:grant-type:device_code','device_code':device['device_code']})
            body=r.json()
            if 'access_token' in body:
                body['expires_at']=time.time()+body['expires_in']
                self.validate_token(body['access_token'])
                self.save(body);return
            if body.get('error')=='slow_down': interval+=5
            elif body.get('error')!='authorization_pending': raise RuntimeError('Microsoft authorization failed: '+body.get('error','unknown'))
        raise TimeoutError('Microsoft authorization expired')

    def token(self):
        if not self.path.exists(): raise RuntimeError('Microsoft authorization missing; run the corresponding login command')
        token=json.loads(self.path.read_text(encoding='utf-8'))
        if token.get('ioalabBinding',self.binding)!=self.binding: raise RuntimeError('Authorization account, application or scopes changed; log in again')
        if token.get('expires_at',0)<time.time()+120:
            r=self.http.post(self.authority+'/token',data={'client_id':self.client_id,'grant_type':'refresh_token','refresh_token':token['refresh_token'],'scope':self.scopes})
            if not r.is_success: raise RuntimeError('Microsoft authorization expired; log in again')
            new=r.json();new.setdefault('refresh_token',token['refresh_token']);new['expires_at']=time.time()+new['expires_in'];self.save(new);token=new
        return token['access_token']

    def validate_token(self,token): raise NotImplementedError


class GraphCalendar(MicrosoftDeviceAuth):
    def __init__(self,client_id,token_path,recipient,*,tenant='consumers',http=None):
        self.recipient=recipient
        super().__init__(client_id,token_path,scopes=SCOPES,account=recipient,tenant=tenant,http=http)

    def validate_token(self,token): self.profile(token)

    def profile(self,token):
        p=self.http.get('https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName',headers={'Authorization':'Bearer '+token}).raise_for_status().json()
        if self.recipient.casefold() not in {str(p.get(k,'')).casefold() for k in ('mail','userPrincipalName')}: raise RuntimeError('Authorized calendar account does not match configured recipient')

    def busy(self,start,end):
        token=self.token();self.profile(token)
        url='https://graph.microsoft.com/v1.0/me/calendarView';params={'startDateTime':start.isoformat(),'endDateTime':end.isoformat(),'$select':'start,end,showAs,isCancelled','$top':'100'}
        count=0
        while url:
            if not url.startswith('https://graph.microsoft.com/'): raise RuntimeError('Unexpected calendar pagination origin')
            data=self.http.get(url,params=params,headers={'Authorization':'Bearer '+token,'Prefer':'outlook.timezone="UTC"'}).raise_for_status().json();params=None
            for item in data.get('value',[]):
                if item.get('isCancelled') or item.get('showAs')=='free': continue
                def dt(v):
                    value=datetime.fromisoformat(v['dateTime'].replace('Z','+00:00'))
                    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
                if dt(item['start'])<end and start<dt(item['end']): return True
            url=data.get('@odata.nextLink');count+=1
            if count>50: raise RuntimeError('Calendar pagination exceeded safety bound')
        return False

def address(value):
    if not isinstance(value,str) or any(c in value for c in '\r\n') or parseaddr(value)[1]!=value or value.count('@')!=1: raise ValueError('Invalid email address')
    return value

def invitation(uid,title,start,end,organizer,recipient):
    organizer,recipient=address(organizer),address(recipient)
    def escape(v): return str(v).replace('\\','\\\\').replace('\r','').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
    def stamp(t): return t.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines=['BEGIN:VCALENDAR','PRODID:-//Jiuwen IoA Lab//Assistant//ZH','VERSION:2.0','CALSCALE:GREGORIAN','METHOD:REQUEST','BEGIN:VEVENT','UID:'+escape(uid),'SEQUENCE:0','DTSTAMP:'+stamp(datetime.now(timezone.utc)),'DTSTART:'+stamp(start),'DTEND:'+stamp(end),'SUMMARY:'+escape(title),'ORGANIZER:mailto:'+organizer,'ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:mailto:'+recipient,'STATUS:CONFIRMED','END:VEVENT','END:VCALENDAR']
    # RFC 5545 folds by UTF-8 octets without splitting a code point.
    folded=[]
    for line in lines:
        part=''
        for char in line:
            if len((part+char).encode())>73:
                folded.append(part);part=' '+char
            else: part+=char
        folded.append(part)
    return '\r\n'.join(folded)+'\r\n'

class OutlookSMTPAuth(MicrosoftDeviceAuth):
    def __init__(self,client_id,token_path,sender,*,host='smtp-mail.outlook.com',port=587,http=None):
        self.host,self.port=host,int(port)
        super().__init__(client_id,token_path,scopes=SMTP_SCOPES,account=address(sender),http=http)
        if host.casefold() not in MICROSOFT_SMTP or self.port!=587: raise ValueError('Microsoft SMTP OAuth2 requires a Microsoft SMTP endpoint on port 587')

    def validate_token(self,token):
        # The SMTP server verifies that this token may send as the configured mailbox.
        SMTPNotification(self.host,self.port,self.account,oauth=self).check(access_token=token)


class SMTPNotification:
    def __init__(self,host,port,sender,*,username='',password='',test=False,oauth=None):
        self.host,self.port,self.sender=host,int(port),address(sender)
        self.username,self.password,self.test,self.oauth=username,password,test,oauth
        if not host: raise ValueError('Configure notification SMTP host')
        if test and host not in ('127.0.0.1','localhost','::1'): raise ValueError('SMTP capture mode is restricted to loopback')
        if oauth:
            if test or host.casefold() not in MICROSOFT_SMTP or self.port!=587: raise ValueError('Microsoft OAuth2 tokens may only be sent over STARTTLS to Microsoft SMTP on port 587')
            if username or password: raise ValueError('Do not combine SMTP OAuth2 with password credentials')
            if oauth.account.casefold()!=self.sender.casefold(): raise ValueError('SMTP OAuth2 account must match the invitation organizer')
        elif not test and host.casefold() in MICROSOFT_SMTP: raise ValueError('Outlook SMTP requires OAuth2; configure IOALAB_SMTP_AUTH=oauth2 and run smtp-login')

    @contextmanager
    def connection(self,access_token=None):
        cls=smtplib.SMTP_SSL if self.port==465 else smtplib.SMTP
        kwargs={'timeout':30}
        if self.port==465: kwargs['context']=ssl.create_default_context()
        with cls(self.host,self.port,**kwargs) as smtp:
            if self.port!=465 and not self.test: smtp.starttls(context=ssl.create_default_context())
            if self.oauth:
                token=access_token if access_token is not None else self.oauth.token()
                response='user='+self.sender+'\x01auth=Bearer '+token+'\x01\x01'
                smtp.auth('XOAUTH2',lambda challenge=None: response if challenge is None else '')
            elif self.username: smtp.login(self.username,self.password)
            yield smtp

    def check(self,*,access_token=None):
        """Connect and authenticate without submitting a message."""
        with self.connection(access_token): pass
        return {'status':'authenticated' if self.oauth or self.username else 'connected','sender':self.sender,'auth':'oauth2' if self.oauth else 'password' if self.username else 'relay','messageSent':False}

    def send(self,uid,title,start,end,recipient):
        message=EmailMessage();message['From']=self.sender;message['To']=address(recipient);message['Subject']='会议邀请：'+title
        message['Date']=formatdate(localtime=False);message['Message-ID']='<'+uid+'>'
        message.set_content('您收到一项会议邀请。接受后才会更新您的日历。\n'+title+'\n'+start.isoformat()+' — '+end.isoformat())
        message.add_alternative(invitation(uid,title,start,end,self.sender,recipient),subtype='calendar',params={'method':'REQUEST','charset':'UTF-8'})
        with self.connection() as smtp:
            refused=smtp.send_message(message)
            if refused: raise RuntimeError('Notification recipient rejected')
        return {'status':'invite_submitted','calendar':'awaiting_acceptance','uid':uid,'recipient':recipient,'transport':'smtp-capture' if self.test else 'smtp'}
