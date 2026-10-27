"""A test invitation must never be retried after an uncertain mail submission."""
import importlib.util
import json
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pytest


@pytest.mark.parametrize('uncertain',[False,True])
def test_one_notification_attempt_and_explicit_calendar_limit(tmp_path,uncertain):
    path=Path(__file__).resolve().parents[1]/'demo/verify_notification.py'
    spec=importlib.util.spec_from_file_location('verify_notification',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Mail:
        test=False;sender='test@agentmail.to';calls=0
        def check(self):return {'messageSent':False}
        def send(self,*args):
            self.calls+=1
            if uncertain:raise TimeoutError()
            return {'status':'invite_submitted','calendar':'awaiting_acceptance'}
    mail=Mail();start=datetime.now(timezone.utc)+timedelta(days=1)
    item={'title':'Transport test','start':start.isoformat(),'end':(start+timedelta(minutes=15)).isoformat()}
    if uncertain:
        with pytest.raises(RuntimeError,match='unknown'):module.run_check(mail,'receiver@example.test',item,tmp_path)
    else:assert module.run_check(mail,'receiver@example.test',item,tmp_path)['sendState']=='submitted'
    saved=json.loads((tmp_path/'evidence.json').read_text())
    assert saved['calendarConflictCheck']=='not performed' and saved['outlookAccepted']=='not yet verified'
    assert saved['sendState']==('unknown' if uncertain else 'submitted')
    with pytest.raises(ValueError,match='attempted'):module.run_check(mail,'receiver@example.test',item,tmp_path)
    assert mail.calls==1
