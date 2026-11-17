"""196 A public adapter boundaries. All native I/O is outside these fixtures."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time

import pytest

from ioalab_sdk.runtime import (HostInput,HostResource,SubmissionAttempt,BindingAck,RuntimeFault,
    HostExecution,HostDefinition,HostToolDeclaration,HostToolResult,SubmissionError,NATIVE_ENTRY_BIND_OPERATION)
from ioalab_claude_code import native_adapter,native_skills
from ioalab_claude_code.native_host import NativeClaudeHost
from ioalab_claude_code.native_transport import NativeTransport
from ioalab_claude_code.native_files import NativeFiles
from ioalab_claude_code.native_process import NativeProcesses,NativeLaunchStore,launch_digest,native_cli_from_ancestors


def payload(text='hello',*,profile='assistant'):
    return HostInput(text,attempt=SubmissionAttempt('attempt','scope',profile+'-actor',profile))


def scope(profile='assistant'):
    return {'hostRef':'claude','adapterId':'claude_code','profile':profile,'actor':profile+'-actor'}


def ready(tmp_path):
    transport=NativeTransport(host_ref='claude',receipt_root=tmp_path/'receipts');transport.start()
    for profile in ('assistant','provider'):
        transport.exchange(scope(profile),{'kind':'hello','sessionId':'actual-session','generation':'generation','workspace':str(tmp_path)})
    return transport


def test_static_complete_bundle_and_no_optional_method_claims():
    registration=NativeClaudeHost.registration
    assert registration.adapter_id=='claude_code' and registration.optional_methods==()
    root=Path(__file__).parents[1]/'src/client/hosts/claude_code/ioalab_claude_code/resources/native'
    assert len(registration.resources)==6
    assert all((root/item.name).is_file() for item in registration.resources)


def test_complete_static_resource_export_without_optional_install_or_source_lookup(tmp_path):
    import ioalab_claude_code
    role={'profile':'original-profile','descriptorFile':str(tmp_path/'not-read-private.json'),
          'ensureCommand':[str(tmp_path/'bound-python.exe'),'-m','ioalab_client.runtime'],
          'callerCommand':[str(tmp_path/'bound-python.exe'),'-m','ioalab_client','--native-input']}
    binding={'format':'ioalab.native-entry/1','hostRef':'original-host','profiles':{'assistant':role},
             'sourceOperations':['assistant.delegation.prepare'],'setupOperation':NATIVE_ENTRY_BIND_OPERATION}
    resources=native_skills.resource_files(binding)
    assert set(resources)=={value.name for value in NativeClaudeHost.registration.resources}|{'entry-binding.json'}
    assert json.loads(resources['entry-binding.json'])==binding and not (tmp_path/'not-read-private.json').exists()
    assert not hasattr(ioalab_claude_code,'collect_context')
    destination=tmp_path/'owned-copy'
    assert native_skills.install(destination,binding)['nativeLoaded'] is False
    assert native_skills.install(destination,binding)['manualRequired']
    (destination/'wrapper.mjs').write_text('user edit')
    with pytest.raises(RuntimeFault):native_skills.install(destination,binding)
    assert (destination/'wrapper.mjs').read_text()=='user edit'
    with pytest.raises(RuntimeFault):native_skills.resource_files({**binding,'grant':'must not export'})


def test_explicit_product_factory_does_not_create_resources_or_switch_sdk_app(tmp_path):
    from ioalab_claude_code import ClaudeCodeHost
    root=tmp_path/'not-created'
    bundle=native_adapter(host_ref='claude',resource_root=root)
    assert isinstance(bundle['host'],NativeClaudeHost) and not root.exists()
    assert bundle['native_handlers']['claude'].__self__ is bundle['native_resources']['claude']
    assert ClaudeCodeHost is not NativeClaudeHost


def test_missing_binding_is_specific_not_submitted_and_never_calls_events():
    seen=[]
    result=NativeClaudeHost().submit('op',payload(),on_event=seen.append)
    assert result.state=='unknown' and result.request_id is None
    assert result.error.dispatch=='not_submitted' and result.error.action=='bind_native_transport'
    assert not seen and result.terminal is None


@pytest.mark.parametrize('authorization',({}, {'nativeToolsEnabled':False}, {'nativeToolsEnabled':'true'}, {'nativeToolsEnabled':1}))
def test_business_native_phase_permission_never_defaults_to_writable(tmp_path,authorization):
    transport=ready(tmp_path);events=[]
    def frozen():return HostExecution('assistant-actor','assistant','business','interaction','job',
        HostDefinition('definition','1','digest','frozen instructions'),authorization=authorization)
    if 'nativeToolsEnabled' in authorization and type(authorization['nativeToolsEnabled']) is not bool:
        with pytest.raises(RuntimeFault):frozen()
        assert not events and not transport.requests
        return
    execution=frozen()
    result=NativeClaudeHost(transport=transport).submit('op',payload(),'actual-session',execution=execution,on_event=events.append)
    assert result.error.phase=='native_permissions' and result.error.dispatch=='not_submitted'
    with pytest.raises(RuntimeFault):
        transport.submit('op',payload(),'actual-session',execution=execution,on_event=events.append)
    assert not events and not transport.requests


def test_existing_session_queue_persists_ack_then_marks_native_acceptance_only(tmp_path):
    transport=ready(tmp_path);events=[]
    def event(value):
        events.append(value)
        return BindingAck(value.result.session_id,value.result.request_id) if value.kind=='session' else None
    host=NativeClaudeHost(transport=transport)
    result=host.submit('op',payload(),'actual-session',on_event=event)
    assert result.state=='waiting' and result.delivery=='unknown'
    item=transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})['item']
    assert item['operationId']=='op' and item['attemptId']=='attempt' and item['text']=='hello'
    transport.exchange(scope(),{'kind':'accepted','sessionId':'actual-session','generation':'generation','operationId':'op','attemptId':'attempt'})
    assert events[-1].result.state=='accepted' and events[-1].result.terminal is None
    assert transport.exchange(scope('provider'),{'kind':'poll','sessionId':'actual-session','generation':'generation'})=={'item':None}
    assert not transport.drain(0)
    with pytest.raises(RuntimeFault):transport.close()


def test_wrong_original_attempt_or_generation_is_rejected(tmp_path):
    transport=ready(tmp_path)
    host=NativeClaudeHost(transport=transport)
    host.submit('op',payload(),'actual-session',on_event=lambda e:BindingAck(e.result.session_id))
    transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})
    with pytest.raises(RuntimeFault):
        transport.exchange(scope(),{'kind':'accepted','sessionId':'actual-session','generation':'generation','operationId':'op','attemptId':'other'})
    with pytest.raises(RuntimeFault):
        transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'other'})


def test_same_session_roles_cannot_poll_or_ack_each_others_request(tmp_path):
    transport=ready(tmp_path);host=NativeClaudeHost(transport=transport)
    ack=lambda e:BindingAck(e.result.session_id)
    host.submit('assistant-op',payload(),'actual-session',on_event=ack)
    assert transport.exchange(scope('provider'),{'kind':'poll','sessionId':'actual-session','generation':'generation'})=={'item':None}
    item=transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})['item']
    assert item['operationId']=='assistant-op'
    with pytest.raises(RuntimeFault):
        transport.exchange(scope('provider'),{'kind':'accepted','sessionId':'actual-session','generation':'generation',
                                            'operationId':'assistant-op','attemptId':'attempt'})
    transport.exchange(scope(),{'kind':'accepted','sessionId':'actual-session','generation':'generation',
                                'operationId':'assistant-op','attemptId':'attempt'})
    assert set(transport.sessions['actual-session']['profiles'])=={'assistant','provider'}


def test_unknown_session_is_not_replaced_and_new_factory_missing_is_explicit(tmp_path):
    transport=ready(tmp_path);host=NativeClaudeHost(transport=transport)
    missing=host.submit('op',payload(),'missing',on_event=lambda e:BindingAck('missing'))
    assert missing.session_id=='missing' and missing.error.dispatch=='not_submitted'
    new=host.submit('new',payload(),on_event=lambda e:BindingAck('fake'))
    assert new.session_id is None and new.error.action=='bind_native_session_factory'
    assert not transport.requests


def test_new_window_requires_registered_project_and_keeps_runtime_materials_separate(tmp_path,monkeypatch):
    calls=[];project=tmp_path/'original-project';project.mkdir()
    plugin=tmp_path/'extension';(plugin/'.claude-plugin').mkdir(parents=True)
    (plugin/'.claude-plugin/plugin.json').write_text('{}')
    (plugin/'entry-binding.json').write_text('{}')
    cli=tmp_path/'claude.exe';cli.write_bytes(b'fixture, not executed')
    class Child:
        def poll(self):return None
    monkeypatch.setenv('CLAUDECODE','1');monkeypatch.setenv('IOALAB_NATIVE_ENV_FIXTURE','inherited')
    def launch(argv,**options):
        # Do not retain or print the developer machine's inherited environment.
        safe={key:value for key,value in options.items() if key!='env'}
        safe['nestedMarker']=options['env'].get('CLAUDECODE')
        safe['inheritanceFixture']=options['env'].get('IOALAB_NATIVE_ENV_FIXTURE')
        calls.append((argv,safe));return Child()
    runtime=tmp_path/'runtime-not-created'
    with pytest.raises(RuntimeFault) as rejected:
        NativeProcesses(home=runtime,plugin_directory=plugin,cli=str(cli),launch=launch).create()
    assert rejected.value.recovery=='bind_native_launch_context' and not calls
    owned=NativeProcesses(home=runtime,plugin_directory=plugin,cli=str(cli),launch=launch,project_directory=project)
    locator=owned.create()
    assert calls[0][1]['cwd']==str(project) and not runtime.exists() and owned.owned(locator)
    assert calls[0][1]['nestedMarker'] is None and calls[0][1]['inheritanceFixture']=='inherited'
    assert '--resume' not in calls[0][0] and '--model' not in calls[0][0]
    first=owned.create(operation_id='original-op',actor='actor',profile='profile')
    assert owned.create(operation_id='original-op',actor='actor',profile='profile')==first
    assert len(calls)==2  # The unscoped fixture plus one original logical operation.
    assert owned.create(operation_id='original-op',actor='other-actor',profile='profile')!=first


def test_ack_callback_failure_never_becomes_not_submitted(tmp_path):
    transport=ready(tmp_path)
    def fail(_):raise RuntimeFault('unknown_result','Original event consumer failed')
    result=NativeClaudeHost(transport=transport).submit('op',payload(),'actual-session',on_event=fail)
    assert result.error.dispatch=='unknown' and result.terminal is None
    assert transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})=={'item':None}


def launch_capture(tmp_path):
    project=tmp_path/'registered-project';project.mkdir(exist_ok=True)
    cli=tmp_path/'registered-claude.exe';cli.write_bytes(b'never executed')
    return {'format':'ioalab.claude-launch/1','projectDirectory':str(project),'cli':str(cli),
        'configDirectoryState':'unset','configDirectory':None,'extensionDirectory':str(tmp_path/'extension')}


def launch_scope(boot='original-boot'):
    return {**scope(),'installationRef':'original-installation','accountBindingRef':'original-account','bootId':boot}


def test_setup_actual_save_requires_sdk_claim_and_keeps_original_receipt_after_ack_loss(tmp_path):
    capture=launch_capture(tmp_path);store=NativeLaunchStore(host_ref='claude',home=tmp_path/'owned-runtime')
    events=[];claims=[]
    def permission(actual,packet):
        assert actual==launch_scope()
        events.append(packet['action'])
        if packet['action']=='pending':return {'operationId':'setup-op','intentRef':'sdk-intent'}
        if packet['action']=='claim':
            assert not store.path.exists()
            assert packet['targetDigest']==launch_digest(capture)
            claim={'operationId':'setup-op','claimRef':'sdk-private-claim','targetDigest':packet['targetDigest']}
            claims.append(claim);return claim
        assert store.path.exists() and store.read()['claim']==claims[0]
        raise RuntimeFault('unknown_result','Fixture lost the original setup ACK')
    store.bind_entry_control(permission)
    with pytest.raises(RuntimeFault):store.capture(launch_scope(),entry_ref='sdk-source',capture=capture,workspace=capture['projectDirectory'])
    original=store.path.read_bytes()
    assert events==['pending','claim','complete']
    restarted=NativeLaunchStore(host_ref='claude',home=store.home)
    recovery=[]
    def acknowledge(actual,packet):
        assert actual==launch_scope('new-boot') and packet['action']=='complete' and 'entryRef' not in packet
        assert packet['claimRef']==claims[0]['claimRef']
        recovery.append(packet);return {'operationId':'setup-op','state':'completed','phase':'bound'}
    restarted.bind_entry_control(acknowledge)
    assert restarted.recover(launch_scope('new-boot'))['state']=='completed'
    different={**capture,'projectDirectory':str(tmp_path)}
    assert restarted.capture(launch_scope('new-boot'),entry_ref='different-source',capture=different,workspace=str(tmp_path))['state']=='completed'
    assert store.path.read_bytes()==original and len(recovery)==2
    with pytest.raises(RuntimeFault):restarted.recover({**launch_scope('new-boot'),'accountBindingRef':'other-account'})


def test_setup_no_intent_or_wrong_machine_workspace_never_saves_launch_context(tmp_path):
    capture=launch_capture(tmp_path);store=NativeLaunchStore(host_ref='claude',home=tmp_path/'runtime')
    actions=[]
    def permission(_scope,packet):actions.append(packet['action']);return None
    store.bind_entry_control(permission)
    with pytest.raises(RuntimeFault):store.capture(launch_scope(),entry_ref='ordinary-source',capture=capture,workspace=str(tmp_path))
    assert not actions and not store.path.exists()
    with pytest.raises(RuntimeFault):store.capture(launch_scope(),entry_ref='ordinary-source',capture=capture,workspace=capture['projectDirectory'])
    assert actions==['pending'] and not store.path.exists()


def test_private_setup_rejects_model_claim_and_normal_hello_cannot_select_launch(tmp_path):
    capture=launch_capture(tmp_path);store=NativeLaunchStore(host_ref='claude',home=tmp_path/'runtime')
    actions=[]
    def permission(_scope,packet):
        actions.append(packet['action'])
        if packet['action']=='pending':return {'operationId':'setup-op','intentRef':'intent'}
        if packet['action']=='claim':return {'operationId':'setup-op','claimRef':'claim','targetDigest':packet['targetDigest']}
        return {'operationId':'setup-op','state':'completed','phase':'bound'}
    transport=NativeTransport(host_ref='claude',launch_store=store);transport.bind_entry_control(permission);transport.start()
    native={'sessionId':'actual-session','generation':'generation'}
    transport.exchange(launch_scope(),{**native,'kind':'hello','workspace':capture['projectDirectory']})
    assert not actions and not store.path.exists()
    with pytest.raises(RuntimeFault):transport.exchange(launch_scope(),{**native,'kind':'entry_setup','entryRef':'source','capture':capture,'claimRef':'model-claim'})
    assert not actions and not store.path.exists()
    value=transport.exchange(launch_scope(),{**native,'kind':'entry_setup','entryRef':'source','capture':capture})
    assert value['state']=='completed' and actions==['pending','claim','complete']


def test_saved_launch_context_opens_new_business_after_original_chat_closed(tmp_path,monkeypatch):
    capture=launch_capture(tmp_path);store=NativeLaunchStore(host_ref='claude',home=tmp_path/'runtime')
    def permission(_scope,packet):
        if packet['action']=='pending':return {'operationId':'setup-op','intentRef':'intent'}
        if packet['action']=='claim':return {'operationId':'setup-op','claimRef':'claim','targetDigest':packet['targetDigest']}
        return {'operationId':'setup-op','state':'completed','phase':'bound'}
    store.bind_entry_control(permission)
    store.capture(launch_scope(),entry_ref='source',capture=capture,workspace=capture['projectDirectory'])
    plugin=tmp_path/'extension';(plugin/'.claude-plugin').mkdir(parents=True)
    (plugin/'.claude-plugin/plugin.json').write_text('{}');(plugin/'entry-binding.json').write_text('{}')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(tmp_path/'different-resident-config'))
    seen=[]
    class Child:
        def poll(self):return None
    def launch(argv,**options):
        seen.append((argv,options['cwd'],'CLAUDE_CONFIG_DIR' in options['env']));return Child()
    processes=NativeProcesses(home=store.home,plugin_directory=plugin,launch_store=NativeLaunchStore(host_ref='claude',home=store.home),launch=launch)
    sid=processes.create(operation_id='new-business',actor='assistant-actor',profile='assistant')
    assert processes.owned(sid) and seen[0][0][0]==capture['cli'] and seen[0][1]==capture['projectDirectory']
    assert seen[0][2] is False and '--model' not in seen[0][0] and '--resume' not in seen[0][0]


def test_new_session_waits_for_native_api_id_before_p28_or_dispatch(tmp_path):
    transport=NativeTransport(host_ref='claude');transport.start();seen=[]
    class Processes:
        def create(self):return 'intended-locator'
    def event(value):
        seen.append(value)
        return BindingAck(value.result.session_id)
    host=NativeClaudeHost(transport=transport,processes=Processes())
    result=host.submit('new',payload(),on_event=event)
    assert result.state=='waiting' and result.session_id is None and not seen
    transport.exchange(scope(),{'kind':'hello','sessionId':'intended-locator','generation':'actual-generation','workspace':str(tmp_path)})
    assert seen[0].result.session_id=='intended-locator'
    item=transport.exchange(scope(),{'kind':'poll','sessionId':'intended-locator','generation':'actual-generation'})['item']
    assert item['operationId']=='new'


def test_local_files_require_authorized_reader_and_digest(tmp_path):
    path=tmp_path/'input.txt';path.write_bytes(b'fixture')
    resource=HostResource('r','input.txt','text/plain',hashlib.sha256(b'fixture').hexdigest(),'original-business','opaque:r')
    with pytest.raises(RuntimeFault):NativeFiles().materialize((resource,))
    mapped=NativeFiles(reader=lambda _:path).materialize((resource,))
    assert mapped[0]['resourceId']=='r' and mapped[0]['path']==str(path)
    path.write_bytes(b'changed')
    with pytest.raises(RuntimeFault):NativeFiles(reader=lambda _:path).materialize((resource,))


def test_native_wrapper_uses_machine_source_and_preserves_unknown_public_call(tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node is a development fixture only, not a native runtime dependency')
    module=Path(__file__).parents[1]/'src/client/hosts/claude_code/ioalab_claude_code/resources/native/wrapper.mjs'
    script=tmp_path/'wrapper.mjs'
    script.write_text('import {invoke} from '+json.dumps(module.as_uri())+""";
import assert from 'node:assert/strict';
const binding={hostRef:'claude',profile:'personal-key',descriptorFile:'private-grant',ensureCommand:['ensure'],callerCommand:['caller'],sourceOperations:['assistant.delegation.prepare']};
let mode='ok';let calls=[];
const api={session:{id:async()=> 'machine-sid',cwd:async()=> 'D:/actual'},
fs:{read:async()=>JSON.stringify(
 {hostRef:'claude',adapterId:'claude_code',profile:'personal-key',grant:'fixture-grant',bootId:'boot',url:'http://127.0.0.1:7/v1/native-source',controlUrl:'http://127.0.0.1:7/v1/native-control'})},
process:{run:async(argv,options)=>{calls.push({argv,body:options?.stdin?JSON.parse(options.stdin):null});
 if(argv[0]==='ensure')return{exitCode:0,stdout:JSON.stringify({status:mode==='ensure-waiting'?'waiting':'completed'})};
 if(mode==='public-error')throw Error('fixture caller disconnect');
 return{exitCode:0,stdout:JSON.stringify({status:'completed',operationId:JSON.parse(options.stdin).operationId,data:{ok:true}})};}},
http:{fetch:async(url,args)=>{calls.push({url,body:JSON.parse(args.body)});
 if(mode==='source-error')throw Error('fixture disconnect');
 assert.equal(url.endsWith('/v1/native-source'),true);
 return{ok:true,text:JSON.stringify({status:'completed',data:{entryRef:'issued'}})};}}};
const request={operation:'assistant.delegation.prepare',operationId:'original-op',arguments:{}};
await invoke(api,binding,request);
assert.deepEqual(calls[0].argv,['ensure']);
assert.deepEqual(calls[1].body.sessionId,'machine-sid');assert.equal(calls[1].body.workspace,'D:/actual');
assert.deepEqual(calls[2].argv,['caller']);assert.deepEqual(calls[2].body.contextRef,{entryRef:'issued'});
assert.equal('sessionId' in calls[2].body,false);
calls=[];mode='public-error';await assert.rejects(invoke(api,binding,request),error=>error.submissionUnknown===true);
assert.equal(calls.length,3);assert.equal(calls[2].body.operationId,'original-op');
calls=[];mode='source-error';await assert.rejects(invoke(api,binding,request),error=>!error.submissionUnknown);
assert.equal(calls.length,2);
calls=[];mode='ensure-waiting';const waiting=await invoke(api,binding,request);
assert.equal(waiting.status,'waiting');assert.equal(waiting.operationId,'original-op');assert.equal(calls.length,1);
// No descriptor/API source access or ensure may occur on controls, including a
// stopped client. The shared caller remains the operation/identity authority.
api.fs.read=async()=>{throw Error('Control must not read a grant');};
api.session.id=async()=>{throw Error('Control must not capture a session');};
mode='ok';
for(const operation of ['runtime.stop','runtime.configuration','runtime.configure','runtime.status']) {
 calls=[];const value=await invoke(api,binding,{...request,operation},async()=>{throw Error('Control must not hello');});
 assert.equal(value.status,'completed');assert.equal(calls.length,1);
 assert.deepEqual(calls[0].argv,['caller']);assert.equal(calls[0].body.contextRef,null);
}
""",encoding='utf-8')
    completed=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr


def test_native_setup_wrapper_uses_explicit_sdk_intent_then_fixed_capture_and_original_query(tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node is a development fixture only')
    module=Path(__file__).parents[1]/'src/client/hosts/claude_code/ioalab_claude_code/resources/native/wrapper.mjs'
    script=tmp_path/'setup.mjs'
    script.write_text('import {invoke} from '+json.dumps(module.as_uri())+""";
import assert from 'node:assert/strict';
const calls=[];let saved=false;let reserved=false;let failSave=false;
const binding={hostRef:'claude',profile:'personal-key',descriptorFile:'private-grant',ensureCommand:['ensure'],
 callerCommand:['bound-python','-m','caller'],sourceOperations:[],setupOperation:'runtime.entry.bind',observerGeneration:'native-generation'};
const grant={hostRef:'claude',adapterId:'claude_code',profile:'personal-key',grant:'private-fixture',bootId:'boot',
 url:'http://127.0.0.1:7/v1/native-source',controlUrl:'http://127.0.0.1:7/v1/native-control'};
const api={plugin:{root:'D:/actual-loaded-extension'},session:{id:async()=> 'actual-sid',cwd:async()=> 'D:/original'},fs:{read:async()=>JSON.stringify(grant)},
process:{run:async(argv,options)=>{
 calls.push({argv,stdin:options?.stdin?JSON.parse(options.stdin):null});
 if(argv[0]==='ensure')return{exitCode:0,stdout:'{"status":"completed"}'};
 if(argv.includes('-c')){
  assert.equal(argv[0],'bound-python');assert.equal(saved,false);assert.equal(reserved,false);
  return{exitCode:0,stdout:JSON.stringify({format:'ioalab.claude-launch/1',projectDirectory:'D:/original',cli:'D:/native.exe',configDirectoryState:'unset',configDirectory:null})};
 }
 const input=JSON.parse(options.stdin);assert.equal(input.operationId,'original-setup');assert.equal(input.operation,'runtime.entry.bind');
 assert.deepEqual(input.contextRef,{entryRef:'sdk-source'});
 return{exitCode:0,stdout:JSON.stringify({operationId:input.operationId,status:saved?'completed':'waiting',data:{phase:saved?'bound':reserved?'reserved':'intent'}})};
}},http:{fetch:async(url,args)=>{
 const body=JSON.parse(args.body);calls.push({url,body});let data;
 if(url.endsWith('/v1/native-source'))data={entryRef:'sdk-source'};
 else if(body.kind==='entry_recover')data=saved?{state:'completed'}:null;
 else{assert.equal(body.kind,'entry_setup');assert.equal(body.sessionId,'actual-sid');assert.equal(body.generation,'native-generation');
   assert.equal(body.capture.projectDirectory,'D:/original');assert.equal(body.capture.extensionDirectory,'D:/actual-loaded-extension');assert.equal('claimRef' in body,false);
   if(failSave)throw Error('Fixture lost save ACK');saved=true;data={state:'completed'};}
 return{ok:true,text:JSON.stringify({status:'completed',data})};
}}};
const request={operation:'runtime.entry.bind',operationId:'original-setup',arguments:{}};
const result=await invoke(api,binding,request);
assert.equal(result.status,'completed');assert.equal(calls.filter(x=>x.argv?.includes('-c')).length,1);
assert.equal(calls.filter(x=>x.stdin?.operation==='runtime.entry.bind').length,2);
calls.length=0;await invoke(api,binding,request);assert.equal(calls.some(x=>x.argv?.includes('-c')),false);
saved=false;reserved=true;calls.length=0;
assert.equal((await invoke(api,binding,request)).data.phase,'reserved');
assert.equal(calls.some(x=>x.argv?.includes('-c') || x.body?.kind==='entry_setup'),false);
reserved=false;failSave=true;calls.length=0;
await assert.rejects(invoke(api,binding,request),error=>error.submissionUnknown===true);
assert.equal(calls.filter(x=>x.body?.kind==='entry_setup').length,1);
assert.equal(calls.filter(x=>x.stdin?.operation==='runtime.entry.bind').length,1);
calls.length=0;await assert.rejects(invoke(api,binding,{...request,arguments:{sessionId:'model-selected'}}));assert.equal(calls.length,0);
""",encoding='utf-8')
    completed=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr


def test_native_entry_polls_each_role_and_refreshes_boot_without_replay(tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node is a development fixture only')
    module=Path(__file__).parents[1]/'src/client/hosts/claude_code/ioalab_claude_code/resources/native/hooks/entry.mjs'
    script=tmp_path/'entry.mjs'
    script.write_text('import {register} from '+json.dumps(module.as_uri())+""";
import assert from 'node:assert/strict';
const handlers=new Map();let tick;let boot='one';let prompts=0;let accepted=0;const hellos=[];const polls=[];
register((event,...values)=>handlers.set(event,values.at(-1)));
const role=(profile,descriptorFile)=>({profile,descriptorFile,ensureCommand:['ensure'],callerCommand:['caller']});
const bindings={format:'ioalab.native-entry/1',hostRef:'claude',sourceOperations:[],setupOperation:'runtime.entry.bind',profiles:{assistant:role('personal','a'),provider:role('seller','p')}};
const api={plugin:{root:'fixture'},session:{id:async()=> 'actual-sid',cwd:async()=> 'D:/actual',version:async()=>({version:'2.1.325',base:'2.1.325'})},
 fs:{read:async file=>JSON.stringify(file==='fixture/entry-binding.json'?bindings:
   {hostRef:'claude',adapterId:'claude_code',profile:file==='a'?'personal':'seller',grant:file,bootId:boot,
    url:'http://127.0.0.1:7/v1/native-source',controlUrl:'http://127.0.0.1:7/v1/native-control'})},
 tool:{register:async()=>{}},clock:{every:(_ms,fn)=>{tick=fn;}},
 prompt:{submit:async value=>{prompts++;return{text:value.text};}},
 http:{fetch:async(_url,args)=>{const body=JSON.parse(args.body);const role=args.headers.Authorization;
  let data={};if(body.kind==='hello'){hellos.push([role,args.headers['X-Ioa-Instance']]);data={registered:true};}
  if(body.kind==='poll'){polls.push(role);data={item:role==='Bearer p'?{operationId:'provider-op',attemptId:'provider-attempt',text:'fixture'}:null};}
  if(body.kind==='accepted'){assert.equal(role,'Bearer p');assert.equal(body.operationId,'provider-op');accepted++;data={recorded:true};}
  return{ok:true,text:JSON.stringify({status:'completed',data})};}}};
const original={startup:true};assert.equal(await handlers.get('session.start')(api,{},async()=>original),original);
tick();for(let i=0;i<100 && accepted===0;i++)await new Promise(r=>setTimeout(r,2));
assert.deepEqual(polls,['Bearer a','Bearer p']);assert.equal(prompts,1);assert.equal(accepted,1);
boot='two';tick();for(let i=0;i<100 && hellos.filter(x=>x[1]==='two').length<2;i++)await new Promise(r=>setTimeout(r,2));
assert.equal(hellos.filter(x=>x[1]==='two').length,2);assert.equal(prompts,1);assert.equal(polls.length,2);
""",encoding='utf-8')
    completed=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr


def test_native_request_requires_full_causality_and_matching_binding_ack(tmp_path):
    transport=ready(tmp_path);events=[]
    def event(e):
        events.append(e)
        return BindingAck(e.result.session_id,e.result.request_id) if e.kind=='session' else None
    NativeClaudeHost(transport=transport).submit('op',payload(),'actual-session',on_event=event)
    transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})
    digest=hashlib.sha256(b'hello').hexdigest()
    proof={'observerGeneration':'generation','inputSha256':digest,'storedSha256':digest,'turnSha256':digest,
           'rowUuid':'pinned-native-row','engineBuild':'2.1.325','rawInputMatch':True,'originVerified':True,
           'uuidPinned':True,'core':True,'promptVerified':True,'uniqueTurn':True}
    message={'operationId':'op','attemptId':'attempt','sessionId':'actual-session','generation':'generation',
             'requestId':'actual-native-turn','proof':proof}
    with pytest.raises(RuntimeFault):
        transport.exchange(scope(),{**message,'kind':'reference','proof':{**proof,'rawInputMatch':False}})
    with pytest.raises(RuntimeFault):
        transport.exchange(scope(),{**message,'kind':'terminal','state':'completed','coreComplete':True})
    transport.exchange(scope(),{**message,'kind':'reference'})
    assert events[-1].result.request_id=='actual-native-turn' and events[-1].result.terminal is None
    with pytest.raises(RuntimeFault):
        transport.exchange(scope('provider'),{**message,'kind':'terminal','state':'completed','coreComplete':True})
    assert not transport.drain(0)
    transport.exchange(scope(),{**message,'kind':'terminal','state':'completed','coreComplete':True,'output':'fixture result'})
    result=events[-1].result
    assert result.terminal.request_id=='actual-native-turn' and result.state=='completed'
    assert transport.drain(0)
    count=len(events)
    transport.exchange(scope(),{**message,'kind':'terminal','state':'completed','coreComplete':True,'output':'fixture result'})
    assert len(events)==count


def test_native_digest_correlation_rejects_unknown_frame_and_ambiguous_turn(tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node is a development boundary fixture only')
    module=Path(__file__).parents[1]/'src/client/hosts/claude_code/ioalab_claude_code/resources/native/hooks/causality.mjs'
    script=tmp_path/'causal.mjs'
    script.write_text('import {Correlation,ToolCalls} from '+json.dumps(module.as_uri())+""";
import assert from 'node:assert/strict';
const core={plugin:'engine',tier:'core'};const origin={kind:'plugin',name:'fixture-plugin'};
const make=()=>new Correlation({sessionId:'sid',pluginName:'fixture-plugin',engineBuild:'2.1.325',
 observerGeneration:'gen',attemptKey:'original-attempt',body:'fixed original whole body'});
const row=(text,uuid='pinned')=>({door:'prompt',uuid,origin,message:{type:'user',role:'user',content:[{type:'text',text}]}});
const a=make();await a.prompt({text:a.body,origin},{text:a.body,origin});
await a.append(row(a.body),row('native wrapper '+a.body),core);
await a.start({text:'native wrapper '+a.body,turnId:'real-turn'},core);
assert.equal(a.binding('real-turn').linked,true);assert.equal(a.assessment('real-turn').linked,false);
a.complete({turnId:'real-turn',reason:'answer',isAborted:false},{plugin:'other',tier:'core'});
assert.equal(a.assessment('real-turn').linked,false);
a.complete({turnId:'real-turn',reason:'answer',isAborted:false},core);
assert.equal(a.assessment('real-turn').terminal,'completed');
await a.start({text:'native wrapper '+a.body,turnId:'other-turn'},core);
assert.equal(a.assessment('real-turn').linked,false);
const b=make();await b.prompt({text:b.body,origin},{text:b.body,origin});
await b.append(row('unknown '+b.body),row('unknown '+b.body),core);
await b.start({text:'unknown '+b.body,turnId:'unlinked-turn'},core);
b.complete({turnId:'unlinked-turn',reason:'answer',isAborted:false},core);
assert.equal(b.assessment('unlinked-turn').linked,false);
const calls=new ToolCalls();const step={turnId:'real-turn',index:0};
calls.chunk(step,{kind:'tool',index:0,id:'machine-tool-id',name:'mcp__ioa-native-client__sdk_tool'},core);
calls.chunk(step,{kind:'input',index:0,json:'{"name":"allowed","arguments":{"value":1}}'},core);
await assert.rejects(calls.proof('machine-tool-id','mcp__ioa-native-client__sdk_tool',{name:'allowed',arguments:{value:1}},'real-turn','gen'));
calls.settle(step,core);
const toolProof=await calls.proof('machine-tool-id','mcp__ioa-native-client__sdk_tool',{arguments:{value:1},name:'allowed'},'real-turn','gen');
assert.equal(toolProof.callId,'machine-tool-id');
await assert.rejects(calls.proof('model-supplied-id','mcp__ioa-native-client__sdk_tool',{name:'allowed',arguments:{value:1}},'real-turn','gen'));
await assert.rejects(calls.proof('machine-tool-id','mcp__ioa-native-client__sdk_tool',{name:'allowed',arguments:{value:2}},'real-turn','gen'));
""",encoding='utf-8')
    completed=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr


def test_readonly_api_receipt_requires_actual_return_and_sdk_binding_without_chat_turn(tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node is a development boundary fixture only')
    module=Path(__file__).parents[1]/'src/client/hosts/claude_code/ioalab_claude_code/resources/native/hooks/causality.mjs'
    script=tmp_path/'readonly.mjs'
    script.write_text('import {ReadonlyCalls,sha256} from '+json.dumps(module.as_uri())+""";
import assert from 'node:assert/strict';
const job={operationId:'original-observation',attemptId:'original-attempt',scopeDigest:'scope',
 actor:'actor',profile:'profile',hostRef:'claude',sessionId:'real-business-sid',prompt:'fixed authorized input',instructions:'frozen P16 definition'};
job.inputSha256=await sha256(job.prompt);
const scope={bootId:'original-boot',actor:'actor',profile:'profile',hostRef:'claude'};
let release;let calls=0;const events=[];
const api={session:{id:async()=>job.sessionId,model:async()=> 'current-native-model'},
 model:{complete:async request=>{calls++;assert.equal(request.model,'current-native-model');
   assert.equal(request.prompt,job.prompt);assert.equal('tools' in request,false);
   return new Promise(resolve=>{release=resolve;});}}};
const publish=async event=>{events.push(event);return{requestId:event.reference,sessionId:event.sessionId};};
const broker=new ReadonlyCalls();const pending=broker.run(api,job,scope,publish);
for(let n=0;n<100 && !release;n++)await new Promise(r=>setTimeout(r,1));
assert.equal(calls,1);assert.equal(events.some(e=>e.kind==='observation_return'),false);
assert.equal(broker.run(api,job,scope,publish),pending);
assert.throws(()=>broker.run(api,job,{...scope,actor:'other-role'},publish));
assert.throws(()=>broker.run(api,{...job,instructions:'changed snapshot'},scope,publish));
release({isAnswered:true,text:'{"observed":true}',usage:{}});
const result=await pending;
assert.equal(result.state,'completed');assert.equal(calls,1);
assert.equal(events.at(-1).output,'{"observed":true}');
assert.equal(events.at(-1).reference.startsWith('claude-completion:'),true);
assert.equal(events.at(-1).sessionId,'real-business-sid');
const missingAck=new ReadonlyCalls();let deniedCalls=0;
const denied={session:api.session,model:{complete:async()=>{deniedCalls++;}}};
await assert.rejects(missingAck.run(denied,{...job,operationId:'unbound'},scope,async()=>null));
assert.equal(deniedCalls,0);
const lost=[];const disconnected={session:api.session,model:{complete:async()=>{throw Error('fixture transport loss');}}};
const uncertain=await new ReadonlyCalls().run(disconnected,{...job,operationId:'lost'},scope,async e=>{
 lost.push(e);return{requestId:e.reference,sessionId:e.sessionId};});
assert.equal(uncertain.state,'unknown');assert.equal(lost.some(e=>e.kind==='observation_return'),false);
""",encoding='utf-8')
    completed=subprocess.run([node,str(script)],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr


def test_observation_control_lane_runs_alongside_chat_and_persists_before_sdk_terminal(tmp_path):
    receipts=tmp_path/'receipts'
    transport=NativeTransport(host_ref='claude',receipt_root=receipts);transport.start()
    grant={**scope(),'bootId':'boot','installationRef':'installation','accountBindingRef':'account'}
    native={'sessionId':'real-business-session','generation':'native-generation'}
    transport.exchange(grant,{**native,'kind':'hello','workspace':str(tmp_path)})
    def event(e):
        if e.kind=='terminal':assert len(list(receipts.glob('*.json')))==1
        return BindingAck(e.result.session_id,e.result.request_id) if e.kind=='session' else None
    transport.submit('chat',payload(),native['sessionId'],on_event=event)
    transport.exchange(grant,{**native,'kind':'poll'})
    execution=HostExecution('assistant-actor','assistant','business','observation','job',
        HostDefinition('P16','1','frozen','frozen readonly instructions'),kind='observation')
    transport.observe('observe',payload(),native['sessionId'],execution=execution,on_event=event)
    job=transport.exchange(grant,{**native,'kind':'poll_observation'})['item']
    assert job['operationId']=='observe' and 'real-business-session' in transport.active
    original={**native,'operationId':'observe','attemptId':'attempt','scopeDigest':'scope',
              'actor':grant['actor'],'profile':grant['profile'],'hostRef':'claude','bootId':'boot',
              'inputSha256':job['inputSha256'],'reference':'claude-completion:fixture'}
    ack=transport.exchange(grant,{**original,'kind':'observation_reference'})
    assert ack=={'requestId':'claude-completion:fixture','sessionId':native['sessionId']}
    returned={**original,'kind':'observation_return','state':'completed','reason':'answered','output':'{"observed":true}'}
    with pytest.raises(RuntimeFault):transport.exchange(grant,returned)
    transport.exchange(grant,{**original,'kind':'observation_dispatch'})
    assert transport.observations.pending() and not list(receipts.glob('*.json'))
    with pytest.raises(RuntimeFault):transport.exchange({**grant,'accountBindingRef':'changed'},returned)
    transport.exchange(grant,returned)
    assert not transport.observations.pending()
    # This observation cannot end the separately active original chat request.
    assert not transport.drain(0) and transport.active[native['sessionId']]==('chat','attempt')


@pytest.mark.parametrize('fresh',(False,True))
def test_s01_observation_waits_for_real_api_result_instead_of_returning_queue_ack(tmp_path,fresh):
    transport=NativeTransport(host_ref='claude',receipt_root=tmp_path/'receipts');transport.start()
    grant={**scope(),'bootId':'boot','installationRef':'installation','accountBindingRef':'account'}
    native={'sessionId':'real-session','generation':'generation'}
    if not fresh:transport.exchange(grant,{**native,'kind':'hello','workspace':str(tmp_path)})
    execution=HostExecution('assistant-actor','assistant','business','observation','job',
        HostDefinition('P16','1','schema-digest','frozen P16 schema'),kind='observation',
        authorization={'nativeToolsEnabled':False})
    results=[];errors=[];events=[];created=threading.Event()
    class Processes:
        def create(self,**owner):
            assert owner=={'operation_id':'op','actor':'assistant-actor','profile':'assistant'}
            created.set();return native['sessionId']
    def event(e):
        events.append(e)
        return BindingAck(e.result.session_id,e.result.request_id) if e.kind=='session' else None
    def run():
        try:results.append(NativeClaudeHost(transport=transport,processes=Processes()).submit('op',payload(),None if fresh else native['sessionId'],execution=execution,on_event=event))
        except BaseException as exc:errors.append(exc)
    worker=threading.Thread(target=run,daemon=True);worker.start()
    if fresh:
        assert created.wait(2) and not events and not transport.sessions
        transport.exchange(grant,{**native,'kind':'hello','workspace':str(tmp_path)})
    item=None
    for _ in range(100):
        item=transport.exchange(grant,{**native,'kind':'poll_observation'})['item']
        if item:break
        time.sleep(.01)
    assert item and worker.is_alive() and not results
    body={**native,'operationId':'op','attemptId':'attempt','scopeDigest':'scope','actor':grant['actor'],
          'profile':grant['profile'],'hostRef':'claude','bootId':'boot','inputSha256':item['inputSha256'],
          'reference':'claude-completion:actual-api-fixture'}
    transport.exchange(grant,{**body,'kind':'observation_reference'})
    transport.exchange(grant,{**body,'kind':'observation_dispatch'})
    assert worker.is_alive() and not results
    transport.exchange(grant,{**body,'kind':'observation_return','state':'completed','reason':'answered','output':'{"observed":true}'})
    worker.join(3)
    assert not worker.is_alive() and not errors
    assert results[0].terminal is not None and results[0].output=='{"observed":true}'
    assert transport.drain(0)


@pytest.mark.parametrize('tool_unknown',(False,True))
def test_native_tool_callback_requires_machine_call_and_drains_before_terminal(tmp_path,tool_unknown):
    transport=ready(tmp_path);events=[];calls=[]
    entered=threading.Event();leave=threading.Event();errors=[]
    execution=HostExecution('assistant-actor','assistant','business','interaction','job',
        HostDefinition('definition','1','digest','frozen instructions'),
        tools=(HostToolDeclaration('allowed','fixture',{'type':'object'}),),authorization={'nativeToolsEnabled':True})
    def event(value):
        events.append(value)
        return BindingAck(value.result.session_id,value.result.request_id) if value.kind=='session' else None
    def callback(call):
        calls.append(call);entered.set()
        assert leave.wait(5)
        return (HostToolResult(error=SubmissionError('unknown_effect','sdk_tool','accepted','Inspect original effect'))
                if tool_unknown else HostToolResult(value={'ok':True}))
    transport.submit('op',payload(),'actual-session',execution=execution,on_event=event,on_tool=callback)
    transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})
    digest=hashlib.sha256(b'hello').hexdigest()
    proof={'observerGeneration':'generation','inputSha256':digest,'storedSha256':digest,'turnSha256':digest,
           'rowUuid':'pinned-row','engineBuild':'2.1.325','rawInputMatch':True,'originVerified':True,
           'uuidPinned':True,'core':True,'promptVerified':True,'uniqueTurn':True}
    base={'operationId':'op','attemptId':'attempt','sessionId':'actual-session','generation':'generation',
          'requestId':'actual-turn','proof':proof}
    transport.exchange(scope(),{**base,'kind':'reference'})
    args={'value':1}
    args_digest=hashlib.sha256(json.dumps({'name':'allowed','arguments':args},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    tool={**base,'kind':'tool','callId':'native-call','name':'allowed','arguments':args,
          'toolProof':{'observerGeneration':'generation','callId':'native-call','requestId':'actual-turn',
                       'nativeName':'mcp__ioa-native-client__sdk_tool','argumentsSha256':args_digest,'core':True}}
    with pytest.raises(RuntimeFault):transport.exchange(scope(),{**tool,'callId':'model-id'})
    assert not calls
    def run():
        try:
            result=transport.exchange(scope(),tool)['toolResult']
            assert result['error']['code']=='unknown_effect' if tool_unknown else result['value']=={'ok':True}
        except BaseException as exc:errors.append(exc)
    worker=threading.Thread(target=run);worker.start()
    try:
        assert entered.wait(2)
        # This needs the same transport lock; it must run while the callback waits.
        assert transport.check()['state']=='busy'
        result=transport.exchange(scope(),{**base,'kind':'terminal','state':'completed','coreComplete':True})
        assert result['draining'] and not transport.drain(0)
        assert not any(event.kind=='terminal' for event in events)
    finally:leave.set();worker.join(5)
    assert not worker.is_alive() and not errors
    assert len(calls)==1 and calls[0].call_id=='native-call' and calls[0].request_id=='actual-turn'
    assert events[-1].result.terminal is not None and transport.drain(0)
    assert events[-1].result.error.code=='tool_unknown' if tool_unknown else events[-1].result.error is None


"""Append to owned suite with B; fixture boundary supplies native machine events."""
@pytest.mark.parametrize('terminal_callback_failure',(False,True))
def test_external_private_s01_binds_new_session_calls_original_reply_and_waits_for_machine_terminal(tmp_path,terminal_callback_failure):
    from datetime import datetime,timezone,timedelta
    transport=NativeTransport(host_ref='claude',receipt_root=tmp_path/'receipts');transport.start()
    opened=[];events=[];replies=[];results=[]
    class Processes:
        def create(self,**ownership):opened.append(ownership);return 'owned-new-session'
    execution=HostExecution('assistant-actor','assistant','business','interaction','job',
        HostDefinition('private-rule','1','frozen-private-rule','Only process the original private message'),
        tools=(HostToolDeclaration('ioa_reply','Reply for original request',{'type':'object'}),),
        authorization={'nativeToolsEnabled':True},deadline=(datetime.now(timezone.utc)+timedelta(seconds=10)).isoformat())
    def event(value):
        events.append(value)
        if value.kind=='terminal' and terminal_callback_failure:raise RuntimeFault('unknown_result','Fixture terminal consumer failed after actual native completion')
        return BindingAck(value.result.session_id,value.result.request_id) if value.kind=='session' else None
    def reply(call):replies.append(call);return HostToolResult(value={'status':'submitted','replyReceipt':'original-sdk-receipt'})
    worker=threading.Thread(target=lambda:results.append(NativeClaudeHost(transport=transport,processes=Processes()).submit(
        'original-private-op',payload('external private data'),execution=execution,on_event=event,on_tool=reply)))
    worker.start()
    for _ in range(100):
        if opened:break
        time.sleep(.01)
    assert opened==[{'operation_id':'original-private-op','actor':'assistant-actor','profile':'assistant'}]
    assert not events and not replies
    native={'sessionId':'owned-new-session','generation':'native-generation'}
    grant={**scope(),'bootId':'sdk-boot','installationRef':'install','accountBindingRef':'account'}
    transport.exchange(grant,{**native,'kind':'hello','workspace':str(tmp_path)})
    item=None
    for _ in range(100):
        item=transport.exchange(grant,{**native,'kind':'poll'})['item']
        if item:break
        time.sleep(.01)
    assert item and worker.is_alive() and not results and not replies
    assert events[0].result.session_id=='owned-new-session'
    assert 'frozen-private-rule' in item['text'] and 'external private data' in item['text']
    digest=hashlib.sha256(item['text'].encode()).hexdigest()
    proof={'observerGeneration':'native-generation','inputSha256':digest,'storedSha256':digest,'turnSha256':digest,
        'rowUuid':'pinned-row','engineBuild':'fixture','rawInputMatch':True,'originVerified':True,
        'uuidPinned':True,'core':True,'promptVerified':True,'uniqueTurn':True}
    bound={**native,'operationId':'original-private-op','attemptId':'attempt','requestId':'actual-native-turn','proof':proof}
    transport.exchange(grant,{**bound,'kind':'reference'})
    args={'output':{'reply':'actual fixture result'}}
    canonical=json.dumps({'name':'ioa_reply','arguments':args},sort_keys=True,separators=(',',':'))
    tool={'callId':'actual-core-tool-id','requestId':'actual-native-turn','nativeName':'mcp__ioa-native-client__sdk_tool',
        'argumentsSha256':hashlib.sha256(canonical.encode()).hexdigest(),'observerGeneration':'native-generation','core':True}
    result=transport.exchange(grant,{**bound,'kind':'tool','callId':'actual-core-tool-id','name':'ioa_reply','arguments':args,'toolProof':tool})
    assert result['toolResult']['value']['replyReceipt']=='original-sdk-receipt' and len(replies)==1
    assert replies[0].session_id=='owned-new-session' and replies[0].request_id=='actual-native-turn'
    assert worker.is_alive() and not results  # Reply receipt is not native terminal.
    transport.exchange(grant,{**bound,'kind':'terminal','state':'completed','coreComplete':True,'output':'finished'})
    worker.join(3)
    assert not worker.is_alive() and results[0].state=='completed' and results[0].terminal.request_id=='actual-native-turn'
    assert results[0].output=='finished' and transport.drain(0)
    assert (results[0].error is not None)==terminal_callback_failure
    if terminal_callback_failure:assert results[0].error.phase=='event_reporting'


def test_fixed_native_capture_uses_actual_ancestor_image_not_path_or_direct_parent(tmp_path,monkeypatch):
    import ioalab_claude_code.native_process as module
    cli=tmp_path/'claude.exe';cli.write_bytes(b'not executed')
    ancestors=[{'pid':3,'parent':2,'created':30,'image':str(tmp_path/'python.exe')},
        {'pid':2,'parent':1,'created':20,'image':str(tmp_path/'intermediate.exe')},
        {'pid':1,'parent':0,'created':10,'image':str(cli)}]
    assert native_cli_from_ancestors(ancestors)==str(cli)
    monkeypatch.setattr(module,'windows_native_ancestors',lambda:iter(ancestors))
    monkeypatch.setattr(module.shutil,'which',lambda *_:pytest.fail('Capture guessed a PATH executable'))
    monkeypatch.delenv('CLAUDE_CONFIG_DIR',raising=False)
    assert module.capture_from_native_process()['cli']==str(cli)
    for invalid in (ancestors[:2], [ancestors[0],{**ancestors[1],'created':40},ancestors[2]],
                    [ancestors[0],{**ancestors[1],'pid':5},ancestors[2]]):
        with pytest.raises(RuntimeFault):native_cli_from_ancestors(invalid)


@pytest.mark.parametrize('poll_native',(False,True))
def test_private_s01_deadline_distinguishes_proven_queue_not_sent_from_unknown_dispatch(tmp_path,poll_native):
    from datetime import datetime,timezone,timedelta
    transport=ready(tmp_path);results=[]
    execution=HostExecution('assistant-actor','assistant','business','interaction','job',
        HostDefinition('rule','1','digest','private instructions'),authorization={'nativeToolsEnabled':True},
        deadline=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
    worker=threading.Thread(target=lambda:results.append(NativeClaudeHost(transport=transport).submit('original-op',payload(),
        'actual-session',execution=execution,on_event=lambda e:BindingAck(e.result.session_id,e.result.request_id))))
    worker.start()
    for _ in range(100):
        if transport.requests:break
        time.sleep(.005)
    if poll_native:
        assert transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})['item']
    worker.join(3)
    assert not worker.is_alive() and len(results)==1 and results[0].terminal is None
    assert results[0].error.dispatch==('unknown' if poll_native else 'not_submitted')
    assert transport.exchange(scope(),{'kind':'poll','sessionId':'actual-session','generation':'generation'})=={'item':None}
    assert transport.drain(0) is (not poll_native)


def test_setup_resource_waits_for_actual_saved_receipt_callback_before_drain(tmp_path):
    capture=launch_capture(tmp_path);store=NativeLaunchStore(host_ref='claude',home=tmp_path/'runtime')
    entered=threading.Event();leave=threading.Event();results=[]
    def permission(_scope,packet):
        if packet['action']=='pending':return {'operationId':'setup-op','intentRef':'intent'}
        if packet['action']=='claim':return {'operationId':'setup-op','claimRef':'claim','targetDigest':packet['targetDigest']}
        entered.set();assert leave.wait(5)
        return {'operationId':'setup-op','state':'completed','phase':'bound'}
    transport=NativeTransport(host_ref='claude',launch_store=store);transport.bind_entry_control(permission);transport.start()
    native={'sessionId':'actual-session','generation':'generation'}
    transport.exchange(launch_scope(),{**native,'kind':'hello','workspace':capture['projectDirectory']})
    worker=threading.Thread(target=lambda:results.append(transport.exchange(launch_scope(),
        {**native,'kind':'entry_setup','entryRef':'source','capture':capture})))
    worker.start()
    try:
        assert entered.wait(3) and store.path.exists() and not results and not transport.drain(0)
        with pytest.raises(RuntimeFault):transport.close()
    finally:leave.set();worker.join(3)
    assert not worker.is_alive() and results[0]['state']=='completed' and transport.drain(0)


def test_private_request_keeps_original_authenticated_grant_scope_after_same_sid_refresh(tmp_path):
    transport=NativeTransport(host_ref='claude',receipt_root=tmp_path/'receipts');transport.start();events=[]
    grant=launch_scope();native={'sessionId':'actual-session','generation':'generation'}
    transport.exchange(grant,{**native,'kind':'hello','workspace':str(tmp_path)})
    NativeClaudeHost(transport=transport).submit('op',payload(),'actual-session',on_event=lambda e:(events.append(e) or BindingAck(e.result.session_id,e.result.request_id)))
    assert transport.exchange(grant,{**native,'kind':'poll'})['item']
    accepted={**native,'kind':'accepted','operationId':'op','attemptId':'attempt'}
    for field in ('bootId','installationRef','accountBindingRef'):
        with pytest.raises(RuntimeFault):transport.exchange({**grant,field:'different'},accepted)
    assert len(events)==1
    transport.exchange(grant,accepted)
    assert events[-1].kind=='accepted' and events[-1].result.terminal is None
