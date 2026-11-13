"""Explicit subprocess double for the public Codex App Server wire protocol."""
import json
from pathlib import Path
import sys
import time


def send(value):
    print(json.dumps(value),flush=True)


if 'generate-json-schema' in sys.argv:
    root=Path(sys.argv[sys.argv.index('--out')+1]);(root/'v2').mkdir(parents=True)
    (root/'v2/ThreadStartParams.json').write_text(json.dumps({'properties':{'dynamicTools':{}}}))
    sys.exit(0)

thread='test-real-thread'
turn='test-turn'
tools=[]
mode='normal'
if '--mode' in sys.argv:mode=sys.argv[sys.argv.index('--mode')+1]
for line in sys.stdin:
    request=json.loads(line)
    method=request.get('method');params=request.get('params',{})
    if method=='initialize':
        send({'id':request['id'],'result':{'userAgent':'codex-test/1','platformOs':'test'}})
    elif method=='model/list':
        send({'id':request['id'],'result':{'data':[{'id':'test-model','model':'test-model'}],'nextCursor':None}})
    elif method=='config/read':
        send({'id':request['id'],'result':{'config':{'mcp_servers':{'external.with.dot':{'command':'must-not-launch','tool_timeout_sec':None}}}}})
    elif method=='skills/list':
        send({'id':request['id'],'result':{'data':[]}})
    elif method in ('thread/start','thread/resume'):
        tools=params.get('dynamicTools') or json.loads((Path(params['cwd'])/'fake-tools.json').read_text())
        (Path(params['cwd'])/'fake-tools.json').write_text(json.dumps(tools))
        assert params['sandbox']=='read-only'
        assert params['approvalPolicy']=='never'
        assert params['config']['mcp_servers']['external.with.dot']['enabled'] is False
        assert 'tool_timeout_sec' not in params['config']['mcp_servers']['external.with.dot']
        if method=='thread/resume':assert params['threadId']==thread
        send({'id':request['id'],'result':{'thread':{'id':thread},'model':'test-model','modelProvider':'test','sandbox':{'type':'readOnly'}}})
        if mode=='stall-input':time.sleep(3);sys.exit(0)
    elif method=='turn/start':
        send({'id':request['id'],'result':{'turn':{'id':turn,'status':'inProgress'}}})
        send({'method':'turn/started','params':{'threadId':thread,'turn':{'id':turn,'status':'inProgress'}}})
        if mode=='wait':continue
        if mode=='exit':sys.exit(9)
        if mode=='bad-tool':
            send({'id':'tool-request','method':'item/tool/call','params':{'threadId':thread,'turnId':turn,'callId':'original-call','namespace':'other','tool':tools[0]['tools'][0]['name'],'arguments':{'text':'hello'}}})
            continue
        if len(tools[0]['tools'])>1:
            send({'id':'reader-request','method':'item/tool/call','params':{'threadId':thread,'turnId':turn,'callId':'read-call','namespace':'ioa_sdk','tool':tools[0]['tools'][-1]['name'],'arguments':{'skill':'selected'}}})
            continue
        send({'id':'tool-request','method':'item/tool/call','params':{'threadId':thread,'turnId':turn,'callId':'original-call','namespace':'ioa_sdk','tool':tools[0]['tools'][0]['name'],'arguments':{'text':'hello'}}})
    elif method=='turn/interrupt':
        send({'id':request['id'],'result':{}})
        send({'method':'turn/completed','params':{'threadId':thread,'turn':{'id':turn,'status':'interrupted'}}})
    elif method is None and request.get('id')=='reader-request':
        assert json.loads(request['result']['contentItems'][0]['text'])['content']=='# Original skill'
        send({'id':'tool-request','method':'item/tool/call','params':{'threadId':thread,'turnId':turn,'callId':'original-call','namespace':'ioa_sdk','tool':tools[0]['tools'][0]['name'],'arguments':{'text':'hello'}}})
    elif method is None and request.get('id')=='tool-request':
        assert json.loads(request['result']['contentItems'][0]['text'])=={'receipt':'hello'}
        send({'method':'thread/tokenUsage/updated','params':{'threadId':thread,'tokenUsage':{'total':{'totalTokens':17}}}})
        send({'method':'turn/completed','params':{'threadId':thread,'turn':{'id':turn,'status':'completed'}}})
