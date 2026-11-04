"""Structured mailbox commands for the user-owned Jiuwen skill."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from .messaging import MailboxClient


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['interfaces','publish','send','sync','messages','message','claim','complete'])
    parser.add_argument('--args',default='{}')
    parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv);client=None
    try:
        base,name,password=(os.environ[k] for k in ('IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD'))
        key=hashlib.sha256((base.rstrip('/')+'|'+name).encode()).hexdigest()[:20]
        home=Path(os.environ.get('IOALAB_HOME',str(Path.home()/'.ioalab'/key)))
        client=MailboxClient(base,name,password,home,connect_identity=True);params=json.loads(args.args_file.read_text(encoding='utf-8-sig') if args.args_file else args.args)
        if args.command=='messages': result=client.api('GET','/messaging/messages')
        elif args.command=='message': result=client.get(params['id'])
        else: result=getattr(client,args.command)(**params)
        print(json.dumps(result,ensure_ascii=False,default=str))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));raise SystemExit(1)
    finally:
        if client: client.close()


if __name__=='__main__': main()
