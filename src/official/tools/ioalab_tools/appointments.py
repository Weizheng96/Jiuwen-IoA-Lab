"""Participant-owned reservations and invitation receipts, separate from SDK data."""
from contextlib import contextmanager,nullcontext
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from a2c.codec import digest
from dotenv import dotenv_values
from jsonschema import Draft202012Validator,FormatChecker
from ioalab_sdk.lease import Lease
from ioalab_sdk.runtime.files import private_json,restrict
from ioalab_sdk.runtime.host import BusinessTools,ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault
from .calendar import GraphCalendar,SMTPNotification,address
from .agentmail import AgentMailNotification
from .schedule import LocalSchedule,DEFAULT_RULES,RULES_SCHEMA,instant

INPUT={'type':'object','properties':{'title':{'type':'string','minLength':1,'maxLength':200},
       'start':{'type':'string','format':'date-time'},'end':{'type':'string','format':'date-time'},
       'timeZone':{'type':'string','minLength':1,'maxLength':64}},'required':['title','start','end','timeZone'],'additionalProperties':False}
STATUS={'type':'object','properties':{'reservationId':{'type':'string','minLength':1,'maxLength':128}},'required':['reservationId'],'additionalProperties':False}
ACTIVE={'reserved','sending','submitted','unknown','accepted'}


def appointment_records(source):
    source=Path(source).resolve();root=source.with_name(source.name+'.ioa')
    if not root.exists():return {}
    marker=root/'source.json';database=root/'appointments.sqlite'
    if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8'))!={'format':'ioalab.appointments/1','source':str(source)}:
        raise RuntimeFault('identity_conflict','Reservation source does not match the selected schedule')
    if not database.is_file():raise RuntimeFault('not_ready','Restore the original reservation database; do not initialize empty storage')
    connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=10)
    try:return {key:json.loads(value) for key,value in connection.execute('SELECT operation,body FROM appointments')}
    finally:connection.close()


class Appointments:
    def __init__(self,calendar,notification,recipient,*,rules=None,clock=None):
        self.calendar,self.notification,self.recipient=calendar,notification,address(recipient)
        self.rules={**DEFAULT_RULES,**(rules or {})}
        Draft202012Validator(RULES_SCHEMA).validate(self.rules)
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        source=calendar.path.resolve()
        self.root=source.with_name(source.name+'.ioa')
        self.lock=source.with_name(source.name+'.appointments.lock')
        self.identity={'format':'ioalab.appointments/1','source':str(source)}

    def _initialize(self):
        marker=self.root/'source.json'
        if self.root.exists():
            if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8'))!=self.identity:
                raise RuntimeFault('identity_conflict','Reservation source does not match the selected schedule')
            if not (self.root/'appointments.sqlite').is_file():
                raise RuntimeFault('not_ready','Restore the original reservation database; do not initialize empty storage')
        else:
            self.root.mkdir(mode=0o700)
            restrict(self.root,directory=True)
            private_json(marker,self.identity)
        path=self.root/'appointments.sqlite'
        with self._database(create=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS appointments(operation TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,body TEXT NOT NULL)')
        restrict(path)

    @contextmanager
    def _database(self,*,create=False):
        path=self.root/'appointments.sqlite'
        connection=sqlite3.connect(str(path) if create else path.as_uri()+'?mode=ro',uri=not create,timeout=10)
        try:yield connection
        finally:connection.close()

    def records(self):
        return appointment_records(self.calendar.path)

    def _save(self,operation,record):
        with self._database(create=True) as db:
            old=db.execute('SELECT fingerprint FROM appointments WHERE operation=?',(operation,)).fetchone()
            if old and old[0]!=record['fingerprint']:raise RuntimeFault('id_conflict','Original reservation input changed')
            db.execute('INSERT OR REPLACE INTO appointments VALUES(?,?,?)',(operation,record['fingerprint'],json.dumps(record,ensure_ascii=False,allow_nan=False)))
            db.commit()

    def _busy(self,calendar,records,start,end,operation):
        return calendar.busy(start,end) or any(key!=operation and item['state'] in ACTIVE and item['start']<end.timestamp() and start.timestamp()<item['end'] for key,item in records.items())

    def _allowed(self,start,end,rules):
        zone=ZoneInfo(rules.get('timeZone','Asia/Shanghai'));a,b=start.astimezone(zone),end.astimezone(zone)
        minutes=(end-start).total_seconds()/60
        return (rules['minMinutes']<=minutes<=rules['maxMinutes'] and a.weekday() in rules['weekdays'] and a.date()==b.date()
                and a.replace(hour=rules['startHour'],minute=0,second=0,microsecond=0)<=a<b<=a.replace(hour=rules['endHour'],minute=0,second=0,microsecond=0)
                and self.clock()+timedelta(minutes=rules['leadMinutes'])<=start<=self.clock()+timedelta(days=rules['daysAhead']))

    def _suggest(self,calendar,records,start,end,rules,operation):
        zone=ZoneInfo(rules.get('timeZone','Asia/Shanghai'))
        duration=timedelta(minutes=max(rules['minMinutes'],min(rules['maxMinutes'],(end-start).total_seconds()/60)))
        candidate=max(start+timedelta(minutes=15),self.clock()+timedelta(minutes=rules['leadMinutes'])).astimezone(zone).replace(second=0,microsecond=0)
        if candidate.minute%15:candidate+=timedelta(minutes=15-candidate.minute%15)
        probes=0
        for _ in range(min(rules['daysAhead']+1,366)):
            begin=candidate.replace(hour=rules['startHour'],minute=0);candidate=max(candidate,begin)
            limit=candidate.replace(hour=rules['endHour'],minute=0)
            while candidate+duration<=limit and candidate.weekday() in rules['weekdays']:
                if self._allowed(candidate,candidate+duration,rules):
                    probes+=1
                    if not self._busy(calendar,records,candidate,candidate+duration,operation):
                        return {'suggestedStart':candidate.isoformat(),'suggestedEnd':(candidate+duration).isoformat(),'timeZone':str(zone)}
                    if probes>=32:return {}
                candidate+=timedelta(minutes=15)
            candidate=(candidate+timedelta(days=1)).replace(hour=rules['startHour'],minute=0)
        return {}

    def book(self,args,context):
        if next(Draft202012Validator(INPUT,format_checker=FormatChecker()).iter_errors(args),None):raise RuntimeFault('invalid_input','Use the configured meeting input format')
        if any(character in args['title'] for character in '\r\n\x00'):raise RuntimeFault('invalid_input','Use a single-line meeting title')
        ZoneInfo(args['timeZone']);start,end=instant(args['start']),instant(args['end'])
        if end<=start:raise RuntimeFault('invalid_input','Meeting end must be after start')
        operation=context['operationId']
        fingerprint=digest({'input':args,**{key:context[key] for key in ('agentId','messageId','interactionId')}})
        with Lease(self.lock,timeout=30):
            self._initialize();records=self.records();old=records.get(operation)
            if old is None:
                operation,old=next(((key,item) for key,item in records.items()
                                    if all(item['context'][field]==context[field] for field in ('agentId','interactionId','messageId'))),
                                   (operation,None))
            if old:
                if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original reservation input changed')
                if old.get('result'):return old['result']
                if old['state'] in ('sending','unknown'):raise RuntimeFault('unknown_result','Inspect the original invitation receipt; automatic resend is disabled','reconcile_invitation')
                context=old['context']
            if any(key!=operation and item['context']['agentId']==context['agentId'] and item['context']['interactionId']==context['interactionId'] and item['state'] in ('sending','unknown') for key,item in records.items()):
                raise RuntimeFault('unknown_result','This interaction has an unresolved invitation','reconcile_invitation')
            session=self.calendar.session() if hasattr(self.calendar,'session') else nullcontext(self.calendar)
            with session as calendar:
                rules={**self.rules,**getattr(calendar,'rules',{})}
                allowed=self._allowed(start,end,rules)
                conflict=not allowed or self._busy(calendar,records,start,end,operation)
                record={'fingerprint':fingerprint,'context':dict(context),'input':args,'start':start.timestamp(),'end':end.timestamp(),
                        'reservationId':uuid.uuid5(uuid.NAMESPACE_URL,'ioa-reservation:'+operation).hex,'state':'reserved'}
                if conflict:
                    result={'status':'conflict','reason':'outside_rules' if not allowed else 'busy','inviteSubmitted':False,
                            **self._suggest(calendar,records,start,end,rules,operation)}
                    self._save(operation,{**record,'state':'conflict','result':result});return result
                if hasattr(calendar,'assert_current'):calendar.assert_current()
                self._save(operation,record)
                self._save(operation,{**record,'state':'sending'})
        uid='ioa-'+record['reservationId']+'@jiuwen-ioa-lab'
        try:
            receipt=self.notification.send(uid,args['title'],start,end,self.recipient)
            if receipt.get('status')!='invite_submitted':raise RuntimeError('Invitation submission was not confirmed')
        except Exception:
            with Lease(self.lock,timeout=30):self._save(operation,{**record,'state':'unknown','requestId':uid})
            raise RuntimeFault('unknown_result','Invitation outcome needs review; reservation retained, automatic resend disabled','reconcile_invitation') from None
        result={'status':'invite_submitted','reservationId':record['reservationId'],'inviteSubmitted':True,'calendar':'awaiting_acceptance'}
        with Lease(self.lock,timeout=30):self._save(operation,{**record,'state':'submitted','requestId':uid,'receipt':receipt,'result':result})
        return result

    def status(self,args,context):
        if next(Draft202012Validator(STATUS).iter_errors(args),None):raise RuntimeFault('invalid_input','Use the original reservationId')
        record=next((item for item in self.records().values() if item['reservationId']==args['reservationId']),None)
        if not record or any(record['context'][key]!=context[key] for key in ('agentId','interactionId')):
            raise RuntimeFault('forbidden','Reservation is outside this interaction')
        return record.get('result') or {'status':record['state'],'reservationId':record['reservationId'],'inviteSubmitted':None,'calendar':'unknown'}

    def close(self):
        for component in (self.calendar,self.notification):
            client=getattr(component,'http',None)
            if client:client.close()


def configure(options,*,home,profile,base_dir):
    if not isinstance(options,dict) or set(options)-{'calendar','notification','recipient','rules','requires_steps'} or not {'calendar','notification','recipient'}<=set(options):
        raise RuntimeFault('invalid_input','Calendar tools need calendar, notification and a fixed recipient')
    def path(value):
        result=Path(value).expanduser()
        return result.resolve() if result.is_absolute() else (Path(base_dir)/result).resolve()
    source=options['calendar'];notice=options['notification'];recipient=address(options['recipient'])
    if not isinstance(source,dict) or not isinstance(notice,dict):raise RuntimeFault('invalid_input','Use explicit calendar and notification objects')
    if source.get('backend')=='local' and set(source)=={'backend','schedule_file'}:
        calendar=LocalSchedule(path(source['schedule_file']));calendar.read()
    elif source.get('backend')=='graph' and set(source)=={'backend','client_id','token_file'}:
        calendar=GraphCalendar(source['client_id'],path(source['token_file']),recipient)
        if not calendar.path.is_file():raise RuntimeFault('not_ready','Authorize the configured calendar before starting its tool')
    else:raise RuntimeFault('invalid_input','Choose an explicit local schedule or Graph calendar')
    if (calendar.path.parent/'mailbox.sqlite').exists():raise RuntimeFault('identity_conflict','Existing mailbox reservations require review; select a separate schedule or restore the original storage')
    allowed={'transport','sender','host','port','username_env','password_env','api_key_env','credentials_file'}
    if set(notice)-allowed:raise RuntimeFault('invalid_input','Notification credentials must use local variable references')
    local=dotenv_values(path(notice['credentials_file']),interpolate=False) if notice.get('credentials_file') else {}
    def credential(name):
        key=notice.get(name)
        if key is None:return ''
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key):raise RuntimeFault('invalid_input','Invalid credential variable name')
        return os.environ.get(key,local.get(key) or '')
    if notice.get('transport')=='agentmail':
        notification=AgentMailNotification(notice['sender'],credential('api_key_env'))
    elif notice.get('transport') in ('smtp','smtp-capture'):
        notification=SMTPNotification(notice['host'],notice.get('port',587),notice['sender'],username=credential('username_env'),password=credential('password_env'),test=notice['transport']=='smtp-capture')
    else:raise RuntimeFault('invalid_input','Choose an explicit invitation transport')
    required=options.get('requires_steps',[])
    if not isinstance(required,list) or any(not isinstance(step,str) or not step for step in required):raise RuntimeFault('invalid_input','Use declared step names')
    service=Appointments(calendar,notification,recipient,rules=options.get('rules'))
    revision='calendar-2:'+digest(options)
    return BusinessTools((ToolBinding('calendar.book','每条来信只按原输入决定一次预约；冲突返回建议，须发送方用新消息改约。不冲突则预留并提交邀请；invite_submitted只表示等待本人接受。',INPUT,service.book,'notification',revision,tuple(required)),
                          ToolBinding('calendar.status','查询本交互既有预约，不重发邀请。',STATUS,service.status,'none',revision)),service.close)
