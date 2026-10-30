"""Editable availability, lost-update protection and real message/SMTP integration."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import pytest
from ioalab_sdk.schedule import LocalSchedule, instant
from ioalab_sdk.assistant import AssistantHandlers
from ioalab_sdk.messaging import MailboxClient
from ioalab_sdk.calendar import SMTPNotification
from _support import new_user, PASSWORD
from test_assistant import appointment


def test_schedule_revision_backup_reload_and_interval_edges(tmp_path):
    calendar=LocalSchedule(tmp_path/'schedule.json')
    first=calendar.init();document=first['document']
    document['events']=[{'id':'focus','title':'PRIVATE-SCHEDULE-TITLE','start':'2026-10-28T10:00:00+08:00','end':'2026-10-28T11:00:00+08:00'}]
    old=calendar.path.read_bytes()
    current=calendar.replace(document,first['revision'])
    assert calendar.path.with_name('schedule.json.bak').read_bytes()==old
    view=calendar.read()
    assert view.busy(instant('2026-10-28T02:30:00Z'),instant('2026-10-28T03:30:00Z'))
    assert not view.busy(instant('2026-10-28T11:00:00+08:00'),instant('2026-10-28T11:30:00+08:00'))
    with pytest.raises(ValueError,match='revision conflict'):calendar.replace(document,first['revision'])
    with pytest.raises(FileExistsError):calendar.init()
    document['events'][0]['status']='cancelled'
    calendar.replace(document,current['revision'])
    assert not calendar.read().busy(instant('2026-10-28T02:30:00Z'),instant('2026-10-28T03:30:00Z'))
    with pytest.raises(ValueError,match='changed'):view.assert_current()


@pytest.mark.parametrize('corruption',['timezone','duplicate','naive','reversed','rules','shape'])
def test_invalid_schedule_never_implies_empty_calendar(tmp_path,corruption):
    calendar=LocalSchedule(tmp_path/'schedule.json');document=calendar.init()['document']
    event={'id':'meeting','title':'PRIVATE-SCHEDULE-TITLE','start':'2026-10-28T10:00:00+08:00','end':'2026-10-28T11:00:00+08:00'}
    document['events']=[event]
    if corruption=='timezone':document['timeZone']='Unknown/Zone'
    if corruption=='duplicate':document['events'].append(dict(event))
    if corruption=='naive':event['start']='2026-10-28T10:00:00'
    if corruption=='reversed':event['end']=event['start']
    if corruption=='rules':document['rules']['endHour']=document['rules']['startHour']
    if corruption=='shape':event['status']='PRIVATE-SCHEDULE-TITLE'
    calendar.path.write_text(json.dumps(document),encoding='utf-8')
    with pytest.raises(Exception) as error:calendar.read()
    assert 'PRIVATE-SCHEDULE-TITLE' not in str(error.value)
    calendar.path.unlink()
    with pytest.raises(FileNotFoundError):calendar.read()


def test_local_rules_busy_time_and_pending_invitation_end_to_end(platform,tmp_path):
    from smtp_capture import Capture
    a,b=new_user(platform),new_user(platform)
    receiver=MailboxClient(platform.s.base_url,a['username'],PASSWORD,tmp_path/'a',connect_identity=True)
    sender=MailboxClient(platform.s.base_url,b['username'],PASSWORD,tmp_path/'b',connect_identity=True)
    capture=Capture(tmp_path/'mail').start()
    calendar=LocalSchedule(tmp_path/'a/schedule.json');state=calendar.init()
    payload=appointment()
    try:
        document=state['document']
        document['events']=[{'id':'busy','title':'PRIVATE-SCHEDULE-TITLE','start':payload['start'],'end':payload['end']}]
        state=calendar.replace(document,state['revision'])
        handler=AssistantHandlers(receiver,calendar=calendar,notification=SMTPNotification('127.0.0.1',capture.port,'notice@example.test',test=True),recipient='owner@example.test')
        handler.publish([b['username']])
        def request():
            message=sender.send(a['username'],'meeting.request',payload)
            receiver.sync();receiver.process(handler)
            return sender.get(message['id'])['result']
        result=request()
        assert result['reason']=='calendar_conflict' and not capture.messages
        assert 'PRIVATE-SCHEDULE-TITLE' not in json.dumps(result)
        document['events']=[];document['rules']['startHour']=11
        state=calendar.replace(document,state['revision'])
        assert request()['reason']=='outside_authorized_rules' and not capture.messages
        document['rules']['startHour']=9
        state=calendar.replace(document,state['revision'])
        assert request()['status']=='invite_submitted' and len(capture.messages)==1
        assert request()['reason']=='local_pending_invitation' and len(capture.messages)==1
        # Manual edits cannot release an invitation already persisted in the receiver.
        state=calendar.replace(document,state['revision'])
        assert request()['reason']=='local_pending_invitation'
        calendar.path.write_text('{invalid',encoding='utf-8')
        assert request()['status']=='failed' and len(capture.messages)==1
        calendar.path.unlink()
        assert request()['status']=='failed' and len(capture.messages)==1
    finally:
        capture.close();receiver.close();sender.close()


def test_schedule_cli_works_offline_and_keeps_pending_records(tmp_path):
    from ioalab_sdk.store import LocalStore
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(root/'src/access/sdk'),'IOALAB_HOME':str(tmp_path)}
    for key in ('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD','IOALAB_SCHEDULE_FILE'):env.pop(key,None)
    def run(command):
        output=subprocess.run([sys.executable,'-m','ioalab_sdk.cli','schedule',command],env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        assert output.returncode==0,output.stdout+output.stderr
        return json.loads(output.stdout)
    initial=run('init')
    store=LocalStore(tmp_path/'mailbox.sqlite')
    try:store.put('appointments','request-one',{'uid':'fixed','start':1,'end':2,'state':'unknown','secret-extra':'not-a-calendar-field'})
    finally:store.close()
    result=run('show')
    assert result['revision']==initial['revision'] and result['appointments'][0]['state']=='unknown'
    assert 'secret-extra' not in json.dumps(result)
