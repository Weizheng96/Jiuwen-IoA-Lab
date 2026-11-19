"""Explicit disposable-test-server operations at service and filesystem boundaries."""
from pathlib import Path
import pytest
from deploy.layout import Layout
from deploy.files import save
from deploy.test_server import clear_state, prune
from test_deployment_activation import owned, candidate
from test_production_release import linux_target, source
from deploy import activation
from types import SimpleNamespace
from deploy.test_server import Reset
from test_deployment_upgrade_flow import setup


def test_clear_requires_released_writers_and_keeps_configuration(tmp_path):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    for name in ('core','agents'):(layout.state/name).mkdir()
    (layout.state/'core/records.sqlite').write_bytes(b'old accounts and contracts')
    (layout.state/'agents/session.json').write_bytes(b'old conversation')
    auth=layout.state/'agents/agently-auth';auth.mkdir();(auth/'token').write_bytes(b'external authorization')
    keychain=layout.state/'agents/.local/share/agently-cli/agents/ioalab-admin'
    keychain.mkdir(parents=True);(keychain/'master.key').write_bytes(b'private master key')
    (keychain/'bootstrap_token').write_bytes(b'encrypted external token')
    old=layout.state/'agents/.local/share/old-project';old.mkdir();(old/'session').write_text('discard')
    core=layout.config/'core';core.mkdir();(core/'platform.json').write_bytes(b'private deployment configuration')
    metadata={'paths':{str(layout.state/'agents/old-sdk'):{'phase':'owned'}},'officialPrepared':True,'initialized':True}
    with pytest.raises(RuntimeError):clear_state(layout,metadata,{},released=lambda:False)
    assert (layout.state/'core/records.sqlite').exists()
    clear_state(layout,metadata,{},released=lambda:True)
    assert list((layout.state/'core').iterdir())==[]
    assert not (layout.state/'agents/session.json').exists()
    assert (auth/'token').read_bytes()==b'external authorization'
    assert (keychain/'master.key').read_bytes()==b'private master key'
    assert (keychain/'bootstrap_token').read_bytes()==b'encrypted external token'
    assert not old.exists()
    assert (core/'platform.json').read_bytes()==b'private deployment configuration'
    assert metadata['initialized'] is False and metadata['officialPrepared'] is False
    clear_state(layout,metadata,{},released=lambda:True)


def test_prune_keeps_exact_current_set_units_and_release(owned,source):
    first,_=candidate(owned,source)
    activation.activate(owned,first,None)
    second,_=candidate(owned,source,python='3.11.10',staging='two')
    activation.activate(owned,second,first['setId'])
    backups=owned.state/'backups';backups.mkdir();(backups/'obsolete').write_text('old snapshot')
    with pytest.raises(RuntimeError):prune(owned,released=lambda:False)
    assert backups.exists()
    result=prune(owned,released=lambda:True)
    assert result['setId']==second['setId']
    assert activation.current(owned)==second
    assert not (owned.environments/'sets'/first['setId']).exists()
    assert not backups.exists()
    for unit in second['units'].values():assert Path(unit['environment']).exists()


@pytest.mark.parametrize('relative',['agently-auth','.local/share/agently-cli'])
def test_external_authorization_link_is_rejected_before_data_clear(owned,tmp_path,relative):
    (owned.state/'core').mkdir();(owned.state/'agents').mkdir()
    data=owned.state/'core/accounts';data.write_text('original accounts')
    external=tmp_path/'external-auth';external.mkdir()
    link=owned.state/'agents'/relative;link.parent.mkdir(parents=True,exist_ok=True)
    link.symlink_to(external,target_is_directory=True)
    with pytest.raises(ValueError,match='links'):clear_state(owned,{}, {},released=lambda:True)
    assert data.read_text()=='original accounts' and external.is_dir()


def test_resuming_clear_refuses_any_remaining_role_process(tmp_path,monkeypatch):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    (layout.state/'core').mkdir();data=layout.state/'core/accounts';data.write_text('still present')
    installer=SimpleNamespace(layout=layout,progress={'testReset':{'phase':'clearing','before':{'unit':{}}}},
        manager=SimpleNamespace(released=lambda *args:True),metadata={})
    monkeypatch.setattr('deploy.test_server.role_processes',lambda metadata:True)
    with pytest.raises(RuntimeError,match='writer reappeared'):Reset(installer).run()
    assert data.read_text()=='still present'


def test_reset_refuses_a_locked_original_sdk_before_deleting_any_data(setup):
    from deploy.files import writer
    flow,manager,_,lock,_,activations,_=setup
    progress={'testReset':{'phase':'quiescing','before':manager.freeze([p['unit'] for p in flow.plans.values()])}}
    installer=SimpleNamespace(layout=flow.layout,progress=progress,manager=manager,plans=flow.plans)
    original=(flow.layout.state/'core/db').read_bytes()
    with writer(lock):
        with pytest.raises(RuntimeError,match='writer lock'):Reset(installer).run()
    assert (flow.layout.state/'core/db').read_bytes()==original
    assert progress['testReset']['phase']=='quiescing' and not activations


def test_reset_refuses_a_locked_original_core_before_deleting_any_data(setup):
    from deploy.files import writer
    flow,manager,_,_,_,activations,_=setup
    core=flow.layout.state/'core/.core.lock';core.write_bytes(b'0')
    progress={'testReset':{'phase':'quiescing','before':manager.freeze([p['unit'] for p in flow.plans.values()])}}
    installer=SimpleNamespace(layout=flow.layout,progress=progress,manager=manager,plans=flow.plans)
    original=(flow.layout.state/'core/db').read_bytes()
    with writer(core):
        with pytest.raises(RuntimeError,match='writer lock'):Reset(installer).run()
    assert (flow.layout.state/'core/db').read_bytes()==original
    assert progress['testReset']['phase']=='quiescing' and not activations


def test_initialization_failure_keeps_new_data_and_resumes_same_stage(tmp_path):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');layout.claim()
    (layout.state/'core').mkdir();data=layout.state/'core/new-accounts';data.write_text('new partial initialization')
    progress={'testReset':{'phase':'initializing'}}
    def fail():raise RuntimeError('SDK response unknown')
    installer=SimpleNamespace(layout=layout,progress=progress,config=SimpleNamespace(base=lambda:None),
        permissions=lambda:None,bootstrap=fail,saved=lambda:None)
    with pytest.raises(RuntimeError,match='unknown'):Reset(installer).run()
    assert progress['testReset']['phase']=='initializing' and data.exists()
    installer.bootstrap=lambda:None
    Reset(installer).run()
    assert progress['testReset']['phase']=='completed' and data.exists()
