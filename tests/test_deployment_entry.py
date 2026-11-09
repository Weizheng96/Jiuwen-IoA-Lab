"""Default entry observation is read-only; fixed bootstrap prepares extensions before web start."""
import json
from pathlib import Path
from types import SimpleNamespace

from deploy import __main__ as entry
from deploy.installation import Installation
from deploy.layout import Layout,environment_text
from deploy import units
from test_production_release import source,release


def test_status_does_not_initialize_or_repair_any_role_state(tmp_path,monkeypatch,capsys):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    monkeypatch.setattr(entry,'require_host',lambda:None)
    assert entry.main(['status','--state',str(layout.state)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['task']['status']=='not_started' and result['currentSet'] is None
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not (layout.private/'initial.json').exists()


def test_bootstrap_prepares_admin_extension_before_handoff_and_original_web_start(tmp_path):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    folder=layout.config/'agents';folder.mkdir()
    for name in ('official','assistant'):(folder/(name+'.env')).write_text(environment_text({'HOME':str(layout.state/'agents')}))
    events=[];plans=units.plans(layout,administrator=True,core={'bind':'127.0.0.1','port':8750,'public_url':'https://ioa.example'})
    manager=SimpleNamespace(install=lambda _:events.append(('units',)),
        start=lambda name:events.append(('start',name)),enable=lambda name:events.append(('enable',name)))
    accounts=SimpleNamespace(reserve=lambda path,*args,**kwargs:events.append(('reserve',path.name)),
        handoff=lambda path,*args:events.append(('handoff',path.name)))
    configuration=SimpleNamespace(input={'admin_assistant':{'enabled':True}},
        official=lambda _:None,administrator=lambda _:layout.private/'admin-assistant/config.json',
        core=lambda:{'admin':{'username':'custom-admin','password':'bootstrap-only-sentinel'}})
    installed=[]
    def call(module,command,*args,**kwargs):
        events.append(('prepare',module,command));installed.append((module,command,kwargs))
        return {'status':'completed'}
    selected=object.__new__(Installation)
    selected.layout,selected.metadata,selected.plans=layout,{},plans
    selected.contents,selected.manager,selected.accounts,selected.config={},manager,accounts,configuration
    selected.workspace=lambda *args,**kwargs:{'status':'completed'}
    selected.permissions=lambda:None;selected.wait=lambda _:None;selected.core_ready=lambda:None
    selected.installed=call;selected.save_metadata=lambda:None
    selected.bootstrap()
    extension=events.index(('prepare','ioalab_admin_assistant','prepare'))
    handoff=events.index(('handoff','swarm-admin'))
    start=events.index(('start',plans['swarm-agent']['unit']))
    assert extension<handoff<start
    authorized=next(kwargs for module,command,kwargs in installed if command=='authorize')
    assert authorized['role']=='agents' and authorized['environment']['IOALAB_BOOTSTRAP_ADMIN_PASSWORD']=='bootstrap-only-sentinel'
    assert all('IOALAB_BOOTSTRAP_ADMIN_PASSWORD' not in kwargs['environment'] for module,command,kwargs in installed if command!='authorize')
    count=len(installed);selected.bootstrap()
    assert len(installed)==count,'Completed preparation/authorization must not be replayed'


def test_accepted_background_install_does_not_look_like_a_shell_failure(monkeypatch,capsys):
    monkeypatch.setattr(entry,'require_host',lambda:None)
    monkeypatch.setattr(entry,'submit',lambda _: {'status':'waiting','operationId':'original'})
    assert entry.main(['install','--config','not-read-by-this-fixture.json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result=={'status':'waiting','operationId':'original'}


def test_core_identity_checks_reuse_installed_probe_for_local_and_public_endpoints():
    selected=object.__new__(Installation)
    selected.layout=SimpleNamespace(environments=Path('/opt/runtime/envs'))
    selected.config=SimpleNamespace(input={'public_url':'https://192.0.2.10'},core=lambda:{
        'runtime':{'bind':'127.0.0.1','port':8750,'public_url':'https://192.0.2.10'}})
    calls=[];selected.command=lambda argv,**kwargs:calls.append((argv,kwargs))
    selected.core_ready()
    assert len(calls)==2
    for (argv,kwargs),url in zip(calls,('http://127.0.0.1:8750','https://192.0.2.10')):
        assert argv[:4]==[Path('/opt/runtime/envs/current/core/bin/python'),'-I','-m','ioalab.readiness']
        assert argv[argv.index('--url')+1]==url and '--config' not in argv
        assert kwargs=={'role':'core'}


def test_public_stop_preserves_waiting_and_uses_original_layout(monkeypatch,capsys):
    from deploy import lifecycle
    layout=object();seen=[]
    monkeypatch.setattr(entry,'require_host',lambda:None)
    monkeypatch.setattr(entry,'existing',lambda state:layout)
    monkeypatch.setattr(lifecycle,'stop',lambda selected,wait_seconds:seen.append((selected,wait_seconds)) or
                        {'status':'waiting','phase':'stopping','allOwnedGroupsEmpty':False})
    assert entry.main(['stop','--state','/original/state','--wait-seconds','0'])==0
    result=json.loads(capsys.readouterr().out)
    assert seen==[(layout,0)] and result['status']=='waiting' and result['allOwnedGroupsEmpty'] is False


def test_trial_readiness_cannot_promote_incomplete_or_wrong_capacity(monkeypatch,tmp_path):
    import pytest
    import deploy.installation as module
    config=tmp_path/'trial.json';config.write_text(json.dumps({'origin':'https://trial.example','confirm_origin':'https://confirm.example','formal_origin':'https://formal.example','admission_key':'a'*40,'port':8766}))
    formal=tmp_path/'platform.json';formal.write_text(json.dumps({'runtime':{'public_url':'https://formal.example','trial_url':'https://trial.example','trial_confirm_url':'https://confirm.example','trial_admission_key':'a'*40}}))
    selected=object.__new__(Installation)
    selected.progress={'requiredRunning':['trial']};selected.plans={'trial':{'unit':'ioalab-trial.service'}}
    selected.manager=SimpleNamespace(ready=lambda _: {'ActiveState':'active','UnitFileState':'disabled'})
    selected.config=SimpleNamespace(input={'platform_config':str(formal),'trial':{'config':str(config)}})
    for health in ({'ready':False,'capacity':3},{'ready':True,'capacity':1}):
        monkeypatch.setattr(module,'http_json',lambda _,value=health:value)
        with pytest.raises(RuntimeError,match='three-seat'):selected.readiness()
    requests=[]
    monkeypatch.setattr(module,'http_json',lambda request:requests.append(request) or {'ready':True,'capacity':3})
    assert selected.readiness()['trial']['ready'] is True
    assert requests[0].get_header('Host')=='trial.example'


def test_installed_preparation_does_not_require_access_to_private_source_directory(tmp_path,monkeypatch):
    import deploy.installation as module
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    home=layout.state/'agents';home.mkdir();job=layout.private/'job';job.mkdir()
    subject=object.__new__(Installation);subject.layout=layout;subject.job=SimpleNamespace(folder=job)
    subject.request={'source':{'root':str(tmp_path/'private-source')}}
    subject.role=lambda _: {'uid':1201,'gid':1202,'home':str(home),'name':'ioalab-agents'}
    calls=[]
    def run(argv,**kwargs):
        calls.append(kwargs);return SimpleNamespace(returncode=0,stdout='{"status":"completed"}\n',stderr='')
    monkeypatch.setattr(module.subprocess,'run',run)
    subject.command(['/installed/python','-I','-m','public-module'],role='agents')
    assert calls[-1]['cwd']==home and calls[-1]['user']==1201 and calls[-1]['extra_groups']==[]
    subject.command(['/installed/python','-I','-m','public-module'])
    assert calls[-1]['cwd']==layout.private and 'user' not in calls[-1]
    subject.command(['/builder/python','-m','deploy.build'],cwd=subject.request['source']['root'])
    assert calls[-1]['cwd']==Path(subject.request['source']['root'])


def readiness_subject(source,tmp_path):
    declarations=[{'account':'provider-a','services':[{'id':'alpha'},{'id':'beta'}]},
                  {'account':'provider-b','services':[{'id':'gamma'}]}]
    (source/'deploy/default-seed.json').write_text(json.dumps({'domains':declarations}),encoding='utf-8')
    folder=release(source,tmp_path/'readiness-release')
    subject=object.__new__(Installation)
    subject.progress={'requiredRunning':['official'],'candidate':{'release':str(folder)}};subject.request={'action':'upgrade'}
    subject.plans={'official':{'unit':'ioalab-official.service'}}
    subject.manager=SimpleNamespace(ready=lambda _: {'ActiveState':'active','UnitFileState':'enabled'})
    subject.layout=SimpleNamespace(config=Path('/not-read'))
    rows=[{'account':domain['account'],'runtime':{'platformConnected':True,'host':{'available':True}},
        'services':[{'profile':service['id'],'agentId':'runtime-id-'+service['id'],
                    'current':{'indexReady':True,'templateReady':True,'localExecution':{'executionReady':True},
                    'admissionPolicy':{'accepting':True}}} for service in domain['services']]} for domain in declarations]
    subject.installed=lambda *args,**kwargs:{'status':'completed','accounts':rows}
    return subject,rows


def test_upgrade_readiness_preserves_pause_and_reports_available_service_count(monkeypatch,source,tmp_path):
    import deploy.installation as module
    monkeypatch.setattr(module,'read_environment',lambda _: {})
    subject,rows=readiness_subject(source,tmp_path);rows[0]['services'][0]['current']['admissionPolicy']['accepting']=False
    report=subject.readiness()
    assert report['runtimeReady'] is True and report['official']['availableServices']==2
    assert rows[0]['services'][0]['current']['admissionPolicy']['accepting'] is False


def test_official_readiness_rejects_incomplete_status_instead_of_counting_it_as_paused(monkeypatch,source,tmp_path):
    import pytest
    import deploy.installation as module
    monkeypatch.setattr(module,'read_environment',lambda _: {})
    for index,error in enumerate(('missing','error')):
        subject,rows=readiness_subject(source,tmp_path/str(index))
        if error=='missing':rows[0]['services'].pop()
        else:rows[0]['services'][0]['error']={'code':'not_ready'}
        with pytest.raises(RuntimeError,match='status is incomplete'):subject.readiness()


def test_readiness_rejects_wrong_account_or_catalog_and_initial_pause(monkeypatch,source,tmp_path):
    import copy
    import pytest
    import deploy.installation as module
    monkeypatch.setattr(module,'read_environment',lambda _: {})
    subject,rows=readiness_subject(source,tmp_path)
    original=copy.deepcopy(rows)
    for fault in ('account','duplicate-account','profile','duplicate-profile','cross-account'):
        rows[:]=copy.deepcopy(original)
        if fault=='account':rows[0]['account']='unexpected'
        elif fault=='duplicate-account':rows[1]['account']=rows[0]['account']
        elif fault=='profile':rows[0]['services'][0]['profile']='unknown'
        elif fault=='duplicate-profile':rows[0]['services'][1]['profile']='alpha'
        else:rows[0]['services'][0],rows[1]['services'][0]=rows[1]['services'][0],rows[0]['services'][0]
        with pytest.raises(RuntimeError):subject.readiness()
    rows[:]=copy.deepcopy(original);subject.request['action']='install'
    assert subject.readiness()['official']['availableServices']==3
    rows[0]['services'][0]['current']['admissionPolicy']['accepting']=False
    with pytest.raises(RuntimeError,match='publication/index is not ready'):subject.readiness()

