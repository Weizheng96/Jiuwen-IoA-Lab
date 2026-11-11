"""The official offline editor shares real reservations without an SDK identity."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from ioalab_tools.appointments import Appointments
from ioalab_tools.schedule import LocalSchedule
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_business_tools import context
from test_assistant import appointment


def test_explicit_offline_file_shows_unknown_reservation_without_credentials_or_reset(tmp_path):
    calendar=LocalSchedule(tmp_path/'owner'/'availability.json')
    env={key:value for key,value in os.environ.items() if not key.startswith('IOALAB_')}
    env['PYTHONUTF8']='1'
    def run(command):
        result=subprocess.run([sys.executable,'-m','ioalab_tools.schedule_cli',command,'--file',str(calendar.path)],
                              cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        return result,json.loads(result.stdout)
    result,initial=run('init');assert result.returncode==0,initial
    assert initial['appointments']==[] and not calendar.path.with_name(calendar.path.name+'.ioa').exists()
    class Notification:
        calls=0
        def send(self,*args):self.calls+=1;raise TimeoutError('explicit unknown external effect')
    notification=Notification();tool=Appointments(calendar,notification,'owner@example.test');original=context()
    with pytest.raises(RuntimeFault):tool.book(appointment(),original)
    result,view=run('show');assert result.returncode==0,view
    assert view['revision']==initial['revision'] and view['appointments'][0]['state']=='unknown'
    assert 'operationId' not in json.dumps(view) and 'context' not in json.dumps(view)
    assert not (tmp_path/'runtime.sqlite').exists() and not (calendar.path.parent/'mailbox.sqlite').exists()
    # Lost durable state must stop the old source, never turn it into empty availability.
    (tool.root/'appointments.sqlite').unlink()
    result,error=run('show');assert result.returncode==1 and 'Restore the original' in error['error']
    with pytest.raises(RuntimeFault,match='Restore the original'):tool.book(appointment(),context())
    assert notification.calls==1 and not (tool.root/'appointments.sqlite').exists()


def test_offline_settlement_commands_never_send_and_report_states(tmp_path):
    calendar=LocalSchedule(tmp_path/'availability.json');calendar.init()
    env={key:value for key,value in os.environ.items() if not key.startswith('IOALAB_')};env['PYTHONUTF8']='1'
    def run(command,arguments):
        result=subprocess.run([sys.executable,'-m','ioalab_tools.schedule_cli',command,'--file',str(calendar.path),'--args',json.dumps(arguments)],
                              cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        return result.returncode,json.loads(result.stdout)
    class Lost:
        calls=0
        def send(self,*args):self.calls+=1;raise TimeoutError('explicit unknown external effect')
    notification=Lost();tool=Appointments(calendar,notification,'owner@example.test')
    with pytest.raises(RuntimeFault):tool.book(appointment(),context())
    reservation=next(iter(tool.records().values()))['reservationId']
    code,error=run('accept',{'reservationId':reservation});assert code==1 and 'applies only to' in error['error']
    code,view=run('reconcile',{'reservationId':reservation,'outcome':'submitted'});assert code==0,view
    assert view['appointment']['state']=='submitted' and view['appointments'][0]['state']=='submitted'
    code,view=run('release',{'reservationId':reservation,'reason':'declined'});assert code==0,view
    assert view['appointments'][0]['state']=='released' and len(view['appointments'][0]['settlements'])==2
    assert 'operationId' not in json.dumps(view) and notification.calls==1
