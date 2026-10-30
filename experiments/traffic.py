"""Independent background client using public APIs; never imported by the core."""
import argparse
import json
import os
import sys
import time
import uuid
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src/access/sdk'),str(ROOT/'official-services')]
from catalog import definitions
from ioalab_sdk import LabClient


def run_order(client, definition, url, *, wait_seconds=90):
    template=client.templates(url)[0]
    name=definition['deliverable']['name']
    values=definition.get('sample_spec',{})
    schema=definition['deliverable'].get('specSchema',{})
    values={k:v for k,v in values.items() if 'const' not in schema.get('properties',{}).get(k,{})}
    # A2C uses a restricted object schema; top-level properties may also be direct keys.
    values={k:v for k,v in values.items() if 'const' not in schema.get(k,{})}
    materials={}
    for inp in definition.get('inputs',[]):
        materials[inp['name']]=(b'x'*10,definition.get('sample_inputs',{}).get(inp['name'],{}))
    if template['formation']=='proposal':
        out=client.request(template['templateDigest'],{name:values},materials=materials,confirmed=True)
        if out.get('returned') or out.get('overBudget'):
            raise RuntimeError(str(out))
        cd=out['cardDigest']; client.adopt(cd,confirmed=True)
    else:
        out=client.draft(template['templateDigest'],{name:values},materials=materials,sign1='client',sign7='client')
        cd=out['cardDigest']; result=client.sign(cd,confirmed=True)
        if result.get('declined'): raise RuntimeError(str(result))
    client.start(cd,confirmed=True)
    until=time.monotonic()+max(1,float(wait_seconds))
    fetched=False
    while time.monotonic()<until:
        state=client.poll(cd)
        for event in state['events']:
            if event['type']=='step-request':
                client.answer_step(cd,True,confirmed=True)
            if event['type']=='anchored' and not fetched:
                client.fetch(cd,event['period']); fetched=True
        if fetched:
            verdict=client.verify(cd,1)
            if verdict['status']=='pass':
                client.accept(cd,1,confirmed=True)
                return {'cardDigest':cd,'status':'accepted'}
            if verdict['status']=='fail':
                return {'cardDigest':cd,'status':'failed-verification'}
        time.sleep(.2)
    raise TimeoutError('Service did not deliver within the experiment wait limit')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--base',default=os.environ.get('IOALAB_BASE_URL','http://127.0.0.1:8750'))
    p.add_argument('--account',default='ISC_001')
    p.add_argument('--password',default='001')
    p.add_argument('--count',type=int,default=1)
    p.add_argument('--service')
    p.add_argument('--domain',help='Domain id such as health or writing')
    p.add_argument('--home',type=Path,default=Path.home()/'.ioalab-experiments')
    args=p.parse_args()
    experiment=uuid.uuid4().hex
    version=(ROOT/'experiments/VERSION').read_text().strip()
    catalog={d['id']:d for d in definitions()}
    client=LabClient(args.base,args.account,args.password,args.home/args.account,source='experiment')
    try:
        for i in range(args.count):
            started=time.monotonic()
            hits=client.search(args.service or args.domain or '文案',48)['hits']
            if args.domain:
                hits=[h for h in hits if (h['card']['url'].rsplit('svc-',1)[-1] in catalog and
                       args.domain in catalog[h['card']['url'].rsplit('svc-',1)[-1]].get('category',''))]
            selected=next((h for h in hits if not args.service or h['card']['url'].endswith('svc-'+args.service)),None)
            if selected is None: raise RuntimeError('No matching online service')
            url=selected['card']['url']; sid=url.rsplit('svc-',1)[-1]
            result=run_order(client,catalog[sid],url)
            record={'experiment':experiment,'version':version,'iteration':i,'service':sid,
                    'formation':catalog[sid].get('formation','preapproval'),'elapsed':time.monotonic()-started,
                    'inputDigest':hashlib.sha256(json.dumps(catalog[sid].get('sample_spec',{}),sort_keys=True).encode()).hexdigest(),**result}
            with (client.home/'experiments.jsonl').open('a',encoding='utf-8') as log:
                log.write(json.dumps(record,ensure_ascii=False)+'\n')
            print(json.dumps(record,ensure_ascii=False))
    finally:
        client.close()


if __name__=='__main__':
    main()
