"""First-write recovery and reinstall preservation for sensitive deployment configuration."""
import json

import pytest

from deploy.configs import Configuration, local_url
from deploy.configure import initialize
from ioalab.configuration import PlatformConfiguration
from deploy.layout import Layout, environment_text, read_environment


def fixture(tmp_path,**changes):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config')
    path=initialize(layout)
    document=json.loads(path.read_text());document['runtime']['public_url']='https://ioa.example'
    document['llm'].update(model='test-model',base_url='https://models.example/v1',api_key='model-private-sentinel')
    path.write_text(json.dumps(document),encoding='utf-8')
    identity=layout.inspect()
    value={'platform_config':str(path),**changes}
    return layout,identity,value


def workspace(layout):
    return {'status':'completed','data_dir':str(layout.state/'agents/swarm-admin'),
            'command':[str(layout.environments/'current/swarm/bin/jiuwenswarm-process')],'model':'OpenAI@native-selection'}


def test_reinstall_preserves_credentials_and_operator_edits(tmp_path):
    layout,identity,value=fixture(tmp_path)
    first=Configuration(layout,identity,value);original=first.base()
    path=first.official(workspace(layout));data=json.loads(path.read_text())
    data.update(max_model_runs=7);data['host']['model']='operator-selection';path.write_text(json.dumps(data))
    core_path=layout.config/'core/platform.json';core=json.loads(core_path.read_text());core['llm']['model']='operator-model'
    core_path.write_text(json.dumps(core),encoding='utf-8')
    authority=PlatformConfiguration(core_path);authority.update(1,{'credits':{'login':'7.00'}})
    second=Configuration(layout,identity,value);actual=second.base();second.official(workspace(layout))
    assert actual['runtime']['session_secret']==original['runtime']['session_secret']
    assert actual['runtime']['assistant_password']==original['runtime']['assistant_password']
    assert actual['llm']['model']=='operator-model' and actual['credits']['login']=='7.00'
    assert json.loads(path.read_text())==data
    assert actual['runtime']['seed_file']==str(layout.environments/'current/release/seed.json')
    assert not (layout.config/'core/core.env').exists()
    assert not any(secret in first.path.read_text() for secret in ('model-private-sentinel','operator-model','7.00'))



def test_changed_initial_input_rejected_without_printing_secrets(tmp_path):
    layout,identity,value=fixture(tmp_path)
    first=Configuration(layout,identity,value);first.base();before=first.path.read_bytes()
    changed={**value,'max_model_runs':7}
    with pytest.raises(ValueError,match='Initial deployment input') as caught:Configuration(layout,identity,changed)
    assert 'sentinel' not in str(caught.value) and first.path.read_bytes()==before


def test_native_assets_keep_content_identity_without_a_symlink(tmp_path):
    from deploy.windows_local import Local
    local=Local(tmp_path/'native');identity=local.layout.claim()
    assets=local.assets({'releaseId':'a'*64})
    path=initialize(local.layout,assets=assets)
    document=json.loads(path.read_text());document['llm'].update(model='test-model',api_key='model-private-sentinel')
    path.write_text(json.dumps(document),encoding='utf-8')
    result=Configuration(local.layout,identity,{'platform_config':str(path)},assets=assets).base()
    assert assets.name=='a'*64
    assert result['runtime']['seed_file']==str(assets/'seed.json')
    assert result['runtime']['bundles_dir']==str(assets/'skills')
    assert not assets.is_symlink()
    with pytest.raises(ValueError,match='content-addressed'):
        local.assets({'releaseId':'../other'})


def test_completed_missing_configuration_is_not_regenerated(tmp_path):
    layout,identity,value=fixture(tmp_path);configuration=Configuration(layout,identity,value);configuration.base()
    (layout.config/'agents/official.env').unlink()
    with pytest.raises(RuntimeError,match='missing original'):Configuration(layout,identity,value).base()
    assert not (layout.config/'agents/official.env').exists()


def test_initial_record_loss_does_not_generate_replacement_credentials(tmp_path):
    layout,identity,value=fixture(tmp_path);configuration=Configuration(layout,identity,value);configuration.base()
    configuration.path.unlink()
    with pytest.raises(RuntimeError,match='missing original'):Configuration(layout,identity,value)
    assert not configuration.path.exists()


def test_unknown_partial_file_is_preserved_until_explicit_review(tmp_path):
    layout,identity,value=fixture(tmp_path);configuration=Configuration(layout,identity,value)
    path=layout.config/'agents/official.env';path.parent.mkdir();path.write_text('OPERATOR="keep"\n')
    with pytest.raises(ValueError,match='preserve'):configuration.base()
    assert path.read_text()=='OPERATOR="keep"\n'



def test_mail_credentials_reach_only_assistant_environment(tmp_path):
    notice={'transport':'agentmail','sender':'ioa@agentmail.to','api_key':'mail-private-sentinel'}
    layout,identity,value=fixture(tmp_path,admin_assistant={'notification':notice})
    configuration=Configuration(layout,identity,value);configuration.base()
    recipe=configuration.administrator(workspace(layout));data=json.loads(recipe.read_text())
    assert data['notification']['api_key_env']=='IOALAB_NOTIFICATION_API_KEY'
    assert 'mail-private-sentinel' not in recipe.read_text()
    assert 'mail-private-sentinel' in (layout.config/'agents/assistant.env').read_text()
    for relative in ('core/platform.json','agents/official.env','agents/swarm-admin.env'):
        assert 'mail-private-sentinel' not in (layout.config/relative).read_text()
    projection=json.dumps(configuration.public())
    assert 'sentinel' not in projection and data['password'] not in projection
    assert data['web_url'].endswith('/ws?user_id=ioa-admin')
    assert read_environment(layout.config/'agents/swarm-admin.env')['JIUWENSWARM_CLI_PORTS']=='1'
    assert read_environment(layout.config/'agents/swarm-admin.env')['JIUWENSWARM_A2UI_ENABLED']=='true'


def test_disabled_administrator_creates_no_swarm_or_assistant_config(tmp_path):
    layout,identity,value=fixture(tmp_path,admin_assistant={'enabled':False})
    configuration=Configuration(layout,identity,value);configuration.base()
    assert configuration.administrator(workspace(layout)) is None
    assert not (layout.config/'agents/swarm-admin.env').exists()
    assert not (layout.config/'agents/assistant.env').exists()


def test_missing_model_fails_before_writing_input(tmp_path):
    layout,identity,value=fixture(tmp_path)
    path=layout.config/'core/platform.json';document=json.loads(path.read_text());document['llm']['api_key']=''
    path.write_text(json.dumps(document),encoding='utf-8')
    with pytest.raises(ValueError):Configuration(layout,identity,value)
    assert not (layout.private/'initial.json').exists()


def test_configure_does_not_replace_existing_platform_file_or_adopt_unknown_root(tmp_path):
    layout,identity,value=fixture(tmp_path)
    path=layout.config/'core/platform.json';before=path.read_bytes()
    with pytest.raises(ValueError,match='already exists'):initialize(layout)
    assert path.read_bytes()==before
    unknown=Layout(tmp_path/'unknown-runtime',tmp_path/'unknown-state',tmp_path/'unknown-config')
    unknown.config.mkdir();(unknown.config/'private-existing').write_text('preserve')
    with pytest.raises(ValueError,match='no ownership'):initialize(unknown)
    assert (unknown.config/'private-existing').read_text()=='preserve'


def test_installation_rejects_duplicate_platform_parameters_and_external_configuration(tmp_path):
    layout,identity,value=fixture(tmp_path)
    with pytest.raises(ValueError):Configuration(layout,identity,{**value,'admin_password':'do-not-use'})
    other,_,external=fixture(tmp_path/'external')
    with pytest.raises(ValueError,match='unique platform'):Configuration(layout,identity,external)
    assert not (layout.private/'initial.json').exists()


@pytest.mark.parametrize('bind,expected',[('0.0.0.0','http://127.0.0.1:8750'),('::','http://[::1]:8750'),('::1','http://[::1]:8750')])
def test_local_probe_address_is_independent_from_public_identity(bind,expected):
    assert local_url(bind,8750)==expected
