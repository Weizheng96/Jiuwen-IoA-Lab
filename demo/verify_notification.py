"""Explicit one-message notification check, independent of calendar authorization."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import uuid

ROOT=Path(__file__).resolve().parents[1]


def run_check(sender,recipient,item,out):
    if sender.test:raise ValueError('Real notification check must not use SMTP capture')
    start,end=(datetime.fromisoformat(item[k].replace('Z','+00:00')) for k in ('start','end'))
    if start.tzinfo is None or end.tzinfo is None or not datetime.now(timezone.utc)<start<end:raise ValueError('Choose a future interval with explicit time zones')
    if not isinstance(item['title'],str) or not 1<=len(item['title'])<=200 or any(c in item['title'] for c in '\r\n'):raise ValueError('Invalid appointment title')
    out=Path(out);out.mkdir(parents=True,exist_ok=True);path=out/'evidence.json'
    if path.exists():raise ValueError('This output already has an attempted send; inspect it instead of resending')
    # Connection/auth checks never send mail, so failure here creates no send intent.
    check=sender.check()
    evidence={'scope':'notification transport only','calendarConflictCheck':'not performed','outlookReceived':'not yet verified','outlookAccepted':'not yet verified','uid':'ioa-notification-check-'+uuid.uuid4().hex+'@jiuwen-ioa-lab','sender':sender.sender,'recipient':recipient,'appointment':{k:item[k] for k in ('title','start','end')},'authentication':check,'sendState':'attempting'}
    with path.open('x',encoding='utf-8') as stream:json.dump(evidence,stream,ensure_ascii=False,indent=2)
    try:
        receipt=sender.send(evidence['uid'],item['title'],start,end,recipient)
        evidence.update(sendState='submitted',receipt=receipt)
    except Exception as exc:
        evidence.update(sendState='unknown',error=type(exc).__name__)
        path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raise RuntimeError('Send result is unknown; review the provider before another attempt') from None
    path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return evidence


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env',type=Path,required=True)
    parser.add_argument('--appointment-file',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--send',action='store_true',help='Actually submit one invitation to the configured recipient')
    args=parser.parse_args()
    if not args.send:parser.error('--send is required for actual email delivery')
    import os,sys
    sys.path[:0]=[str(ROOT/'src/access/sdk'),str(ROOT/'deploy')]
    from configure_assistant import read_env
    from ioalab_tools.calendar import address
    for key,value in read_env(args.env).items():
        if key.startswith('IOALAB_'):os.environ[key]=value
    spec=importlib.util.spec_from_file_location('notification_assistant',ROOT/'personal-assistant/assistant.py')
    assistant=importlib.util.module_from_spec(spec);spec.loader.exec_module(assistant)
    sender=assistant.notification(Path(os.environ.get('IOALAB_HOME','data/personal-assistant')).resolve())
    recipient=address(os.getenv('IOALAB_RECIPIENT_EMAIL','zhengw.cc@outlook.com'))
    item=json.loads(args.appointment_file.read_text(encoding='utf-8-sig'))
    print(json.dumps(run_check(sender,recipient,item,args.out),ensure_ascii=False))


if __name__=='__main__':main()
