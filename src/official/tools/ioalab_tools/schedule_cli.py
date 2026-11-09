"""Edit a participant-owned schedule and settle its reservations offline; never create an SDK identity or send mail."""
import argparse
import json
from pathlib import Path

from ioalab_sdk.runtime.protocol import configure_stdio
from .appointments import SETTLEMENTS,appointment_records,settle
from .schedule import LocalSchedule


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['init','show','replace',*SETTLEMENTS])
    parser.add_argument('--file',type=Path,required=True,help='the schedule_file (or Graph token_file) selected in local business-tool configuration')
    parser.add_argument('--args',default='{}')
    parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv)
    try:
        schedule=LocalSchedule(args.file)
        params=json.loads(args.args_file.read_text(encoding='utf-8-sig') if args.args_file else args.args)
        if args.command in SETTLEMENTS:result={'appointment':settle(schedule.path,args.command,params)}
        else:result=getattr(schedule,args.command)(**params)
        result['appointments']=[{key:value[key] for key in ('reservationId','start','end','state','requestId','settlements') if key in value}
                                for value in appointment_records(schedule.path).values()]
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
