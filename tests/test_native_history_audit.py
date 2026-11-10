"""The developer audit uses the real public wire and keeps message bodies out of stdout."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import threading

from websockets.sync.server import serve


SCRIPT=Path(__file__).resolve().parents[1]/'.agents/skills/jiuwen-swarm/scripts/read_web_history.py'


def audit_metadata(calls,paths=()):
    spec=importlib.util.spec_from_file_location('native_history_fixture',SCRIPT)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    frames=[{'payload':{'page_idx':1,'message':{'event_type':'chat.tool_call','tool_call':{
        'tool_call_id':key,'name':name,'arguments':json.dumps(arguments)}}}}
        for key,name,arguments in calls]
    return module.summary(frames,'pure-fixture',paths)


def test_custom_configuration_paths_detect_direct_reads_and_writes_without_opening_files():
    config=r'D:\never-exists\settings.provider.local.json'
    other='/no-real-file/assistant.account.json'
    report=audit_metadata([
        ('read','read_file',{'file_path':config}),
        ('write','write_file',{'file_path':config,'content':'PRIVATE-FIXTURE-CANARY'}),
        ('shell-read','bash',{'command':f'Get-Content -LiteralPath "{config}"'}),
        ('python-read','bash',{'command':f'Path(r"{config}").read_text()'}),
        ('shell-write','bash',{'command':f'Set-Content -LiteralPath "{other}" -Value fixture'}),
        ('other','read_file',{'file_path':'/scene/template-draft-input.json'}),
    ],[config,other])
    assert report['configurationBodyReadCalls']==['read','shell-read','python-read']
    assert report['configurationBodyWriteCalls']==['write','shell-write']
    assert report['configurationAccessReviewCalls']==[]
    assert 'PRIVATE-FIXTURE-CANARY' not in json.dumps(report)
    assert 'not proof' in report['scope']


def test_sdk_config_forwarding_with_business_result_reads_is_not_a_body_access():
    config=r'D:\never-exists\settings.provider.local.json'
    calls=[('shell-sdk','bash',{'command':f'python "skills/ioalab.py" provider.status --config "{config}"; cat result.json'}),
           ('python-sdk','bash',{'command':f"subprocess.run(['python','ioalab.py','provider.preview','--config',r'{config}']); open('result.json').read()"}),
           ('module-sdk','bash',{'command':f'python -m ioalab_sdk.access call provider.status --config={config}'}),
           ('body','write_file',{'file_path':'/work/arguments.json','content':config})]
    report=audit_metadata(calls,[config])
    assert report['configurationBodyReadCalls']==[]
    assert report['configurationBodyWriteCalls']==[]
    assert report['configurationAccessReviewCalls']==[]

    variable=audit_metadata([('variable-sdk','bash',{'command':
        f"cfg=r'{config}'; subprocess.run(['python','ioalab.py','provider.preview','--config',cfg]); open('result.json','wb').write(result)"})],[config])
    assert variable['configurationBodyReadCalls']==[] and variable['configurationBodyWriteCalls']==[]
    assert variable['configurationAccessReviewCalls']==['variable-sdk']


def test_sdk_forwarding_does_not_hide_a_separate_direct_config_access():
    config='/never-exists/settings.provider.local.json'
    report=audit_metadata([
        ('mixed','bash',{'command':f'python ioalab.py runtime.status --config "{config}"; cat "{config}"'}),
        ('write-open','bash',{'command':f'open("{config}", "w").write("fixture")'}),
        ('uncertain','bash',{'command':f'python arbitrary.py "{config}"'}),
    ],[config])
    assert report['configurationBodyReadCalls']==['mixed']
    assert report['configurationBodyWriteCalls']==['write-open']
    assert report['configurationAccessReviewCalls']==['uncertain']


def test_default_configuration_name_remains_supported():
    report=audit_metadata([('legacy','read_file',{'file_path':'settings.local.json'})])
    assert report['configurationBodyReadCalls']==['legacy']


def test_public_paged_audit_preserves_shared_turn_ids_and_deduplicates_tool_calls(tmp_path):
    requests=[];session='observed-session';canary='private-message-body-canary'
    config=r'D:\fixture-only\settings.provider.local.json'
    def handler(websocket):
        for page in (1,2):
            request=json.loads(websocket.recv());requests.append(request)
            assert request['method']=='history.get' and request['session_id']==session
            assert request['params']=={'session_id':session,'page_idx':page}
            calls=[{'name':'skill_tool','tool_call_id':'skill-call','arguments':'{"skill_name":"owned-skill"}'},
                   {'name':'read_file','tool_call_id':'read-call','arguments':json.dumps({'file_path':config})}]
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
            '--session-id',session,'--out',str(target),'--configuration-path',config],capture_output=True,encoding='utf-8',timeout=20)
        server.shutdown();thread.join(5)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['status']=='captured' and report['pages']==2 and report['messageCount']==6
    assert report['tools']=={'skill_tool':1,'read_file':1}
    assert report['configurationBodyReadCalls']==['read-call']
    assert report['configurationPaths']==[config]
    assert canary not in result.stdout and canary in target.read_text(encoding='utf-8')
    assert len(requests)==2


def test_existing_audit_evidence_is_never_overwritten_or_requeried(tmp_path):
    path=tmp_path/'raw.json';path.write_bytes(b'original-evidence')
    result=subprocess.run([sys.executable,str(SCRIPT),'--ws-url','ws://127.0.0.1:1/ws?user_id=owner',
        '--session-id','observed-session','--out',str(path)],capture_output=True,encoding='utf-8',timeout=10)
    assert result.returncode==2 and 'Evidence already exists' in result.stderr
    assert path.read_bytes()==b'original-evidence'
