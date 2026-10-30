"""Explicit independent background messaging clients; no core-owned assistants."""
import argparse,json,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/access/sdk'))
from ioalab_sdk import LabClient
from ioalab_sdk.messaging import MailboxClient

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--accounts',type=Path,required=True,help='Local JSON array of username/password; never commit it');p.add_argument('--home',type=Path,default=ROOT/'data/message-experiments');p.add_argument('--count',type=int,default=5);args=p.parse_args()
    accounts=json.loads(args.accounts.read_text(encoding='utf-8'));experiment=uuid.uuid4().hex;clients=[]
    if len(accounts)<2:raise ValueError('At least two independently configured accounts are required')
    try:
        for a in accounts:
            home=args.home/a['username'];identity=LabClient(args.base,a['username'],a['password'],home,source='experiment');identity.close()
            box=MailboxClient(args.base,a['username'],a['password'],home);clients.append(box)
            box.publish('experiment.ping',{'description':'显式背景消息实验','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False},'allowedSenders':[a['username'] for a in accounts]})
        for round_ in range(args.count):
            mids=[c.send(accounts[(i+1)%len(accounts)]['username'],'experiment.ping',{'text':'experiment '+experiment+' round '+str(round_)})['id'] for i,c in enumerate(clients)]
            for c in clients:c.sync();c.process(lambda m:{'status':'received','source':'experiment','experiment':experiment})
            print(json.dumps({'experiment':experiment,'round':round_,'messages':mids,'source':'experiment'}),flush=True);time.sleep(1)
    finally:
        for c in clients:c.close()

if __name__=='__main__':main()
