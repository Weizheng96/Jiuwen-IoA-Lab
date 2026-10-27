"""Independent administrator assistant. Core holds neither model nor calendar credentials."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'sdk'))
from ioalab_sdk.messaging import MailboxClient
from ioalab_sdk.assistant import AssistantHandlers
from ioalab_sdk.calendar import GraphCalendar,SMTPNotification,OutlookSMTPAuth

def graph(home):
    return GraphCalendar(os.getenv('IOALAB_GRAPH_CLIENT_ID',''),home/'calendar-token.json',os.getenv('IOALAB_RECIPIENT_EMAIL','zhengw.cc@outlook.com'))

def notification(home):
    auth=os.getenv('IOALAB_SMTP_AUTH','password').lower()
    if auth not in ('password','oauth2'): raise ValueError('IOALAB_SMTP_AUTH must be password or oauth2')
    host=os.environ['IOALAB_SMTP_HOST'];port=os.getenv('IOALAB_SMTP_PORT','587');sender=os.environ['IOALAB_SMTP_FROM']
    if auth=='oauth2':
        if os.getenv('IOALAB_SMTP_PASSWORD'): raise ValueError('Remove IOALAB_SMTP_PASSWORD when using OAuth2')
        username=os.getenv('IOALAB_SMTP_USERNAME','')
        if username and username.casefold()!=sender.casefold(): raise ValueError('SMTP username and sender must match in OAuth2 mode')
        oauth=OutlookSMTPAuth(os.getenv('IOALAB_SMTP_CLIENT_ID') or os.getenv('IOALAB_GRAPH_CLIENT_ID',''),home/'smtp-token.json',sender,host=host,port=port)
        return SMTPNotification(host,port,sender,oauth=oauth)
    return SMTPNotification(host,port,sender,username=os.getenv('IOALAB_SMTP_USERNAME',''),password=os.getenv('IOALAB_SMTP_PASSWORD',''))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['run','once','calendar-login','smtp-login','smtp-check','check']);parser.add_argument('--env',type=Path);args=parser.parse_args()
    if args.env:
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
        from configure_assistant import read_env
        for key,value in read_env(args.env).items():
            if key.startswith('IOALAB_'):os.environ[key]=value
    home=Path(os.environ.get('IOALAB_HOME','data/personal-assistant')).resolve()
    if args.command=='calendar-login':
        print('请使用被预约邮箱登录：'+os.getenv('IOALAB_RECIPIENT_EMAIL','zhengw.cc@outlook.com'),flush=True)
        graph(home).login();return
    if args.command in ('smtp-login','smtp-check'):
        sender=notification(home)
        if args.command=='smtp-login':
            if not sender.oauth: raise ValueError('smtp-login requires IOALAB_SMTP_AUTH=oauth2')
            print('请使用通知邮箱登录：'+sender.sender,flush=True)
            sender.oauth.login();print(json.dumps({'status':'authorized','sender':sender.sender,'messageSent':False},ensure_ascii=False))
        else: print(json.dumps(sender.check(),ensure_ascii=False))
        return
    if args.command=='check':
        print(json.dumps({'username':os.getenv('IOALAB_USERNAME','ISC_001'),'modelRuntime':'external Jiuwen','swarmConfigured':bool(os.getenv('IOALAB_SWARM_WS')),'calendarAppConfigured':bool(os.getenv('IOALAB_GRAPH_CLIENT_ID')),'calendarAuthorized':(home/'calendar-token.json').exists(),'notificationConfigured':bool(os.getenv('IOALAB_SMTP_HOST')),'smtpAuth':os.getenv('IOALAB_SMTP_AUTH','password'),'smtpAppConfigured':bool(os.getenv('IOALAB_SMTP_CLIENT_ID') or os.getenv('IOALAB_GRAPH_CLIENT_ID')),'smtpAuthorized':(home/'smtp-token.json').exists()},ensure_ascii=False));return
    from ioalab_sdk.swarm_web import SwarmWebBridge
    mailbox=MailboxClient(os.environ['IOALAB_BASE_URL'],os.getenv('IOALAB_USERNAME','ISC_001'),os.environ['IOALAB_PASSWORD'],home,connect_identity=True)
    try:
        bridge=SwarmWebBridge(os.environ['IOALAB_SWARM_WS'],mailbox.store) if os.getenv('IOALAB_SWARM_WS') else None
        calendar=graph(home) if os.getenv('IOALAB_GRAPH_CLIENT_ID') else None
        sender=notification(home) if os.getenv('IOALAB_SMTP_HOST') else None
        handlers=AssistantHandlers(mailbox,bridge=bridge,calendar=calendar,notification=sender,recipient=os.getenv('IOALAB_RECIPIENT_EMAIL','zhengw.cc@outlook.com'),rules=json.loads(os.getenv('IOALAB_APPOINTMENT_RULES','{}')))
        handlers.publish(json.loads(os.environ['IOALAB_ALLOWED_SENDERS']) if os.getenv('IOALAB_ALLOWED_SENDERS') else None,replace=bool(os.getenv('IOALAB_ALLOWED_SENDERS')))
        while True:
            mailbox.sync(wait=0 if args.command=='once' else 2);handlers_result=mailbox.process(handlers)
            for message in handlers_result: print(json.dumps({'messageId':message['id'],'state':message['state'],'result':message['result']},ensure_ascii=False),flush=True)
            if args.command=='once': break
    finally: mailbox.close()

if __name__=='__main__': main()
