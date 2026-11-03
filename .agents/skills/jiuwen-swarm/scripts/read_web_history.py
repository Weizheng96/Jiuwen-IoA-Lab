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


def normalized_path(value):
    # Compare metadata only: do not resolve or open the account configuration.
    return re.sub(r'/+', '/', str(value).replace('\\', '/')).casefold()


def configuration_access(call,arguments,paths):
    targets=[normalized_path(path) for path in paths]
    def mentions(value):
        value=normalized_path(value)
        return any(target in value for target in targets)
    name=call['name']
    path=arguments.get('file_path',arguments.get('path',''))
    if name in ('read_file','write_file'):
        return (name=='read_file',name=='write_file',False) if mentions(path) else (False,False,False)
    command=arguments.get('command','')
    if not isinstance(command,str):return False,False,False
    # Passing a path to the product SDK is not a model reading its body. Remove
    # only literal --config arguments on an observed SDK invocation; leave any
    # separate direct access to that same path visible to the matcher.
    sdk=bool(re.search(r'ioalab\.py|ioalab_sdk\.runtime|ioalab_jiuwen',command))
    if sdk:
        command=re.sub(r'''(["'])--config\1\s*,\s*r?(["'])(.*?)\2''','--config SDK_PATH',command)
        command=re.sub(r'''--config(?:=|\s+)r?(["'])(.*?)\1''','--config SDK_PATH',command)
        command=re.sub(r'--config(?:=|\s+)[^\s,;|<>]+','--config SDK_PATH',command)
    if not mentions(command):return False,False,False
    # Variable forwarding needs manual source review. Do not label an unrelated
    # open(result_path, 'wb') as an account write merely because cfg was assigned
    # elsewhere in the same command. Keep direct uses of that variable visible.
    assigned=[]
    def assignment(match):
        if mentions(match.group(3)):
            assigned.append(match.group(1))
            return match.group(1)+'=CONFIGURATION_PATH'
        return match.group(0)
    reduced=re.sub(r'''(\$?[A-Za-z_]\w*)\s*=\s*r?(["'])(.*?)\2''',assignment,command)
    if sdk and assigned and not mentions(reduced):
        direct=any(re.search(r'(?:open\s*\(|Path\s*\(|Get-Content\s+|Set-Content\s+|\bcat\s+)'+re.escape(variable)+r'\b',reduced,re.I)
                   for variable in assigned)
        if not direct:return False,False,True
    read_command=re.sub(r'''\bopen\s*\([^\n]*?,\s*["'][wax][^"']*["']''','<write-open>',command)
    reads=bool(re.search(r'Get-Content|\b(?:cat|type|more|head|tail)\b|read_text|read_bytes|ReadAllText|ReadAllBytes|\bopen\s*\(',read_command,re.I))
    writes=bool(re.search(r'Set-Content|Add-Content|Out-File|write_text|write_bytes|WriteAllText|WriteAllBytes|\b(?:tee|cp|mv)\b|(?:\>|\>\>)\s*["\']?[^\n]*|\bopen\s*\([^\n]*,[\s]*["\'][wax]',command,re.I))
    # Static patterns cannot prove what arbitrary scripts/variable paths do.
    return reads,writes,not (reads or writes)


def summary(frames,session,configuration_paths=()):
    messages=[f['payload']['message'] for f in frames if isinstance(f.get('payload',{}).get('message'),dict)]
    calls={}
    for message in messages:
        if message.get('event_type')=='chat.tool_call':
            call=message['tool_call'];calls[call['tool_call_id']]=call
    result={'sessionId':session,'pages':len({f['payload']['page_idx'] for f in frames}),
        'messageCount':len(messages),'tools':dict(Counter(c['name'] for c in calls.values())),
        'configurationPaths':list(configuration_paths) or ['settings.local.json'],
        'configurationBodyReadCalls':[],'configurationBodyWriteCalls':[],'configurationAccessReviewCalls':[],
        'packageInstallCalls':[],'sdkOperations':[],'skillsRead':[],
        'scope':'Observed public tool-call metadata only; absence of a matched pattern is not proof of filesystem access isolation.'}
    for call in calls.values():
        arguments=json.loads(call['arguments']);name=call['name'];command=arguments.get('command','')
        if name=='skill_tool':
            result['skillsRead'].append({key:arguments[key] for key in ('skill_name','relative_file_path') if key in arguments})
        config_read,config_write,review=configuration_access(call,arguments,result['configurationPaths'])
        if config_read:result['configurationBodyReadCalls'].append(call['tool_call_id'])
        if config_write:result['configurationBodyWriteCalls'].append(call['tool_call_id'])
        if review:result['configurationAccessReviewCalls'].append(call['tool_call_id'])
        if re.search(r'\bpip(?:3)?\s+install\b',command):result['packageInstallCalls'].append(call['tool_call_id'])
        result['sdkOperations']+=re.findall(r'ioalab\.py"?\s+([a-z][a-z0-9_.]*)',command)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ws-url',required=True,help='The observed local WebChannel URL including its user_id')
    parser.add_argument('--session-id',required=True,help='The exact observed session; no discovery is performed')
    parser.add_argument('--out',required=True,type=Path,help='New local private raw-evidence file; never uploaded')
    parser.add_argument('--configuration-path',action='append',default=[],
        help='Actual account configuration path to inspect in metadata; repeat for multiple paths; never opened')
    args=parser.parse_args(argv)
    url=urlsplit(args.ws_url)
    if url.scheme not in ('ws','wss') or url.hostname not in ('127.0.0.1','localhost','::1') or url.username or url.password:
        parser.error('Use the explicitly observed local WebChannel endpoint without URL credentials')
    if not args.session_id or len(args.session_id)>256:parser.error('Use one bounded observed session ID')
    if args.out.exists():parser.error('Evidence already exists; choose a new local output file')
    try:
        frames=capture(args.ws_url,args.session_id)
        report=summary(frames,args.session_id,args.configuration_path)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open('x',encoding='utf-8') as stream:json.dump(frames,stream,ensure_ascii=False,indent=2)
    except Exception as exc:
        print(json.dumps({'status':'failed','errorType':type(exc).__name__}));return 1
    print(json.dumps({'status':'captured',**report},ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
