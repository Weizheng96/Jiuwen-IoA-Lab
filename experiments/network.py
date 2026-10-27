"""Explicit parallel SDK participants for the concept recording; no hosted agents."""
import json
import secrets
import sys
import threading
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'sdk'),str(ROOT/'official-services'),str(ROOT/'experiments')]
from ioalab_sdk import LabClient
from ioalab_sdk.messaging import MailboxClient
from catalog import definitions
from traffic import run_order


class NetworkTraffic:
    def __init__(self,config,folder,count=4):
        self.config,self.folder=config,Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        self.stop=threading.Event()
        self.ready=threading.Event()
        self.records=[]
        self.threads=[]
        self.accounts=[]
        self.boxes=[]
        self.errors=[]
        run=secrets.token_hex(3)
        for i in range(count):
            account={'username':f'lab_{run}_{i+1:02}','password':secrets.token_urlsafe(24)}
            response=httpx.post(config['base']+'/api/auth/register',json=account,trust_env=False,timeout=10)
            response.raise_for_status()
            self.accounts.append(account)
        # These are runtime credentials, never recording metadata or source assets.
        (self.folder/'accounts.json').write_text(json.dumps(self.accounts),encoding='utf-8')

    def start(self):
        for i,account in enumerate(self.accounts):
            client=LabClient(self.config['base'],account['username'],account['password'],self.folder/account['username'],source='experiment')
            box=MailboxClient(self.config['base'],account['username'],account['password'],client.home)
            box.publish('network.note',{'description':'实验客户端交流','inputSchema':{'type':'object','properties':{'text':{'type':'string','maxLength':200}},'required':['text'],'additionalProperties':False}})
            self.boxes.append(box)
            thread=threading.Thread(target=self.run,args=(i,client,box),daemon=True)
            self.threads.append(thread)
            thread.start()
        self.ready.set()

    def run(self,i,client,box):
        self.ready.wait()
        selected=('med-reminder','triage-advice','rehab-plan','med-reminder')[i%4]
        definition=next(d for d in definitions() if d['id']==selected)
        url=self.config['provider']+'/a2a/providers/svc-'+definition['id']
        try:
            iteration=0;orders=0
            while not self.stop.is_set():
                if self.stop.wait(i*.3 if iteration==0 else 4):break
                target=self.accounts[(i+1)%len(self.accounts)]['username']
                message=box.send(target,'network.note',{'text':f'实验交流：第{iteration+1}轮需求同步'})
                self.records.append({'kind':'message','id':message['id'],'actor':i,'iteration':iteration})
                box.sync()
                box.process(lambda m:{'status':'received'})
                # Keep messages active through narration while staying within the initial credit.
                if iteration%6==0 and orders<7:
                    client.search(definition['name'],8)
                    result=run_order(client,definition,url)
                    if result['status']!='accepted':raise RuntimeError('Experiment contract did not pass acceptance')
                    orders+=1
                    self.records.append({'kind':'order','actor':i,'iteration':iteration,'service':selected,'formation':definition.get('formation','preapproval'),**result})
                box.sync()
                box.process(lambda m:{'status':'received'})
                iteration+=1
        except Exception as exc:
            self.errors.append({'actor':i,'error':type(exc).__name__,'detail':str(exc)[:300]})
        finally:
            box.close();client.close()

    def close(self):
        self.stop.set();self.ready.set()
        for thread in self.threads:thread.join(30)
        if any(t.is_alive() for t in self.threads):raise RuntimeError('Experiment participant did not exit')
        proof={'source':'experiment','participants':len(self.accounts),'records':self.records,'errors':self.errors}
        (self.folder/'traffic.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
        return proof
