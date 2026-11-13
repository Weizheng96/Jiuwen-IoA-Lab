"""Failed test-reset source handoff at task, process and OS-lock boundaries."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from deploy.layout import Layout
from deploy.files import save


@pytest.fixture
def reset_handoff(tmp_path, monkeypatch):
    import json, uuid
    from deploy import reset_resume
    from deploy.jobs import Job, SOURCE_FILES, source_identity
    from deploy.release import digest
    from deploy.repair import pointer_for
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');identity=layout.claim()
    old,new=tmp_path/'old-source',tmp_path/'new-source'
    for root in (old,new):
        for name in ('deploy','src','vendor'):
            (root/name).mkdir(parents=True);(root/name/'file').write_text(name)
        for name in SOURCE_FILES:
            file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('fixed')
    (new/'src/file').write_text('reviewed SDK fix')
    value={'trial':None,'admin_assistant':{'enabled':True}}; input_digest=digest(value)
    save(layout.private/'initial.json',{'deploymentId':identity['deploymentId'],'inputDigest':input_digest,'input':value})
    save(layout.private/'installation.json',{'initialized':False,'deploymentId':identity['deploymentId']})
    (layout.state/'core').mkdir();(layout.state/'core/.core.lock').write_text('0')
    (layout.state/'core/new-data').write_text('must survive SDK recovery')
    parent=Job(layout,uuid.uuid4().hex);parent.create('test-reset',source_identity(old),input_digest)
    candidate={'setId':'parent-set'}
    save(parent.folder/'progress.json',{'stage':'test-reset','outcome':'failed','candidate':candidate,'testReset':{'phase':'initializing'}})
    save(layout.private/'current-job.json',pointer_for(parent,parent.request()))
    observed={'status':'stopped','candidate':candidate}
    monkeypatch.setattr(Job,'observe',lambda job:{'status':observed['status']})
    monkeypatch.setattr(Job,'submit',lambda job:{'status':'waiting','operationId':job.operation})
    monkeypatch.setattr(reset_resume.activation,'current',lambda _:observed['candidate'])
    return layout,parent,new,observed


def test_failed_reset_source_handoff_preserves_parent_request_and_new_data(reset_handoff):
    from deploy import reset_resume
    from deploy.jobs import Job
    layout,parent,new,_=reset_handoff
    before=(parent.folder/'request.json').read_bytes()
    result=reset_resume.submit(layout,new,parent.operation)
    child=Job(layout,result['operationId']);request=child.request()
    assert request['resumeTestReset']['parentOperation']==parent.operation
    assert request['resumeTestReset']['parentSource']==parent.request()['source']
    assert request['inputDigest']==parent.request()['inputDigest']
    assert (parent.folder/'request.json').read_bytes()==before
    assert (layout.state/'core/new-data').read_text()=='must survive SDK recovery'
    assert reset_resume.submit(layout,new,parent.operation)==result


@pytest.mark.parametrize('change',['active','phase','candidate','input','initialized'])
def test_reset_source_handoff_refuses_wrong_original_boundary(reset_handoff,change):
    from deploy import reset_resume
    from deploy.release import read_json
    layout,parent,new,observed=reset_handoff
    if change=='active':observed['status']='waiting'
    elif change=='candidate':observed['candidate']={'setId':'foreign'}
    elif change=='phase':
        p=read_json(parent.folder/'progress.json');p['testReset']['phase']='clearing';save(parent.folder/'progress.json',p)
    elif change=='input':
        p=read_json(layout.private/'initial.json');p['input']['trial']={};save(layout.private/'initial.json',p)
    else:save(layout.private/'installation.json',{'initialized':True})
    pointer=(layout.private/'current-job.json').read_bytes()
    with pytest.raises(RuntimeError):reset_resume.submit(layout,new,parent.operation)
    assert (layout.private/'current-job.json').read_bytes()==pointer
    assert (layout.state/'core/new-data').exists()


def test_partial_reset_handoff_holds_existing_core_and_sdk_locks(tmp_path,monkeypatch):
    from deploy import reset_resume
    from deploy.files import writer
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    core=layout.state/'core';core.mkdir();(core/'.core.lock').write_text('0')
    home=layout.state/'agents/official';account=home/'accounts/ISP_001';account.mkdir(parents=True)
    config=layout.config/'agents';config.mkdir()
    save(config/'official.json',{'data_dir':str(home)})
    (home/'deployment.lock').write_text('0');save(home/'state.json',{'accounts':{'ISP_001':{}}})
    sdk=account/'resident';sdk.mkdir();(sdk/'client.lock').write_text('0')
    save(account/'config.json',{'data_dir':str(sdk)})
    installer=SimpleNamespace(layout=layout,metadata={'officialPrepared':False,'administratorPrepared':False},
        request={'resumeTestReset':{'parentOperation':'original'}},config=SimpleNamespace(input={'admin_assistant':{'enabled':True}}))
    monkeypatch.setattr('deploy.test_server.role_processes',lambda _:False)
    for lock in [core/'.core.lock',home/'deployment.lock',sdk/'client.lock']:
        with writer(lock):
            with pytest.raises(RuntimeError):
                with reset_resume.partial_leases(installer):pass
    with reset_resume.partial_leases(installer):pass
    monkeypatch.setattr('deploy.test_server.role_processes',lambda _:True)
    with pytest.raises(RuntimeError):
        with reset_resume.partial_leases(installer):pass


@pytest.mark.parametrize('interrupted', [False, True])
def test_completed_reset_handoff_retains_bound_evidence_and_cleanup_is_restartable(reset_handoff, interrupted):
    from deploy import reset_resume
    from deploy.jobs import Job
    from deploy.release import read_json
    layout,parent,new,_=reset_handoff
    result=reset_resume.submit(layout,new,parent.operation);child=Job(layout,result['operationId'])
    original=parent.request()
    if interrupted:
        save(child.folder/'resumed-parent.json',{'request':original,'progress':read_json(parent.folder/'progress.json')})
        (parent.folder/'request.json').unlink()
    reset_resume.finish(child,child.request())
    assert not parent.folder.exists()
    assert read_json(child.folder/'resumed-parent.json')['request']==original
    assert (layout.state/'core/new-data').exists()
    reset_resume.finish(child,child.request())
