"""The developer audit uses the real public wire and keeps message bodies out of stdout."""
import json
from pathlib import Path
import subprocess
import sys
import threading

from websockets.sync.server import serve


SCRIPT=Path(__file__).resolve().parents[1]/'.agents/skills/jiuwen-swarm/scripts/read_web_history.py'


def test_public_paged_audit_preserves_shared_turn_ids_and_deduplicates_tool_calls(tmp_path):
    requests=[];session='observed-session';canary='private-message-body-canary'
    def handler(websocket):
        for page in (1,2):
            request=json.loads(websocket.recv());requests.append(request)
            assert request['method']=='history.get' and request['session_id']==session
            assert request['params']=={'session_id':session,'page_idx':page}
            calls=[{'name':'skill_tool','tool_call_id':'skill-call','arguments':'{"skill_name":"owned-skill"}'},
                   {'name':'read_file','tool_call_id':'read-call','arguments':'{"file_path":"settings.local.json"}'}]
            for call in calls:
                payload={'session_id':session,'page_idx':page,'total_pages':2,
                    'message':{'id':'shared-native-turn','event_type':'chat.tool_call','tool_call':call}}
                websocket.send(json.dumps({'event':'history.message','payload':payload}))
            websocket.send(json.dumps({'event':'history.message','payload':{'session_id':session,'page_idx':page,
                'total_pages':2,'message':{'id':'shared-native-turn','event_type':'chat.text','content':canary}}}))
            websocket.send(json.dumps({'event':'history.message','payload':{'session_id':session,'page_idx':page,
                'total_pages':2,'status':'done'}}))
    with serve(handler,'127.0.0.1',0) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        target=tmp_path/'private'/'raw.json'
        result=subprocess.run([sys.executable,str(SCRIPT),'--ws-url',f'ws://127.0.0.1:{server.socket.getsockname()[1]}/ws?user_id=owner',
            '--session-id',session,'--out',str(target)],capture_output=True,encoding='utf-8',timeout=20)
        server.shutdown();thread.join(5)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['status']=='captured' and report['pages']==2 and report['messageCount']==6
    assert report['tools']=={'skill_tool':1,'read_file':1}
    assert report['configurationBodyReadCalls']==['read-call']
    assert canary not in result.stdout and canary in target.read_text(encoding='utf-8')
    assert len(requests)==2


def test_existing_audit_evidence_is_never_overwritten_or_requeried(tmp_path):
    path=tmp_path/'raw.json';path.write_bytes(b'original-evidence')
    result=subprocess.run([sys.executable,str(SCRIPT),'--ws-url','ws://127.0.0.1:1/ws?user_id=owner',
        '--session-id','observed-session','--out',str(path)],capture_output=True,encoding='utf-8',timeout=10)
    assert result.returncode==2 and 'Evidence already exists' in result.stderr
    assert path.read_bytes()==b'original-evidence'
