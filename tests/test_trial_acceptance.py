import importlib.util
import json
from pathlib import Path
import pytest
from ioalab_trial.acceptance import CHECKS,validated_checks


def recorder():
    path=Path(__file__).parents[1]/'tools/verification/trial_acceptance.py'
    spec=importlib.util.spec_from_file_location('trial_acceptance_tool',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def reviewed(tmp_path):
    tool=recorder();context=tmp_path/'context';context.mkdir();(context/'Dockerfile').write_text('reviewed immutable program')
    inputs=tool.context_inputs(context,'a'*40)
    evidence=tmp_path/'root-chrome-observation.json';evidence.write_text(json.dumps({'actualObservedFixture':'test data only'}))
    review={**inputs,'reviewedBy':'root-chrome','browserSessionId':'fixture-browser-session',
            'engineeringImageId':'sha256:'+'c'*64,
            'checks':{check:{'status':'passed','artifacts':{evidence.name:tool.sha(evidence)}} for check in CHECKS}}
    path=tmp_path/'review.json';path.write_text(json.dumps(review))
    return tool,context,path


def test_engineering_context_has_no_production_acceptance(tmp_path):
    tool,context,review=reviewed(tmp_path)
    assert not any(validated_checks(context/'acceptance').values())
    tool.record(context,review)
    assert all(validated_checks(context/'acceptance').values())


@pytest.mark.parametrize('changed',['artifact','source','context','image','missing-check'])
def test_accepted_image_rejects_changed_or_incomplete_evidence(tmp_path,changed):
    tool,context,review=reviewed(tmp_path);tool.record(context,review)
    root=context/'acceptance';path=root/'native-acceptance.json';receipt=json.loads(path.read_text())
    if changed=='artifact':(root/next(iter(receipt['checks'][CHECKS[0]]['artifacts']))).write_text('changed')
    elif changed=='source':receipt['sourceRevision']='b'*40
    elif changed=='context':receipt['contextSha256']='b'*64
    elif changed=='image':receipt['engineeringImageId']='mutable:tag'
    else:receipt['checks'].pop(CHECKS[-1])
    path.write_text(json.dumps(receipt))
    assert not any(validated_checks(root).values())


def test_recorder_rejects_new_program_inputs_and_plain_boolean_claims(tmp_path):
    tool,context,review=reviewed(tmp_path)
    (context/'Dockerfile').write_text('new program')
    with pytest.raises(ValueError,match='changed'):tool.record(context,review)
    fresh=tmp_path/'fresh';fresh.mkdir();(fresh/'Dockerfile').write_text('original')
    tool.context_inputs(fresh,'a'*40)
    review.write_text(json.dumps({check:True for check in CHECKS}))
    with pytest.raises(ValueError,match='root-Chrome'):tool.record(fresh,review)


def test_engineering_epoch_is_never_allocated_and_partial_creation_not_retried(tmp_path,monkeypatch):
    import ioalab_trial as sandbox  # the tool imports DockerSandbox from the package entry
    path=Path(__file__).parents[1]/'tools/verification/trial_engineering.py'
    spec=importlib.util.spec_from_file_location('trial_engineering_tool',path)
    tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
    config=tmp_path/'engineering.json';config.write_text(json.dumps({'deployment_profile':'linux-server','sandbox':{'deployment':'ioa-engineering'}}))
    inputs=tmp_path/'inputs.json';inputs.write_text(json.dumps({'sourceRevision':'a'*40,'contextSha256':'b'*64}))
    state=tmp_path/'operation.json';calls=[]
    class Sandbox:
        image_id='sha256:'+'d'*64
        def __init__(self,*args,**kwargs):pass
        def prepare(self,seat,epoch):calls.append(('prepare',seat,epoch))
        def ui(self,*args):return 'http://127.0.0.1:29002'
        def stop(self,*args):calls.append('stop')
        def verify_stopped(self,*args):calls.append('verify')
        def wipe(self,*args):calls.append('wipe')
    monkeypatch.setattr(sandbox,'DockerSandbox',Sandbox)
    result=tool.run('prepare',config,state,inputs)
    assert result['engineeringOnly'] and result['productionReady'] is False and len(calls)==1
    with pytest.raises(ValueError,match='already exist'):tool.run('prepare',config,state,inputs)
    assert tool.run('retire',config,state,inputs)['phase']=='retired'
    assert calls[1:]==['stop','verify','wipe']
    state.unlink()
    def fail(*args):raise RuntimeError('partial resource creation')
    monkeypatch.setattr(Sandbox,'prepare',fail)
    with pytest.raises(RuntimeError,match='partial'):tool.run('prepare',config,state,inputs)
    with pytest.raises(ValueError,match='already exist'):tool.run('prepare',config,state,inputs)


def test_engineering_observe_uses_real_local_skill_list_contract(tmp_path,monkeypatch):
    import httpx,websockets.sync.client
    import ioalab_trial as sandbox  # the tool imports DockerSandbox from the package entry
    path=Path(__file__).parents[1]/'tools/verification/trial_engineering.py'
    spec=importlib.util.spec_from_file_location('trial_engineering_observe_tool',path)
    tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
    config=tmp_path/'engineering.json';config.write_text(json.dumps({'deployment_profile':'linux-server','sandbox':{'deployment':'ioa-engineering'}}))
    inputs=tmp_path/'inputs.json';inputs.write_text(json.dumps({'sourceRevision':'a'*40,'contextSha256':'b'*64}))
    state=tmp_path/'operation.json';methods=[]
    class Sandbox:
        image_id='sha256:'+'d'*64
        def __init__(self,*a,**kw):pass
        def prepare(self,*args):pass
        def ui(self,*args):return 'http://127.0.0.1:29002'
    monkeypatch.setattr(sandbox,'DockerSandbox',Sandbox)
    tool.run('prepare',config,state,inputs)
    monkeypatch.setattr(httpx,'get',lambda *a,**kw:httpx.Response(200,content=b'native',request=httpx.Request('GET','http://native/')))
    payload={'skills':[{'name':'ioalab-delegate','installed':True,'enabled':True,'source':'local'}]}
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def send(self,raw):self.request=json.loads(raw);methods.append(self.request['method'])
        def recv(self,**kwargs):
            data=payload if self.request['method']=='skills.list' else {'a2ui_enabled':True}
            return json.dumps({'type':'res','id':self.request['id'],'ok':True,'payload':data})
    monkeypatch.setattr(websockets.sync.client,'connect',lambda *a,**kw:Socket())
    result=tool.run('observe',config,state,inputs)
    assert methods==['skills.list','config.get','session.list']
    assert result['observations']['skills.list']['assistantSkillPresent'] is True
    payload={'plugins':[{'name':'ioalab-delegate'}]}
    with pytest.raises(RuntimeError,match='invalid public schema'):tool.run('observe',config,state,inputs)
