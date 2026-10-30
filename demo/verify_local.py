"""Public SDK E2E evidence against a running local_stack; no direct database writes."""
import argparse,json,sys,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src/access/sdk'),str(ROOT/'official-services'),str(ROOT/'experiments')]
from ioalab_sdk import LabClient
from ioalab_sdk.messaging import MailboxClient
from catalog import definitions
from traffic import run_order

def wait_message(box,mid,timeout=50):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        m=box.get(mid)
        if m['state'] in ('completed','rejected','expired'):return m
        time.sleep(.3)
    raise TimeoutError('Message has not completed: '+mid)

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--skip-order',action='store_true');a=p.parse_args()
    config=json.loads(Path(a.config).read_text(encoding='utf-8'));folder=Path(config['folder']);b=config['accounts']['b']
    evidence={'base':config['base'],'fixtures':config['fixtures'],'realJiuwen':bool(config.get('swarm'))}
    if not a.skip_order:
        client=LabClient(config['base'],b['username'],b['password'],folder/'client-b')
        try:
            d=next(d for d in definitions() if d['id']=='med-reminder')
            result=run_order(client,d,config['provider']+'/a2a/providers/svc-med-reminder')
            assert result['status']=='accepted',result
            until=time.monotonic()+15
            while True:
                view=client.api('GET','/orders/'+result['cardDigest'])
                if view['state']=='completed':break
                if time.monotonic()>until:raise TimeoutError('Provider did not close the contract')
                time.sleep(.2)
            evidence['delegation']={'cardDigest':result['cardDigest'],'state':view['state'],'signatures':view['signatures'],'paidPeriods':view['paidPeriods'],'localWasmAcceptance':True}
        finally:client.close()
    box=MailboxClient(config['base'],b['username'],b['password'],folder/'client-b',connect_identity=True)
    try:
        to=config['accounts']['a']['username']
        definitions_=box.interfaces(to);assert {'message.forward','meeting.request'}<={i['name'] for i in definitions_}
        if config.get('swarm'):
            m=box.send(to,'message.forward',{'text':'你好，管理员。我已通过 IoA 消息中心发送项目进展，请查看会议预约。'})
            receipt=wait_message(box,m['id']);assert receipt['result']['status']=='delivered',receipt
            evidence['forward']={'id':m['id'],'state':receipt['state'],**receipt['result']}
        start=datetime.now(timezone.utc)+timedelta(days=1)
        while start.weekday()>4:start+=timedelta(days=1)
        start=start.replace(hour=2,minute=0,second=0,microsecond=0)
        payload={'title':'Jiuwen IoA Lab 项目交流','start':start.isoformat(),'end':(start+timedelta(minutes=30)).isoformat(),'timeZone':'Asia/Shanghai'}
        # Preserve prior evidence: on repeat select a non-overlapping slot.
        for hour in range(2,9):
            slot=start.replace(hour=hour);payload.update(start=slot.isoformat(),end=(slot+timedelta(minutes=30)).isoformat())
            m=box.send(to,'meeting.request',payload);receipt=wait_message(box,m['id'])
            if receipt['result']['status']=='invite_submitted':break
        assert receipt['result']['status']=='invite_submitted',receipt
        assert list((folder/'mail').glob('*.eml')),'SMTP capture did not receive invitation'
        evidence['meeting']={'id':m['id'],'state':receipt['state'],**receipt['result']}
        m=box.send(to,'meeting.request',payload);conflict=wait_message(box,m['id']);assert conflict['result']['status']=='no_time',conflict
        evidence['conflict']={'id':m['id'],**conflict['result']}
        path=folder/('evidence-'+str(int(time.time()))+'.json');path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(evidence,ensure_ascii=False,indent=2));print('Evidence: '+str(path))
    finally:box.close()

if __name__=='__main__':main()
