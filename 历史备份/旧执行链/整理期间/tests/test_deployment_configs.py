"""First-write recovery and reinstall preservation for sensitive deployment configuration."""
import json

import pytest

from deploy.configs import Configuration, local_url
from deploy.layout import Layout, environment_text, read_environment


def fixture(tmp_path,**changes):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config')
    identity=layout.claim()
    value={'public_url':'https://ioa.example','model':{'name':'test-model','base_url':'https://models.example/v1','api_key':'model-private-sentinel'},**changes}
    return layout,identity,value


def workspace(layout):
    return {'status':'completed','data_dir':str(layout.state/'agents/swarm-admin'),
            'command':[str(layout.environments/'current/swarm/bin/jiuwenswarm-process')],'model':'OpenAI@native-selection'}


def test_reinstall_preserves_credentials_and_operator_edits(tmp_path):
    layout,identity,value=fixture(tmp_path)
    first=Configuration(layout,identity,value);original=first.base()
    path=first.official(workspace(layout));data=json.loads(path.read_text())
    data.update(max_model_runs=7);data['host']['model']='operator-selection';path.write_text(json.dumps(data))
    core_path=layout.config/'core/core.env';core=read_environment(core_path);core['IOALAB_LLM_MODEL']='operator-model'
    core_path.write_text(environment_text(core),encoding='utf-8')
    second=Configuration(layout,identity,value);actual=second.base();second.official(workspace(layout))
    assert first.record['credentials']==second.record['credentials']
    assert actual['IOALAB_SECRET']==original['IOALAB_SECRET']
    assert actual['IOALAB_ASSISTANT_PASSWORD']==original['IOALAB_ASSISTANT_PASSWORD']
    assert actual['IOALAB_LLM_MODEL']=='operator-model'
    assert json.loads(path.read_text())==data
    assert actual['IOALAB_SEED_FILE']==str(layout.environments/'current/release/seed.json')


def test_changed_initial_input_rejected_without_printing_secrets(tmp_path):
    layout,identity,value=fixture(tmp_path)
    first=Configuration(layout,identity,value);first.base();before=first.path.read_bytes()
    changed={**value,'model':{**value['model'],'api_key':'new-private-sentinel'}}
    with pytest.raises(ValueError,match='Initial deployment input') as caught:Configuration(layout,identity,changed)
    assert 'sentinel' not in str(caught.value) and first.path.read_bytes()==before


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
    path=layout.config/'core/core.env';path.parent.mkdir();path.write_text('OPERATOR="keep"\n')
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
    for relative in ('core/core.env','agents/official.env','agents/swarm-admin.env'):
        assert 'mail-private-sentinel' not in (layout.config/relative).read_text()
    projection=json.dumps(configuration.public())
    assert 'sentinel' not in projection and data['password'] not in projection
    assert data['web_url'].endswith('/ws?user_id=ioa-admin')
    assert read_environment(layout.config/'agents/swarm-admin.env')['JIUWENSWARM_CLI_PORTS']=='1'


def test_disabled_administrator_creates_no_swarm_or_assistant_config(tmp_path):
    layout,identity,value=fixture(tmp_path,admin_assistant={'enabled':False})
    configuration=Configuration(layout,identity,value);configuration.base()
    assert configuration.administrator(workspace(layout)) is None
    assert not (layout.config/'agents/swarm-admin.env').exists()
    assert not (layout.config/'agents/assistant.env').exists()


def test_missing_model_fails_before_writing_input(tmp_path):
    layout,identity,value=fixture(tmp_path);value['model']=None
    with pytest.raises(ValueError,match='complete real model'):Configuration(layout,identity,value)
    assert not (layout.private/'initial.json').exists()


@pytest.mark.parametrize('bind,expected',[('0.0.0.0','http://127.0.0.1:8750'),('::','http://[::1]:8750'),('::1','http://[::1]:8750')])
def test_local_probe_address_is_independent_from_public_identity(bind,expected):
    assert local_url(bind,8750)==expected

def test_reinstall_preserves_explicit_empty_seed_and_bundle_paths(tmp_path):
    layout,identity,value=fixture(tmp_path)
    configuration=Configuration(layout,identity,value);configuration.base()
    path=layout.config/'core/core.env';values=read_environment(path)
    values.update(IOALAB_SEED_FILE='',IOALAB_BUNDLES_DIR='')
    path.write_text(environment_text(values),encoding='utf-8')
    before=path.read_bytes()
    actual=Configuration(layout,identity,value).base()
    assert actual['IOALAB_SEED_FILE']==actual['IOALAB_BUNDLES_DIR']==''
    assert path.read_bytes()==before
