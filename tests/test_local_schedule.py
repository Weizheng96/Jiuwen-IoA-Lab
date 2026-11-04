"""Participant-owned schedule validity and current invitation reservation ownership."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
from ioalab_tools.schedule import LocalSchedule,instant
from ioalab_tools.appointments import Appointments
from ioalab_tools.calendar import SMTPNotification
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_assistant import appointment
from demo.smtp_capture import Capture


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



def test_local_rules_busy_time_and_pending_invitation_end_to_end(tmp_path):
    capture=Capture(tmp_path/'mail').start()
    calendar=LocalSchedule(tmp_path/'schedule.json');state=calendar.init();payload=appointment()
    service=Appointments(calendar,SMTPNotification('127.0.0.1',capture.port,'notice@example.test',test=True),'owner@example.test')
    def request():
        return service.book(payload,{'operationId':str(uuid.uuid4()),'messageId':str(uuid.uuid4()),
            'agentId':'schedule-owner','interactionId':str(uuid.uuid4()),'sessionId':'isolated-schedule-test'})
    try:
        document=state['document'];document['events']=[{'id':'busy','title':'PRIVATE-SCHEDULE-TITLE','start':payload['start'],'end':payload['end']}]
        state=calendar.replace(document,state['revision'])
        result=request();assert result['reason']=='busy' and not capture.messages
        assert 'PRIVATE-SCHEDULE-TITLE' not in json.dumps(result)
        document['events']=[];document['rules']['startHour']=11;state=calendar.replace(document,state['revision'])
        assert request()['reason']=='outside_rules' and not capture.messages
        document['rules']['startHour']=9;state=calendar.replace(document,state['revision'])
        assert request()['status']=='invite_submitted' and len(capture.messages)==1
        assert request()['reason']=='busy' and len(capture.messages)==1
        state=calendar.replace(document,state['revision']);assert request()['reason']=='busy'
        calendar.path.write_text('{invalid',encoding='utf-8')
        with pytest.raises(Exception):request()
        assert len(capture.messages)==1
        calendar.path.unlink()
        with pytest.raises(FileNotFoundError):request()
        assert len(capture.messages)==1
    finally:service.close();capture.close()


def test_schedule_cli_works_offline_and_keeps_pending_records(tmp_path):
    from ioalab_tools.appointments import appointment_records
    calendar=LocalSchedule(tmp_path/'schedule.json');initial=calendar.init()
    class UnknownNotification:
        def send(self,*args):raise TimeoutError('External invitation receipt unknown')
    service=Appointments(calendar,UnknownNotification(),'owner@example.test')
    try:
        with pytest.raises(RuntimeFault):service.book(appointment(),{'operationId':str(uuid.uuid4()),'messageId':str(uuid.uuid4()),
            'agentId':'schedule-owner','interactionId':str(uuid.uuid4()),'sessionId':'isolated-schedule-test'})
        records=appointment_records(calendar.path);assert len(records)==1 and next(iter(records.values()))['state']=='unknown'
        env={**os.environ,'PYTHONUTF8':'1'}
        for key in ('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD','IOALAB_HOME','IOALAB_SCHEDULE_FILE'):env.pop(key,None)
        output=subprocess.run([sys.executable,'-m','ioalab_tools.schedule_cli','show','--file',str(calendar.path)],
            env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        assert output.returncode==0,output.stdout+output.stderr
        result=json.loads(output.stdout)
        assert result['revision']==initial['revision'] and result['appointments'][0]['state']=='unknown'
        assert 'schedule-owner' not in output.stdout and 'sessionId' not in output.stdout
    finally:service.close()
