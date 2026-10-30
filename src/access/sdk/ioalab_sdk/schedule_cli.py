"""Offline schedule editing for the same identity directory used by the receiver."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from .schedule import configured


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['init','show','replace'])
    parser.add_argument('--args',default='{}')
    parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv)
    try:
        home=os.getenv('IOALAB_HOME')
        if not home:
            key=hashlib.sha256((os.environ['IOALAB_BASE_URL'].rstrip('/')+'|'+os.environ['IOALAB_USERNAME']).encode()).hexdigest()[:20]
            home=Path.home()/'.ioalab'/key
        home=Path(home).resolve()
        schedule=configured(home)
        params=json.loads(args.args_file.read_text(encoding='utf-8-sig') if args.args_file else args.args)
        result=getattr(schedule,args.command)(**params)
        # Open read-only: viewing the local schedule never creates an identity or authenticates online.
        result['appointments']=[]
        database=home/'mailbox.sqlite'
        if database.exists():
            connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
            try:
                rows=connection.execute("SELECT k,v FROM documents WHERE tbl='appointments'").fetchall()
                for mid,raw in rows:
                    record=json.loads(raw)
                    result['appointments'].append({'messageId':mid,**{k:record[k] for k in ('start','end','state','uid') if k in record}})
            finally:connection.close()
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
