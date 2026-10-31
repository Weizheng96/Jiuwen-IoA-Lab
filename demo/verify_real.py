"""Opt-in real production acceptance against already configured independent participants."""
import argparse,copy,json,os,sys,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src/access/sdk'),str(ROOT/'src/official/services'),str(ROOT/'experiments')]
from ioalab_sdk import LabClient
from ioalab_services.catalog import definitions
from traffic import run_order

def main():
    p=argparse.ArgumentParser();p.add_argument('--provider',required=True,help='Independent Provider base URL');p.add_argument('--service',default='med-reminder');p.add_argument('--spec-file',type=Path,required=True);p.add_argument('--home',type=Path,required=True);p.add_argument('--wait',type=int,default=300);args=p.parse_args()
    health=httpx.get(args.provider.rstrip('/')+'/health',timeout=10,trust_env=False).raise_for_status().json()
    if not health.get('ready') or health.get('execution')!='swarm':raise ValueError('Real acceptance requires a ready Provider explicitly reporting swarm execution')
    definition=copy.deepcopy(next(d for d in definitions() if d['id']==args.service))
    if definition.get('inputs'):raise ValueError('For a service with materials use the full SDK workflow and real authorized materials')
    spec=json.loads(args.spec_file.read_text(encoding='utf-8-sig'));definition['sample_spec']=spec.get(definition['deliverable']['name'],spec)
    client=LabClient(os.environ['IOALAB_BASE_URL'],os.environ['IOALAB_USERNAME'],os.environ['IOALAB_PASSWORD'],args.home,source='experiment')
    try:
        result=run_order(client,definition,args.provider.rstrip('/')+'/a2a/providers/svc-'+args.service,wait_seconds=args.wait)
        assert result['status']=='accepted',result
        result.update(execution='swarm',wasmAcceptance=True,service=args.service)
        path=args.home/('real-acceptance-'+str(int(time.time()))+'.json');path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    finally:client.close()

if __name__=='__main__':main()
