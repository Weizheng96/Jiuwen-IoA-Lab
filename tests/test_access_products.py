"""Packaged product skills, immutable installation inputs and real configuration reuse."""
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.access import main
from ioalab_sdk.runtime import start
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_runtime import configuration

ROOT=Path(__file__).resolve().parents[1]


def bootstrap():
    spec=importlib.util.spec_from_file_location('ioalab_client.test_commands',ROOT/'src/client/ioalab_client/commands.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_native_preflight_permission_failure_retains_original_id_without_dispatch(tmp_path,monkeypatch,capsys):
    from ioalab_sdk.runtime import lifecycle
    config=tmp_path/'existing.json'
    read=Path.read_bytes
    def denied(path):
        if path==config:raise PermissionError('private OS detail must not escape')
        return read(path)
    monkeypatch.setattr(Path,'read_bytes',denied)
    monkeypatch.setattr(lifecycle,'LocalClient',lambda *a,**k:pytest.fail('Preflight failure reached RPC'))
    packet={'operation':'runtime.status','operationId':'original-read','arguments':{},'contextRef':None}
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(packet)))
    assert main(['call','runtime.status','--config',str(config),'--request-stdin'])==1
    output=capsys.readouterr().out;result=json.loads(output)
    assert result['operationId']=='original-read' and result['status']=='rejected'
    assert result['phase']=='preflight' and result['dispatch']=='not_submitted'
    assert result['error']['recovery']=='inspect_configuration_permissions'
    assert 'private OS detail' not in output


@pytest.mark.parametrize('invalid', ['bad-json','wrong-operation','bad-context'])
def test_invalid_native_envelope_is_not_associated_or_used_to_read_configuration(tmp_path,monkeypatch,capsys,invalid):
    config=tmp_path/'not-read.json'
    packet={'operation':'runtime.status','operationId':'untrusted-id','arguments':{},'contextRef':None}
    if invalid=='wrong-operation':packet['operation']='runtime.stop'
    if invalid=='bad-context':packet['contextRef']={'entryRef':'valid','sessionId':'model-input'}
    monkeypatch.setattr(sys,'stdin',io.StringIO('{' if invalid=='bad-json' else json.dumps(packet)))
    monkeypatch.setattr(Path,'read_bytes',lambda *a:pytest.fail('Invalid packet read configuration'))
    assert main(['call','runtime.status','--config',str(config),'--request-stdin'])==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='rejected' and result.get('operationId') is None
    assert 'dispatch' not in result


def test_native_dispatched_rpc_loss_preserves_unknown_not_preflight(tmp_path,monkeypatch,capsys):
    from ioalab_sdk.runtime import client as lifecycle
    config=tmp_path/'settings.json'
    private_json(config,{'data_dir':str(tmp_path/'home')})
    packet={'operation':'runtime.status','operationId':'original-read','arguments':{},'contextRef':None}
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(packet)))
    calls=[]
    def lost(operation,*args,**kwargs):
        calls.append((operation,kwargs['operation_id']))
        raise OSError('response lost')
    monkeypatch.setattr(lifecycle,'LocalClient',lambda *a,**k:SimpleNamespace(call=lost))
    assert main(['call','runtime.status','--config',str(config),'--request-stdin'])==1
    result=json.loads(capsys.readouterr().out)
    assert calls==[('runtime.status','original-read')]
    assert result['status']=='unknown' and result['operationId']=='original-read'
    assert result.get('dispatch')!='not_submitted' and result['error']['dispatch']=='unknown'


def test_configuration_permission_handoff_preserves_account_instead_of_registering(tmp_path,monkeypatch):
    from ioalab_client.onboarding import summary,markdown
    config=tmp_path/'existing.json'
    read=Path.read_bytes
    def denied(path):
        if path==config:raise PermissionError('no access')
        return read(path)
    monkeypatch.setattr(Path,'read_bytes',denied)
    result=summary('codex',config=config)
    assert result['error']['recovery']=='inspect_configuration_permissions'
    assert result['data']['accountName'] is None
    assert [(step['id'],step['actor']) for step in result['data']['pending']]==[('configuration_permissions','agent')]
    rendered=markdown(result)
    assert '配置权限受限' in rendered and 'runtime.configuration' in rendered
    assert 'account.setup' not in rendered and '生成新账号' not in result['data']['pending'][0]['instruction']


def test_malformed_configuration_is_distinct_from_permission_denied(tmp_path):
    from ioalab_sdk.runtime.config import read_configuration
    config=tmp_path/'bad.json';config.write_bytes(b'{not-json')
    with pytest.raises(RuntimeFault) as failed:read_configuration(config)
    assert failed.value.code=='invalid_input' and failed.value.recovery=='configure'
    assert 'UTF-8 JSON' in str(failed.value)


@pytest.mark.parametrize('host',['codex','jiuwen','claude_code'])
def test_three_native_wrappers_preserve_sdk_preflight_refusal_and_reject_unrelated_receipts(tmp_path,monkeypatch,capsys,host):
    import shutil
    packet={'operation':'runtime.status','operationId':'original-read','arguments':{},'contextRef':None}
    config=tmp_path/'unreadable.json';read=Path.read_bytes
    def denied(path):
        if path==config:raise PermissionError('fixture permission boundary')
        return read(path)
    with monkeypatch.context() as local:
        local.setattr(Path,'read_bytes',denied)
        local.setattr(sys,'stdin',io.StringIO(json.dumps(packet)))
        assert main(['call','runtime.status','--config',str(config),'--request-stdin'])==1
        rejected=json.loads(capsys.readouterr().out)
    assert rejected['dispatch']=='not_submitted' and rejected['operationId']=='original-read'
    unknown={'operationId':'original-read','status':'unknown','data':None,
             'error':{'code':'unknown_result','dispatch':'unknown','recovery':'resume_original'}}
    cases=[rejected,{**rejected,'operationId':'another-request'},
           {k:v for k,v in rejected.items() if k!='operationId'},unknown]
    response=tmp_path/'response.json';caller=tmp_path/'caller.py'
    caller.write_text('import sys\nfrom pathlib import Path\nsys.stdout.write(Path(sys.argv[1]).read_text(encoding="utf-8"))\n',encoding='utf-8')
    profile={'profile':'personal','descriptorFile':str(tmp_path/'never-read-descriptor'),
             'callerCommand':[sys.executable,str(caller),str(response)],'ensureCommand':['must-not-start']}
    binding={'format':'ioalab.native-entry/1','hostRef':'original','sourceOperations':[],
             'setupOperation':'runtime.entry.bind','profiles':{'assistant':profile}}
    (tmp_path/'entry-binding.json').write_text(json.dumps(binding),encoding='utf-8')
    inputs={key:value for key,value in packet.items() if key!='contextRef'}
    if host=='claude_code':
        node=shutil.which('node')
        if not node:pytest.skip('Node is a development fixture for the actual Claude wrapper')
        module=ROOT/'src/client/hosts/claude_code/ioalab_claude_code/resources/native/wrapper.mjs'
        script=tmp_path/'claude-wrapper.mjs'
        script.write_text('import {invoke} from '+json.dumps(module.as_uri())+';\n'+
            'import {spawnSync} from "node:child_process";\n'+
            'const api={process:{run:async(argv,opt)=>{const p=spawnSync(argv[0],argv.slice(1),{input:opt.stdin,encoding:"utf8"});return {stdout:p.stdout,exitCode:p.status};}}};\n'+
            'try {console.log(JSON.stringify(await invoke(api,'+json.dumps({**profile,'sourceOperations':[],'setupOperation':'runtime.entry.bind'})+','+json.dumps(inputs)+')));}\n'+
            'catch(e){if(!e.submissionUnknown)throw e;console.log(JSON.stringify({status:"unknown",error:{dispatch:"unknown"}}));}\n',encoding='utf-8')
        def invoke():
            process=subprocess.run([node,str(script)],capture_output=True,text=True,encoding='utf-8',timeout=10)
            assert process.returncode==0,process.stderr
            return json.loads(process.stdout)
    elif host=='codex':
        from ioalab_codex.native_entry import invoke as native_invoke
        args=tmp_path/'arguments.json';args.write_text(json.dumps({'operationId':'original-read'}),encoding='utf-8')
        def invoke():return native_invoke(tmp_path,'assistant',['runtime.status','--args-file',str(args)])[1]
    else:
        path=ROOT/'src/client/hosts/jiuwen/ioalab_jiuwen/resources/native/entry.py'
        spec=importlib.util.spec_from_file_location('jiuwen_wrapper_receipt_fixture',path)
        native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
        entry=native.Entry(tmp_path)
        def invoke():return entry.invoke('assistant',inputs,capture=lambda:pytest.fail('Control captured a source'))
    for index,value in enumerate(cases):
        response.write_text(json.dumps(value),encoding='utf-8')
        actual=invoke()
        if index in (0,3):assert actual==value
        else:
            assert actual['status']=='unknown'
            assert actual.get('dispatch')!='not_submitted' and actual['error']['dispatch']=='unknown'


def test_bound_skill_role_cannot_be_overridden_by_call_arguments(monkeypatch):
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('Role override reached installation or RPC'))
    with pytest.raises(SystemExit) as error:
        module.main(['--role','assistant','--role','service','runtime.status'])
    assert error.value.code==2


def test_validate_is_read_only_and_never_returns_credentials(tmp_path,capsys):
    config=tmp_path/'settings.json';home=tmp_path/'not-created'
    private_json(config,{'platform_url':'http://127.0.0.1:8000','username':'public-account','password':'private-test-value',
                        'data_dir':str(home),'identities':[{'key':'personal','role':'assistant','name':'My assistant'}]})
    assert main(['validate','--config',str(config)])==0
    text=capsys.readouterr().out;value=json.loads(text)
    assert value['status']=='completed' and not home.exists()
    assert 'password' not in text and 'private-test-value' not in text
    original=RuntimeConfig.load(config);summary=original.public_summary()
    assert replace(original,password='another-private-value').public_summary()==summary
    assert replace(original,max_model_runs=original.max_model_runs+1).public_summary()['configurationDigest']!=summary['configurationDigest']
    assert replace(original,provider={'services':{'service':{'native_tools':{'write_file':'allow'}}}}).public_summary()['configurationDigest']!=summary['configurationDigest']


def test_existing_instance_cannot_hide_new_role_configuration(platform,tmp_path,monkeypatch):
    config,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(config)).start()
    try:
        before=LocalClient(runtime.config.home).call('runtime.status')['data']
        assert start(config)['data']['bootId']==before['bootId']
        edited=read_json(config);edited['identities'].append({'key':'service','role':'service','name':'New role'})
        private_json(config,edited)
        original_spawn=subprocess.Popen
        def spawn(command,*args,**kwargs):
            if 'serve' in command:pytest.fail('spawned a competing runtime')
            return original_spawn(command,*args,**kwargs)
        monkeypatch.setattr('ioalab_sdk.runtime.lifecycle.subprocess.Popen',spawn)
        with pytest.raises(RuntimeFault) as exc:start(config)
        assert exc.value.code=='configuration_changed'
        after=LocalClient(runtime.config.home).call('runtime.status')['data']
        assert after['bootId']==before['bootId'] and after['configurationDigest']==before['configurationDigest']
        assert len(after['identities'])==1
    finally:runtime.close()


@pytest.mark.parametrize('role,operation,example',[
    ('assistant','provider.publish','assistant.delegation.prepare'),
    ('assistant','prepare','assistant.delegation.prepare'),
    ('assistant','assistant','assistant.delegation.prepare'),
    ('service','assistant.delegation.prepare','provider.tasks.get'),
    ('service','prepare','provider.tasks.get'),
    ('service','provider','provider.tasks.get'),
])
def test_unrelated_role_cannot_trigger_bootstrap_or_rpc(tmp_path,monkeypatch,capsys,role,operation,example):
    module=bootstrap();root=tmp_path/'skill';(root/'assets').mkdir(parents=True)
    (root/'assets/skill.json').write_text(json.dumps({'role':role}));module.__file__=str(root/'scripts/ioalab.py')
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('unrelated operation installed packages'))
    monkeypatch.setattr(module,'invoke',lambda *args:pytest.fail('invalid operation invoked SDK'))
    arguments=tmp_path/'arguments.json';arguments.write_text('{"request":"unchanged"}')
    assert module.main(['--role',role,operation,'--config',str(tmp_path/'missing-config.json'),'--args-file',str(arguments)])==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='rejected' and result['error']['code']=='invalid_input'
    assert result['error']['recovery']=='correct_input'
    assert example in result['error']['message'] and 'complete operation name' in result['error']['message']
    assert 'do not change identity roles' in result['error']['message']
    assert json.loads(arguments.read_text())=={'request':'unchanged'}


@pytest.mark.parametrize('role,operation,status,error',[
    ('assistant','assistant.delegation.prepare','waiting',None),
    ('service','provider.tasks.get','completed',None),
    ('assistant','assistant.tools.list','rejected',{'code':'not_ready','message':'Unknown capability','recovery':'inspect_capabilities'}),
])
def test_complete_role_operation_is_forwarded_without_alias_or_error_reclassification(tmp_path,monkeypatch,capsys,role,operation,status,error):
    from types import SimpleNamespace
    module=bootstrap();root=tmp_path/'skill';(root/'assets').mkdir(parents=True)
    (root/'assets/skill.json').write_text(json.dumps({'role':role}));module.__file__=str(root/'scripts/ioalab.py')
    python=tmp_path/'installed-python.exe';prepared={'status':'completed','data':{'python':str(python)}}
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda *args,**kwargs:prepared))
    config=tmp_path/'external-config.json';arguments=tmp_path/'arguments.json'
    arguments.write_text('{"request":"unchanged"}')
    expected={'status':status,'data':{'reference':'original'},'error':error};calls=[]
    def invoke(actual_python,command):
        calls.append((actual_python,command))
        if command[1:4]==['ioalab_sdk.access','call','runtime.status']:
            return SimpleNamespace(stdout=json.dumps({'status':'completed','data':{}}),stderr='',returncode=0)
        return SimpleNamespace(stdout=json.dumps(expected),stderr='',returncode=1 if status=='rejected' else 0)
    monkeypatch.setattr(module,'invoke',invoke)
    assert module.main(['--role',role,operation,'--config',str(config),'--args-file',str(arguments)])==(1 if status=='rejected' else 0)
    assert json.loads(capsys.readouterr().out)==expected
    expected_calls=[(python,['-m','ioalab_sdk.access','call',operation,'--config',str(config.resolve()),'--args-file',str(arguments.resolve())])]
    if operation=='assistant.delegation.prepare':expected_calls.insert(0,(python,['-m','ioalab_sdk.access','call','runtime.status','--config',str(config.resolve())]))
    assert calls==expected_calls
    assert json.loads(arguments.read_text())=={'request':'unchanged'} and not config.exists()


def test_both_real_product_bundles_share_one_installed_client(tmp_path):
    from ioalab_client import export_skills
    paths=export_skills(tmp_path/'bundles',source=ROOT/'src/client/skills',python=sys.executable)
    for path,root,role in zip(paths,['ioalab-delegate','ioalab-provider'],['assistant','service']):
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            assert root+'/SKILL.md' in names and root+'/references/setup.md' in names
            assert not any('/assets/' in name or name.endswith('/installer.py') or name.endswith('.whl') for name in names)
            script=archive.read(root+'/scripts/ioalab.py').decode()
            assert repr(sys.executable) in script and role in script
            assert archive.read(root+'/references/shared-runtime.md')==(ROOT/'src/client/skills/_shared/references/shared-runtime.md').read_bytes()
    assert export_skills(tmp_path/'bundles',source=ROOT/'src/client/skills',python=sys.executable)==paths
    paths[0].write_bytes(b'previous-user-export')
    with pytest.raises(RuntimeError,match='preserve'):
        export_skills(tmp_path/'bundles',source=ROOT/'src/client/skills',python=sys.executable)
    assert paths[0].read_bytes()==b'previous-user-export'


def test_new_downloads_only_serve_fixed_product_names(platform,client,tmp_path,monkeypatch):
    monkeypatch.setattr(platform.s,'bundles_dir',tmp_path)
    for name in ('ioalab-assistant-skill.zip','ioalab-provider-skill.zip'):
        assert client.get('/downloads/'+name).status_code==503
        data=b'public product bundle '+name.encode();(tmp_path/name).write_bytes(data)
        assert client.get('/downloads/'+name).content==data
    (tmp_path/'private.json').write_text('private')
    assert client.get('/downloads/private.json').status_code==404
    import hashlib
    manifest=client.get('/api/access/bundles').json()
    assert len(manifest['bundles'])==2
    for item in manifest['bundles']:
        actual=client.get('/downloads/'+item['name']).content
        assert item['available'] and item['size']==len(actual)
        assert item['sha256']==hashlib.sha256(actual).hexdigest()
    assert client.get('/readme_agent.md',follow_redirects=False).status_code==404


@pytest.mark.parametrize('parent_encoding',['ascii','gbk'])
def test_product_entry_returns_utf8_under_non_utf8_parent(tmp_path,parent_encoding,monkeypatch,capsys):
    from types import SimpleNamespace
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'notice':'中文账号与参数路径','python':sys.executable}}))
    assert module.main(['--role','assistant','runtime.install.status'])==0
    result=capsys.readouterr().out
    assert result.isascii() and json.loads(result)['data']['notice']=='中文账号与参数路径'


def test_old_installation_status_preserves_observation_and_allows_safe_stop(platform,tmp_path,monkeypatch,capsys):
    config,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(config)).start()
    try:
        command=['call','runtime.status','--config',str(config)]
        assert main(command)==0
        before=json.loads(capsys.readouterr().out)
        # Same package versions but a different release must not look like this caller's ready runtime.
        monkeypatch.setattr(runtime,'installation',{**runtime.installation,'releaseId':'f'*64})
        assert main(command)==1
        mismatch=json.loads(capsys.readouterr().out)
        assert mismatch['status']=='rejected' and mismatch['error']['code']=='installation_changed'
        assert mismatch['data']['bootId']==before['data']['bootId']
        assert mismatch['data']['runtimeId']==before['data']['runtimeId']
        assert mismatch['data']['installation']['releaseId']=='f'*64
        args=tmp_path/'stop.json';private_json(args,{})
        assert main(['call','runtime.stop','--config',str(config),'--args-file',str(args)])==0
        stopped=json.loads(capsys.readouterr().out)
        assert stopped['status']=='completed' and stopped['data']['preserved']
        assert stopped['data']['bootId']==before['data']['bootId']
    finally:runtime.close()


@pytest.mark.parametrize('role', ['assistant', 'service'])
@pytest.mark.parametrize('existing', [False, True])
def test_mcp_registration_precedes_account_and_preserves_configuration(tmp_path, monkeypatch, capsys, role, existing):
    from types import SimpleNamespace
    module = bootstrap()
    config = tmp_path / 'account.json'
    original = b'{"platform_url":"http://127.0.0.1:8750"}'
    if existing:
        config.write_bytes(original)
    monkeypatch.setattr(module, 'installation', lambda: SimpleNamespace(prepare=lambda: {
        'status': 'completed', 'data': {'python': sys.executable}}))
    monkeypatch.setattr(module, 'invoke', lambda *args: pytest.fail('Registration must not open accounts or start a resident'))
    assert module.main(['--role', role, 'runtime.mcp.configuration', '--config', str(config)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'completed'
    data = result['data']
    assert (data['stage'], data['nextAction'], data['accountRequired']) == ('mcp', 'enable_in_current_chat', False)
    assert data['setupArguments'] == {'role': role}
    assert data['args'] == ['-m', 'ioalab_sdk.mcp', '--config', str(config)]
    assert data['jiuwenJson']['mcpServers']['ioalab']['args'] == data['args']
    assert data['jiuwenAutoSetup'] == {'tool': 'connect_mcp', 'arguments': {
        'name': 'ioalab', 'command': sys.executable, 'args': data['args']}}
    assert config.exists() == existing
    if existing:
        assert config.read_bytes() == original
    assert not list(tmp_path.glob('.ioa-setup-*'))

@pytest.mark.parametrize('operation',['runtime.stop','account.refresh','operations.get','assistant.messaging.received'])
def test_control_and_original_observation_never_auto_start(tmp_path,monkeypatch,capsys,operation):
    from types import SimpleNamespace
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    monkeypatch.setattr(module,'ensure_running',lambda *args:pytest.fail('Control recursively started the runtime'))
    calls=[]
    def invoke(python,command):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({'status':'completed','data':{},'error':None}),stderr='',returncode=0)
    monkeypatch.setattr(module,'invoke',invoke)
    assert module.main(['--role','assistant',operation,'--config',str(tmp_path/'config.json')])==0
    assert len(calls)==1 and calls[0][1:3]==['ioalab_sdk.access','call']


def test_business_waiting_start_never_submits_or_rewrites_original_arguments(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    waiting={'status':'waiting','data':{'startingOrBusy':True},'error':{'code':'not_ready'}}
    monkeypatch.setattr(module,'ensure_running',lambda *args:SimpleNamespace(stdout=json.dumps(waiting),returncode=0))
    monkeypatch.setattr(module,'invoke',lambda *args:pytest.fail('Busy runtime received a new business call'))
    path=tmp_path/'original.json';path.write_text('{"operationId":"keep-original"}')
    assert module.main(['--role','assistant','assistant.delegation.flow','--args-file',str(path)])==0
    assert json.loads(capsys.readouterr().out)==waiting
    assert json.loads(path.read_text())=={'operationId':'keep-original'}


def test_native_stdin_reuses_bound_caller_and_forwards_sdk_entry_envelope(tmp_path,monkeypatch,capsys):
    import io
    from types import SimpleNamespace
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    packet={'operation':'assistant.delegation.flow','operationId':'original-native-operation',
            'arguments':{'action':'begin','input':{}},'contextRef':{'entryRef':'sdk-issued-entry'}}
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(packet)))
    calls=[]
    def invoke(python,arguments,**options):
        calls.append((arguments,options))
        return SimpleNamespace(stdout=json.dumps({'operationId':packet['operationId'],'status':'completed','data':{},'error':None}),stderr='',returncode=0)
    monkeypatch.setattr(module,'invoke',invoke)
    assert module.main(['--role','assistant','--config',str(tmp_path/'bound.json'),'--native-input'])==0
    assert calls[0][0][1:4]==['ioalab_sdk.access','call','runtime.status']
    assert calls[1][0][-1]=='--request-stdin'
    assert json.loads(calls[1][1]['input'])==packet
    assert 'entryRef' not in packet['arguments']


def test_native_stdin_cannot_override_bound_role_or_config(tmp_path,monkeypatch,capsys):
    import io
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('Unbound native metadata reached installation'))
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'operation':'assistant.delegation.flow',
        'operationId':'original','arguments':{},'contextRef':{'entryRef':'opaque'},'role':'service'})))
    assert module.main(['--role','assistant','--native-input'])==1
    assert json.loads(capsys.readouterr().out)['status']=='rejected'


def test_native_control_stdin_reuses_private_args_file_and_never_starts(tmp_path,monkeypatch,capsys):
    import io
    from types import SimpleNamespace
    module=bootstrap()
    private_json(tmp_path/'bound.json', {'username':'test-owner','password':'test-only',
        'data_dir':str(tmp_path/'home'),'identities':[{'key':'personal','role':'assistant','name':'Owner'}]})
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    monkeypatch.setattr(module,'ensure_running',lambda *args:pytest.fail('Control recursively started the runtime'))
    values={'expectedDigest':'original-config-digest','changes':{'max_model_runs':2}}
    packet={'operation':'runtime.configure','operationId':'original-control','arguments':values,'contextRef':None}
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(packet)))
    paths=[]
    def invoke(python,arguments,**options):
        assert arguments[1:3]==['ioalab_client.runtime','configure']
        path=Path(arguments[arguments.index('--args-file')+1]);paths.append(path)
        assert json.loads(path.read_text(encoding='utf-8'))==values
        assert 'entryRef' not in path.read_text(encoding='utf-8')
        assert not options
        return SimpleNamespace(stdout=json.dumps({'status':'completed','data':{},'error':None}),stderr='',returncode=0)
    monkeypatch.setattr(module,'invoke',invoke)
    assert module.main(['--role','assistant','--config',str(tmp_path/'bound.json'),'--native-input'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['operationId']=='original-control' and result['operationKind']=='control_association'
    assert len(paths)==1 and not paths[0].exists()


def test_native_unknown_operation_cannot_wake_or_enter_installation(monkeypatch,capsys):
    import io
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('Unknown operation entered installation'))
    monkeypatch.setattr(module,'ensure_running',lambda *args:pytest.fail('Unknown operation woke the client'))
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'operation':'assistant.unknown',
        'operationId':'original-unknown','arguments':{},'contextRef':None})))
    assert module.main(['--role','assistant','--native-input'])==1
    assert json.loads(capsys.readouterr().out)['error']['code']=='invalid_input'


def test_native_role_effect_cannot_borrow_control_null_source(monkeypatch,capsys):
    import io
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('Unbound effect entered installation'))
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'operation':'assistant.delegation.flow',
        'operationId':'original-unbound','arguments':{'action':'begin','input':{}},'contextRef':None})))
    assert module.main(['--role','assistant','--native-input'])==1
    assert json.loads(capsys.readouterr().out)['error']['code']=='not_ready'


def test_native_business_waiting_start_keeps_startup_reference_and_original_request(monkeypatch,capsys):
    import io
    from types import SimpleNamespace
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'operation':'assistant.delegation.flow',
        'operationId':'original-business','arguments':{'action':'begin','input':{}},'contextRef':{'entryRef':'issued'}})))
    waiting={'operationId':'actual-startup-control','status':'waiting','data':{'startingOrBusy':True},'error':None}
    monkeypatch.setattr(module,'ensure_running',lambda *args:SimpleNamespace(stdout=json.dumps(waiting),returncode=0))
    monkeypatch.setattr(module,'invoke',lambda *args,**kwargs:pytest.fail('Waiting startup submitted business'))
    assert module.main(['--role','assistant','--native-input'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['operationId']=='original-business' and result['dispatch']=='not_submitted'
    assert result['phase']=='ensure_client' and result['data']['startup']==waiting


def test_native_rpc_different_operation_id_is_not_rewritten(monkeypatch,capsys):
    import io
    from types import SimpleNamespace
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'operation':'runtime.status',
        'operationId':'original-read','arguments':{},'contextRef':None})))
    monkeypatch.setattr(module,'invoke',lambda *args,**kwargs:SimpleNamespace(stdout=json.dumps({
        'operationId':'another-real-rpc','status':'completed','data':{'marker':'not-the-original'}}),stderr='',returncode=0))
    assert module.main(['--role','assistant','--native-input'])==1
    result=json.loads(capsys.readouterr().out)
    assert result['operationId']=='original-read' and result['status']=='unknown'
    assert result['data'] is None


@pytest.mark.parametrize('status',['completed','waiting','unknown'])
def test_native_setup_forwards_sdk_entry_ref_and_original_state_without_role_start(monkeypatch,capsys,status):
    from ioalab_sdk.runtime import NATIVE_ENTRY_BIND_OPERATION
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    monkeypatch.setattr(module,'ensure_running',lambda *args:pytest.fail('Setup wrapper already owns explicit ensure'))
    packet={'operation':NATIVE_ENTRY_BIND_OPERATION,'operationId':'original-native-setup',
            'arguments':{},'contextRef':{'entryRef':'sdk-verified-by-server'}}
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(packet)))
    expected={'operationId':packet['operationId'],'status':status,'data':{'phase':'sdk-original-setup'},'error':None}
    def invoke(python,arguments,**options):
        assert arguments[1:4]==['ioalab_sdk.access','call',NATIVE_ENTRY_BIND_OPERATION]
        assert arguments[-1]=='--request-stdin'
        assert json.loads(options['input'])==packet
        return SimpleNamespace(stdout=json.dumps(expected),stderr='',returncode=1 if status=='unknown' else 0)
    monkeypatch.setattr(module,'invoke',invoke)
    assert module.main(['--role','assistant','--native-input'])==(1 if status=='unknown' else 0)
    assert json.loads(capsys.readouterr().out)==expected


@pytest.mark.parametrize('arguments,reference',[
    ({'sessionId':'model-input'},{'entryRef':'opaque'}),
    ({'cwd':'C:/model-input'},{'entryRef':'opaque'}),
    ({'confirmed':True},{'entryRef':'opaque'}),
    ({},None),
])
def test_native_setup_rejects_model_fields_or_missing_source_before_installation(monkeypatch,capsys,arguments,reference):
    from ioalab_sdk.runtime import NATIVE_ENTRY_BIND_OPERATION
    module=bootstrap()
    monkeypatch.setattr(module,'installation',lambda:pytest.fail('Invalid setup crossed installation boundary'))
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'operation':NATIVE_ENTRY_BIND_OPERATION,
        'operationId':'original-invalid-setup','arguments':arguments,'contextRef':reference})))
    assert module.main(['--role','assistant','--native-input'])==1
    assert json.loads(capsys.readouterr().out)['status']=='rejected'


def test_p07_setup_identifier_comes_from_sdk_declaration_and_not_role_operations(tmp_path):
    from ioalab_client.skills import native_entry_binding
    from ioalab_sdk.runtime import NATIVE_ENTRY_BIND_OPERATION,CATALOG
    bound=native_entry_binding(python=sys.executable,config=tmp_path/'original.json',host_ref='original-host')
    assert bound['setupOperation']==NATIVE_ENTRY_BIND_OPERATION
    assert NATIVE_ENTRY_BIND_OPERATION in CATALOG
    assert NATIVE_ENTRY_BIND_OPERATION not in bound['sourceOperations']
    assert 'runtime.status' not in bound['sourceOperations']


@pytest.mark.parametrize('state,start', [
    ({'status':'completed','data':{'installation':{'assembly':'ioalab-jiuwen'}}}, False),
    ({'status':'rejected','error':{'recovery':'runtime.start'}}, True),
    ({'status':'rejected','error':{'recovery':'inspect_configuration'}}, False),
    ({'status':'unknown','error':{'recovery':'runtime.start'}}, False),
])
def test_role_call_reuses_live_assembly_and_preserves_preflight_errors(tmp_path,monkeypatch,capsys,state,start):
    module=bootstrap();config=tmp_path/'config.json'
    private_json(config, {'username':'test-owner','password':'test-only','data_dir':str(tmp_path/'home'),
        'identities':[{'key':'personal','role':'assistant','name':'Owner'}]})
    original=config.read_bytes()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    calls=[]
    def invoke(python,command):
        calls.append(command)
        value=state if 'runtime.status' in command else {'status':'completed','data':{'items':[]}}
        return SimpleNamespace(stdout=json.dumps(value),stderr='',returncode=0 if value['status']=='completed' else 1)
    monkeypatch.setattr(module,'invoke',invoke)
    module.main(['--role','assistant','assistant.messaging.flow','--config',str(config)])
    output=json.loads(capsys.readouterr().out)
    assert sum(command[1:3]==['ioalab_client.runtime','start'] for command in calls)==int(start)
    if state['status']!='completed' and not start:
        assert output==state and len(calls)==1
    else:assert calls[-1][1:4]==['ioalab_sdk.access','call','assistant.messaging.flow']
    assert config.read_bytes()==original


def test_role_control_preserves_original_application_and_refuses_migration(platform,tmp_path,monkeypatch,capsys):
    from contextlib import redirect_stdout
    from importlib import import_module
    module=bootstrap();config,_=configuration(platform,tmp_path,service=False)
    original=json.loads(config.read_text());original['host']={'command':['/usr/bin/true'], 'data_dir':str(tmp_path/'swarm')}
    private_json(config,original)
    runtime=ResidentRuntime(RuntimeConfig.load(config),assembly='ioalab-jiuwen').start()
    monkeypatch.setattr(module,'installation',lambda:SimpleNamespace(prepare=lambda:{'status':'completed','data':{'python':sys.executable}}))
    def process(python,command,**options):
        output=io.StringIO()
        with redirect_stdout(output):code=import_module(command[1]+'.__main__').main(command[2:]) if command[1]=='ioalab_jiuwen' else import_module(command[1]).main(command[2:])
        return SimpleNamespace(stdout=output.getvalue(),stderr='',returncode=code)
    monkeypatch.setattr(module,'invoke',process)
    try:
        before=config.read_bytes();boot=runtime.ipc.boot_id
        assert module.main(['--role','assistant','runtime.configuration','--config',str(config)])==0
        data=json.loads(capsys.readouterr().out)['data']
        assert set(data['hostFields'])=={'command','data_dir'}
        args=tmp_path/'migration.json';private_json(args,{'expectedDigest':data['configurationDigest'],'changes':{'host':{'migrate_from':'jiuwen'}}})
        assert module.main(['--role','assistant','runtime.configure','--config',str(config),'--args-file',str(args)])==1
        rejected=json.loads(capsys.readouterr().out)
        assert rejected['error']['code']=='invalid_input' and rejected['error']['recovery']!='runtime.stop'
        assert LocalClient(runtime.config.home).call('runtime.status')['data']['bootId']==boot
        assert config.read_bytes()==before
    finally:runtime.close()
    assert module.main(['--role','assistant','runtime.validate','--config',str(config)])==0
    assert json.loads(capsys.readouterr().out)['status']=='completed'
