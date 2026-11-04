"""Independent demo receiver. Explicit calendar fixture; real Jiuwen is optional and labeled."""
import json,os,sys,time
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/access/sdk'))
from ioalab_sdk.messaging import MailboxClient
from ioalab_tools.handlers import AssistantHandlers
from ioalab_tools.calendar import SMTPNotification
from ioalab_sdk.swarm_web import SwarmWebBridge

class CalendarFixture:
    def __init__(self,path): self.path=Path(path)
    def busy(self,start,end):
        return any(datetime.fromisoformat(e['start'])<end and start<datetime.fromisoformat(e['end']) for e in json.loads(self.path.read_text(encoding='utf-8')))

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--calendar-fixture',required=True);args=p.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    a=config['accounts']['a'];box=MailboxClient(config['base'],a['username'],a['password'],Path(config['folder'])/'assistant',connect_identity=True)
    bridge=SwarmWebBridge(config['swarm'],box.store) if config.get('swarm') else None
    handler=AssistantHandlers(box,bridge=bridge,calendar=CalendarFixture(args.calendar_fixture),notification=SMTPNotification('127.0.0.1',config['smtpPort'],'ioa-notification@example.test',test=True),recipient=config.get('recipient','owner@example.test'))
    handler.publish([config['accounts']['b']['username']]);print('Ready: independent receiver; CALENDAR FIXTURE + SMTP CAPTURE; Jiuwen '+('real' if bridge else 'not configured'),flush=True)
    try:
        while True:
            box.sync(wait=2)
            for message in box.process(handler): print(json.dumps({'id':message['id'],'state':message['state'],'result':message['result']},ensure_ascii=False),flush=True)
    finally: box.close()

if __name__=='__main__':main()
