"""Explicitly configured local handlers; message data never grants new capabilities."""
from datetime import datetime,timezone,timedelta
from contextlib import nullcontext
import time
from zoneinfo import ZoneInfo
from .lease import Lease
from .messaging import RetryLater

FORWARD_SCHEMA={'type':'object','properties':{'text':{'type':'string','minLength':1,'maxLength':4000}},'required':['text'],'additionalProperties':False}
MEETING_SCHEMA={'type':'object','properties':{'title':{'type':'string','minLength':1,'maxLength':200},'start':{'type':'string','format':'date-time'},'end':{'type':'string','format':'date-time'},'timeZone':{'type':'string','maxLength':64}},'required':['title','start','end','timeZone'],'additionalProperties':False}

class AssistantHandlers:
    def __init__(self,mailbox,*,bridge=None,calendar=None,notification=None,recipient='zhengw.cc@outlook.com',rules=None):
        self.mailbox,self.store=mailbox,mailbox.store
        self.bridge,self.calendar,self.notification=bridge,calendar,notification
        self.recipient=recipient
        self.rules={'timeZone':'Asia/Shanghai','weekdays':[0,1,2,3,4],'startHour':9,'endHour':18,'minMinutes':15,'maxMinutes':120,'leadMinutes':30,'daysAhead':30,**(rules or {})}

    def publish(self,allowed=None,*,replace=False):
        existing={i['name'] for i in self.mailbox.interfaces()}
        for name,schema,description in [('message.forward',FORWARD_SCHEMA,'给我的九问网页留言'),('meeting.request',MEETING_SCHEMA,'在预授权时段内预约会议，发送日历邀请邮件')]:
            if name in existing and not replace:continue
            self.mailbox.publish(name,{'inputSchema':schema,'description':description,'allowedSenders':allowed})

    def __call__(self,message):
        # Recheck current visibility and server expiry immediately before effects.
        current=self.mailbox.get(message['id'])
        if current['state'] in ('expired','rejected'): return {'status':'permission_revoked'}
        mine=next((i for i in self.mailbox.interfaces() if i['name']==message['interface']),None)
        if not mine or not mine['enabled'] or (mine['allowedSenders'] is not None and message['senderUsername'] not in mine['allowedSenders']): return {'status':'permission_revoked'}
        if message['interface']=='message.forward':
            if not self.bridge: raise RetryLater('Jiuwen bridge not configured')
            return self.bridge.forward(message)
        if message['interface']=='meeting.request': return self.meeting(message)
        return {'status':'failed','error':'No local handler is configured for this interface'}

    def meeting(self,message):
        if not self.calendar or not self.notification: return {'status':'failed','error':'Calendar authorization or notification mailbox is not configured'}
        session=self.calendar.session() if hasattr(self.calendar,'session') else nullcontext(self.calendar)
        try:
            with session as calendar:
                return self._meeting(message,calendar)
        except (OSError,ValueError):
            return {'status':'failed','error':'Availability source is missing, invalid or changed during processing'}

    def _meeting(self,message,calendar):
        payload=message['payload'];r={**self.rules,**getattr(calendar,'rules',{})}
        start=datetime.fromisoformat(payload['start'].replace('Z','+00:00'));end=datetime.fromisoformat(payload['end'].replace('Z','+00:00'))
        if start.tzinfo is None or end.tzinfo is None: return {'status':'no_time','reason':'timezone_required'}
        try: zone=ZoneInfo(r['timeZone']);ZoneInfo(payload['timeZone'])
        except Exception: return {'status':'no_time','reason':'unknown_timezone'}
        a,b=start.astimezone(zone),end.astimezone(zone);now=datetime.now(timezone.utc);minutes=(end-start).total_seconds()/60
        earliest=a.replace(hour=r['startHour'],minute=0,second=0,microsecond=0);latest=a.replace(hour=r['endHour'],minute=0,second=0,microsecond=0)
        if not (r['minMinutes']<=minutes<=r['maxMinutes'] and a.weekday() in r['weekdays'] and a.date()==b.date() and earliest<=a<b<=latest and now+timedelta(minutes=r['leadMinutes'])<=start<=now+timedelta(days=r['daysAhead'])): return {'status':'no_time','reason':'outside_authorized_rules'}
        with Lease(self.mailbox.home/'appointments.lock',timeout=30):
            old=self.store.get('appointments',message['id'])
            if old:
                if old['state']=='submitted': return old['receipt']
                if old['state'] in ('sending','unknown'): return {'status':'unknown','uid':old['uid'],'reason':'Review notification transport before retrying'}
            for mid,record in self.store.all('appointments').items():
                if mid!=message['id'] and record['state'] in ('sending','submitted','unknown') and record['start']<end.timestamp() and start.timestamp()<record['end']:
                    return {'status':'no_time','reason':'local_pending_invitation'}
            # Errors reading the selected availability source never imply free time.
            if calendar.busy(start,end): return {'status':'no_time','reason':'calendar_conflict'}
            if self.mailbox.get(message['id'])['expires']<=time.time(): return {'status':'failed','error':'Request expired before invitation'}
            if hasattr(calendar,'assert_current'):calendar.assert_current()
            uid='ioa-'+message['id']+'@jiuwen-ioa-lab'
            record={'uid':uid,'start':start.timestamp(),'end':end.timestamp(),'state':'sending'}
            self.store.put('appointments',message['id'],record)
            try: receipt=self.notification.send(uid,payload['title'],start,end,self.recipient)
            except Exception:
                record['state']='unknown';self.store.put('appointments',message['id'],record)
                return {'status':'unknown','uid':uid,'reason':'Notification outcome requires review; automatic resend disabled'}
            record.update(state='submitted',receipt=receipt);self.store.put('appointments',message['id'],record)
            return receipt
