"""Owner-run Microsoft device-code login for Graph calendar reads and Outlook SMTP sending; tokens stay in local files."""
import argparse
import json
from pathlib import Path
import sys

from ioalab_sdk.runtime import configure_stdio
from .calendar import GraphCalendar, OutlookSMTPAuth, address


def main(argv=None):
    configure_stdio()
    parser=argparse.ArgumentParser(prog='ioalab-outlook',description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    graph=commands.add_parser('graph-login',help='authorize read-only calendarView for the configured recipient')
    graph.add_argument('--client-id',required=True,help='calendar.client_id of the business-tool configuration')
    graph.add_argument('--account',required=True,help='the configured recipient whose calendar is read')
    graph.add_argument('--token-file',type=Path,required=True,help='calendar.token_file of the business-tool configuration')
    smtp=commands.add_parser('smtp-login',help='authorize SMTP.Send for the configured notification sender')
    smtp.add_argument('--client-id',required=True,help='notification.client_id of the business-tool configuration')
    smtp.add_argument('--sender',required=True,help='notification.sender, the invitation organizer mailbox')
    smtp.add_argument('--token-file',type=Path,required=True,help='notification.token_file of the business-tool configuration')
    smtp.add_argument('--host',default='smtp-mail.outlook.com',help='notification.host, a Microsoft SMTP endpoint')
    args=parser.parse_args(argv)
    auth=None
    try:
        path=args.token_file.expanduser().resolve()
        if args.command=='graph-login':
            auth=GraphCalendar(args.client_id,path,address(args.account));resource,account='graph',args.account
        else:
            auth=OutlookSMTPAuth(args.client_id,path,args.sender,host=args.host);resource,account='smtp',args.sender
        # The device-code instruction is for the owner; stdout stays one JSON line.
        auth.login(prompt=lambda message:print(message,file=sys.stderr,flush=True))
        print(json.dumps({'status':'authorized','resource':resource,'account':account,'tokenFile':str(path),'messageSent':False},ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));return 1
    finally:
        if auth:auth.http.close()


if __name__=='__main__':raise SystemExit(main())
