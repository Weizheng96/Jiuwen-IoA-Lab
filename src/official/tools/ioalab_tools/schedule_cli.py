"""Edit a participant-owned schedule offline; never create an SDK identity."""
import argparse
import json
from pathlib import Path

from ioalab_sdk.runtime.protocol import configure_stdio
from .appointments import appointment_records
from .schedule import LocalSchedule


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['init','show','replace'])
    parser.add_argument('--file',type=Path,required=True,help='the schedule_file selected in local business-tool configuration')
    parser.add_argument('--args',default='{}')
    parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv)
    try:
        schedule=LocalSchedule(args.file)
        params=json.loads(args.args_file.read_text(encoding='utf-8-sig') if args.args_file else args.args)
        result=getattr(schedule,args.command)(**params)
        result['appointments']=[{key:value[key] for key in ('reservationId','start','end','state','requestId') if key in value}
                                for value in appointment_records(schedule.path).values()]
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
