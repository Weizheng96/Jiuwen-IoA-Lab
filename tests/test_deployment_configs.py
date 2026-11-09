"""First-write recovery and reinstall preservation for sensitive deployment configuration."""
import json
import os
from pathlib import Path

import pytest

from deploy.configs import Configuration, local_url
from deploy.configure import initialize
from ioalab.configuration import PlatformConfiguration
from deploy.layout import Layout, environment_text, read_environment


def fixture(tmp_path,**changes):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config')
    path=initialize(layout)
    if os.name=='nt':
        # Deployment is Linux-only; on a Windows development machine the test
        # applies the platform's own private-file protection that chmod cannot express.
        from ioalab.configuration_permissions import protect_new
        protect_new(path.parent,directory=True);protect_new(path)
    document=json.loads(path.read_text());document['runtime']['public_url']='https://ioa.example'
    document['llm'].update(model='test-model',base_url='https://models.example/v1',api_key='model-private-sentinel')
    path.write_text(json.dumps(document),encoding='utf-8')
    identity=layout.inspect()
    value={'platform_config':str(path),**changes}
    return layout,identity,value


def workspace(layout):
    return {'status':'completed','data_dir':str(layout.state/'agents/swarm-admin'),
            'command':[str(layout.environments/'current/swarm/bin/jiuwenswarm-process')],'model':'OpenAI@native-selection'}


@pytest.mark.parametrize('change',['matching','trailing-slash','empty-native','wrong-key','wrong-port','formal-conflict',
                                  'native-default-port','confirm-default-port','native-host-case','confirm-host-case'])
def test_trial_pair_is_checked_before_installation_effects(tmp_path,monkeypatch,change):
    from deploy import configuration,units
    layout,_,value=fixture(tmp_path)
    path=Path(value['platform_config'])
    core=json.loads(path.read_text());core['runtime'].update(public_url='https://192.0.2.10',
        trial_url='https://192.0.2.10:8443',trial_confirm_url='https://192.0.2.10:9443',trial_admission_key='PRIVATE-KEY-'*4)
    gateway={'origin':'https://192.0.2.10:8443','confirm_origin':'https://192.0.2.10:9443',
             'formal_origin':'https://192.0.2.10:443','admission_key':'PRIVATE-KEY-'*4}
    if change=='empty-native':core['runtime']['trial_url']=''
    if change=='wrong-key':gateway['admission_key']='DIFFERENT-PRIVATE-KEY-'*3
    if change=='wrong-port':gateway['confirm_origin']='https://192.0.2.10:9444'
    if change=='formal-conflict':core['runtime']['trial_confirm_url']='https://192.0.2.10:443'
    if change=='trailing-slash':
        core['runtime']['trial_url']+='/'
        gateway['confirm_origin']+='/'
    if change in ('native-default-port','confirm-default-port','native-host-case','confirm-host-case'):
        core['runtime'].update(trial_url='https://trial.test',trial_confirm_url='https://confirm.test')
        gateway.update(origin='https://trial.test',confirm_origin='https://confirm.test')
        if change=='native-default-port':core['runtime']['trial_url']='https://trial.test:443'
        if change=='confirm-default-port':gateway['confirm_origin']='https://confirm.test:443'
        if change=='native-host-case':core['runtime']['trial_url']='https://TRIAL.test'
        if change=='confirm-host-case':gateway['confirm_origin']='https://CONFIRM.test'
    path.write_text(json.dumps(core),encoding='utf-8')
    trial=tmp_path/'trial.json';trial.write_text(json.dumps(gateway),encoding='utf-8')
    binding={'config':str(trial),'controller_python':'not-executed'}
    monkeypatch.setattr(units,'trial_binding',lambda selected:binding)
    if change in ('matching','trailing-slash'):
        result=configuration.validate({**value,'trial':binding})
        assert result['trial']==binding and 'admission_key' not in json.dumps(result)
    else:
        with pytest.raises(ValueError) as error:configuration.validate({**value,'trial':binding})
        assert 'must be complete and match' in str(error.value) and 'PRIVATE-KEY' not in str(error.value)


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


def test_outlook_oauth_notification_reaches_recipe_without_any_token(tmp_path):
    notice={'transport':'smtp','sender':'owner@outlook.com','host':'smtp-mail.outlook.com','port':587,'client_id':'public-app-id'}
    layout,identity,value=fixture(tmp_path,admin_assistant={'notification':notice})
    configuration=Configuration(layout,identity,value);configuration.base()
    recipe=configuration.administrator(workspace(layout));data=json.loads(recipe.read_text())
    token=layout.state/'agents/admin-assistant/outlook-smtp-token.json'
    assert data['notification']=={**notice,'token_file':str(token)}
    assert not token.exists() and configuration.public()['meetingConfigured'] is True
    assert 'IOALAB_NOTIFICATION' not in (layout.config/'agents/assistant.env').read_text()
    # The calendar factory assembles from the recipe unchanged; the missing token only
    # makes meeting invitations not_ready until the owner's login, without reinstalling.
    from ioalab_sdk.runtime.protocol import RuntimeFault
    from ioalab_tools.appointments import configure
    from ioalab_tools.schedule import LocalSchedule
    schedule=tmp_path/'schedule/schedule.json';schedule.parent.mkdir();LocalSchedule(schedule).init()
    token.parent.mkdir(parents=True)
    options={'calendar':{'backend':'local','schedule_file':str(schedule)},'notification':data['notification'],'recipient':data['recipient']}
    bundle=configure(options,home=tmp_path,profile='admin',base_dir=tmp_path)
    try:
        assert bundle.status()=={'state':'not_ready','ready':False,'code':'not_ready','recovery':'smtp_login',
                                 'message':'Run ioalab-outlook smtp-login for the configured notification sender'}
        book=next(binding for binding in bundle.bindings if binding.name=='calendar.book')
        context={'operationId':'op-1','agentId':'agent','messageId':'m','interactionId':'i'}
        args={'title':'评审','start':'2030-01-02T03:00:00Z','end':'2030-01-02T03:30:00Z','timeZone':'Asia/Shanghai'}
        with pytest.raises(RuntimeFault,match='smtp-login') as caught:book.invoke(args,context)
        assert caught.value.code=='not_ready' and not (schedule.parent/'mailbox.sqlite').exists()
        token.write_text('{"fixture":"owner login result"}',encoding='utf-8')
        assert bundle.status()=={'state':'ready','ready':True}
    finally:
        bundle.close()


@pytest.mark.parametrize('notice,message',[
    ({'transport':'smtp','sender':'a@outlook.com','host':'smtp-mail.outlook.com','client_id':'app','password':'x','username':'a'},'mutually exclusive'),
    ({'transport':'smtp','sender':'a@example.test','host':'smtp.example.test','client_id':'app'},'Microsoft SMTP endpoints'),
    ({'transport':'smtp','sender':'a@outlook.com','host':'smtp.office365.com','username':'a','password':'x'},'requires notification.client_id'),
    ({'transport':'agentmail','sender':'a@agentmail.to','api_key':'k','client_id':'app'},'explicit AgentMail or SMTP'),
    ({'transport':'smtp-capture','sender':'a@example.test','host':'127.0.0.1','client_id':'app'},'explicit AgentMail or SMTP')])
def test_outlook_oauth_notification_rejections(notice,message):
    from deploy.configuration import notification
    with pytest.raises(ValueError,match=message):notification(notice,mode='dev')


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
