"""Explicit one-time import of an authorized Aliyun CSV into the private development source."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from tools.llm import SOURCE, ModelInputError


def protect(path):
    if os.name!='nt':
        os.chmod(path,0o600);return
    result=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,
                          timeout=5,creationflags=subprocess.CREATE_NO_WINDOW,check=True)
    rows=list(csv.reader(result.stdout.decode('utf-8',errors='replace').splitlines()))
    sid=rows[0][1] if rows and len(rows[0])>1 else ''
    if not re.fullmatch(r'S-[0-9-]+',sid):raise ValueError()
    result=subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r','*'+sid+':F','*S-1-5-18:F'],
                          capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise ValueError()


def import_csv(source,*,name,model,destination=SOURCE):
    from deploy.workspace import path_without_links
    from deploy.configuration import model as validate_model
    created=False
    try:
        source=path_without_links(source);destination=path_without_links(destination)
        if not source.is_file() or destination.exists() or not destination.parent.is_dir():raise ValueError()
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name):raise ValueError()
        with source.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.reader(stream))
        if not rows or any(len(row)!=2 for row in rows):raise ValueError()
        fields=dict(rows)
        if len(fields)!=len(rows):raise ValueError()
        endpoint=fields['openAiCompatible'];url=urlsplit(endpoint)
        if url.scheme!='https' or fields.get('apiHost',url.hostname)!=url.hostname:raise ValueError()
        value=validate_model({'name':model,'base_url':endpoint,'api_key':fields['apiKey']})
        document={'profiles':{name:value},'tts':{'profile':name,'model':'qwen3-tts-flash-realtime',
                    'url':'wss://'+url.hostname+'/api-ws/v1/realtime'}}
        descriptor=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);created=True
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as stream:
            protect(destination)  # Empty file only until its ACL is restricted.
            json.dump(document,stream,ensure_ascii=False,indent=2);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
    except Exception:
        if created:destination.unlink(missing_ok=True)
        raise ModelInputError('Private model import failed; original source and existing destination preserved') from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',type=Path,required=True)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--destination',type=Path,default=SOURCE)
    args=parser.parse_args()
    try:import_csv(args.csv,name=args.profile,model=args.model,destination=args.destination)
    except ModelInputError as error:print(str(error));return 1
    print('Private development model source imported; original input preserved');return 0


if __name__=='__main__':raise SystemExit(main())
