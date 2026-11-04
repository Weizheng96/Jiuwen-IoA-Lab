"""Sample real Docker counters; never manufactures missing model/timing metrics."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time
import re


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--deployment',required=True)
    parser.add_argument('--phase',required=True,choices=['baseline','three-idle','one-active','three-active','after-reset'])
    parser.add_argument('--seconds',type=int,default=60)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,30}',args.deployment):parser.error('deployment must be an exact reviewed trial prefix')
    if not 1 <= args.seconds <= 3600:parser.error('seconds must be 1..3600')
    until=time.monotonic()+args.seconds
    with args.out.open('x',encoding='utf-8') as stream:
        while time.monotonic()<until:
            selected=subprocess.run(['docker','ps','--filter','name=^'+args.deployment+'-','--format','{{.ID}}'],
                check=True,capture_output=True,text=True).stdout.split()
            rows=[]
            if selected:
                result=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',*selected],
                    check=True,capture_output=True,text=True)
                rows=[json.loads(line) for line in result.stdout.splitlines() if line]
            stream.write(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'phase':args.phase,'containers':rows,'missing':['model tokens/calls','business completion/queue/reset latencies',
                    'host baseline CPU/PSS','per-process PSS','disk volume growth']})+'\n')
            stream.flush()
            time.sleep(1)


if __name__=='__main__':main()
