"""Capture one observed native session through its public paged history API."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

from websockets.sync.client import connect


def capture(url,session,*,timeout=15,max_pages=20):
    frames=[];page=1;pages=1
    with connect(url,proxy=None,open_timeout=5,close_timeout=1,max_size=4*1024*1024) as websocket:
        while page<=pages:
            websocket.send(json.dumps({'type':'req','id':'audit-'+uuid.uuid4().hex,'method':'history.get',
                'session_id':session,'params':{'session_id':session,'page_idx':page}}))
            deadline=time.monotonic()+timeout
            while True:
                frame=json.loads(websocket.recv(timeout=max(.01,deadline-time.monotonic())))
                if frame.get('event')!='history.message':continue
                payload=frame['payload']
                if payload.get('session_id')!=session or payload.get('page_idx')!=page:continue
                pages=payload['total_pages']
                if type(pages) is not int or not 1<=pages<=max_pages:raise ValueError('History pagination is outside the explicit bound')
                frames.append(frame)
                if payload.get('status')=='done':break
            page+=1
    return frames


def summary(frames,session):
    messages=[f['payload']['message'] for f in frames if isinstance(f.get('payload',{}).get('message'),dict)]
    calls={}
    for message in messages:
        if message.get('event_type')=='chat.tool_call':
            call=message['tool_call'];calls[call['tool_call_id']]=call
    result={'sessionId':session,'pages':len({f['payload']['page_idx'] for f in frames}),
        'messageCount':len(messages),'tools':dict(Counter(c['name'] for c in calls.values())),
        'configurationBodyReadCalls':[],'packageInstallCalls':[],'sdkOperations':[],'skillsRead':[],
        'scope':'Observed public tool-call metadata only; absence of a matched pattern is not proof of filesystem access isolation.'}
    for call in calls.values():
        arguments=json.loads(call['arguments']);name=call['name'];command=arguments.get('command','')
        if name=='skill_tool':
            result['skillsRead'].append({key:arguments[key] for key in ('skill_name','relative_file_path') if key in arguments})
        config_read=(name=='read_file' and 'settings.local.json' in arguments.get('file_path',''))
        config_read=config_read or ('settings.local.json' in command and re.search(r'Get-Content|\bcat\b|read_text|ReadAllText',command))
        if config_read:result['configurationBodyReadCalls'].append(call['tool_call_id'])
        if re.search(r'\bpip(?:3)?\s+install\b',command):result['packageInstallCalls'].append(call['tool_call_id'])
        result['sdkOperations']+=re.findall(r'ioalab\.py"?\s+([a-z][a-z0-9_.]*)',command)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ws-url',required=True,help='The observed local WebChannel URL including its user_id')
    parser.add_argument('--session-id',required=True,help='The exact observed session; no discovery is performed')
    parser.add_argument('--out',required=True,type=Path,help='New local private raw-evidence file; never uploaded')
    args=parser.parse_args(argv)
    url=urlsplit(args.ws_url)
    if url.scheme not in ('ws','wss') or url.hostname not in ('127.0.0.1','localhost','::1') or url.username or url.password:
        parser.error('Use the explicitly observed local WebChannel endpoint without URL credentials')
    if not args.session_id or len(args.session_id)>256:parser.error('Use one bounded observed session ID')
    if args.out.exists():parser.error('Evidence already exists; choose a new local output file')
    try:
        frames=capture(args.ws_url,args.session_id)
        report=summary(frames,args.session_id)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open('x',encoding='utf-8') as stream:json.dump(frames,stream,ensure_ascii=False,indent=2)
    except Exception as exc:
        print(json.dumps({'status':'failed','errorType':type(exc).__name__}));return 1
    print(json.dumps({'status':'captured',**report},ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
