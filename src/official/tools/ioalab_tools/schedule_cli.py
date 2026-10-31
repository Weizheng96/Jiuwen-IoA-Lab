"""Edit a participant-owned schedule offline; never create an SDK identity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .appointments import appointment_records
from .schedule import LocalSchedule,configured


def legacy_records(home):
    database=home/'mailbox.sqlite'
    if not database.exists():return []
    connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    try:
        return [{'storage':'legacy','messageId':mid,**{key:value[key] for key in ('start','end','state','uid') if key in value}}
                for mid,raw in connection.execute("SELECT k,v FROM documents WHERE tbl='appointments'")
                for value in [json.loads(raw)]]
    finally:connection.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['init','show','replace'])
    parser.add_argument('--file',type=Path,help='the schedule_file selected in local business-tool configuration')
    parser.add_argument('--args',default='{}')
    parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.file:
            schedule=LocalSchedule(args.file)
            home=schedule.path.parent
        else:
            # Preserve explicit legacy operator configuration until its data is migrated.
            home=os.getenv('IOALAB_HOME')
            if not home:
                key=hashlib.sha256((os.environ['IOALAB_BASE_URL'].rstrip('/')+'|'+os.environ['IOALAB_USERNAME']).encode()).hexdigest()[:20]
                home=Path.home()/'.ioalab'/key
            home=Path(home).resolve();schedule=configured(home)
        params=json.loads(args.args_file.read_text(encoding='utf-8-sig') if args.args_file else args.args)
        result=getattr(schedule,args.command)(**params)
        result['appointments']=legacy_records(home)
        result['appointments'].extend({key:value[key] for key in ('reservationId','start','end','state','requestId') if key in value}
                                      for value in appointment_records(schedule.path).values())
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
