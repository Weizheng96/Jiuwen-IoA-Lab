"""Real private configuration persistence, revision conflicts and failure atomicity."""
from copy import deepcopy
import json
import os

import pytest

from ioalab.configuration import PlatformConfiguration, defaults,validate
from ioalab.configuration_permissions import protect_new
from ioalab.errors import LabError


@pytest.fixture
def configuration(tmp_path):
    home=tmp_path/'platform-config';home.mkdir();protect_new(home,directory=True)
    document=defaults();document['runtime']['public_url']='https://platform.example.test'
    document['llm']['api_key']='fixture-only-model-secret'
    return PlatformConfiguration.create(home/'platform.json',document)


def test_single_file_roundtrip_dynamic_snapshot_redaction_and_revision_conflict(configuration,monkeypatch):
    authority=PlatformConfiguration.load(configuration.path);settings=authority.settings()
    monkeypatch.setenv('IOALAB_LOGIN_CREDIT','999')
    assert settings.login_credit=='100.00'
    assert settings.initial_credit=='500.00'
    assert settings.admin_username=='admin' and settings.admin_password=='admin1234'
    assert settings.public_url=='https://platform.example.test'
    assert settings.auth_max_failures==40 and settings.auth_window_seconds==60
    assert 'fixture-only-model-secret' not in json.dumps(configuration.view())
    assert 'admin1234' not in json.dumps(configuration.view())
    assert configuration.view()['restart']['llmKeyConfigured'] is True
    stale=PlatformConfiguration(configuration.path)
    authority.update(1,{'credits':{'login':'7.25','repeat_daily':True},'authentication':{'max_failures':9}})
    assert settings.policy()['credits']['login']=='7.25'
    assert settings.policy()['authentication']['max_failures']==9
    assert PlatformConfiguration.load(configuration.path).settings().login_credit=='7.25'
    before=configuration.path.read_bytes()
    with pytest.raises(LabError):stale.update(1,{'credits':{'initial':'999.00'}})
    assert configuration.path.read_bytes()==before


def test_illegal_dynamic_fields_and_invalid_values_do_not_publish(configuration):
    before=configuration.path.read_bytes();view=configuration.view()
    for changes in ({'admin':{'password':'changed'}},{'credits':{'login':'NaN'}},
                    {'credits':{'initial':'-1.00'}},{'credits':{'login':'1.001'}},
                    {'authentication':{'max_failures':False}},{'authentication':{'window_seconds':0}},
                    {'runtime':{'public_url':'http://changed.example'}},{'credits':{'repeat_daily':'false'}}):
        with pytest.raises(LabError):configuration.update(1,changes)
        assert configuration.path.read_bytes()==before and configuration.view()==view


def test_failed_atomic_replace_preserves_disk_and_active_policy(configuration,monkeypatch):
    before=configuration.path.read_bytes();policy=configuration.policy()
    def failure(*args):raise OSError('simulated unavailable filesystem')
    monkeypatch.setattr('ioalab.configuration.os.replace',failure)
    with pytest.raises(LabError) as failed:configuration.update(1,{'credits':{'login':'3.00'}})
    assert failed.value.code=='configuration_write_failed'
    assert configuration.path.read_bytes()==before and configuration.policy()==policy
    assert not list(configuration.path.parent.glob('.platform-*'))


def test_external_same_revision_edit_is_not_overwritten(configuration):
    altered=json.loads(configuration.path.read_text('utf-8'))
    altered['credits']['initial']='123.00'
    configuration.path.write_text(json.dumps(altered),encoding='utf-8')
    with pytest.raises(LabError):configuration.update(1,{'credits':{'login':'8.00'}})
    assert json.loads(configuration.path.read_text('utf-8'))['credits']['initial']=='123.00'


def network_configuration(tmp_path,*,domain=False,empty=False):
    folder=tmp_path/'network-config';folder.mkdir();protect_new(folder,directory=True)
    document=defaults();runtime=document['runtime'];runtime['public_url']='https://formal.example' if domain else 'https://192.0.2.10'
    runtime.update(data=str(tmp_path/'network-state'),mode='dev',stub=True,autoseed=False)
    runtime['trial_admission_key']='PRIVATE-ADMISSION-KEY-SENTINEL'*2
    if not empty:
        runtime['trial_url']='https://native.example:8443' if domain else 'https://192.0.2.10:8443'
        runtime['trial_confirm_url']='https://confirm.example:9443' if domain else 'https://192.0.2.10:9443'
    return PlatformConfiguration.create(folder/'platform.json',document)


def test_trial_ports_are_saved_without_changing_active_audience_until_restart(tmp_path):
    from ioalab.identity.trial import entry
    authority=network_configuration(tmp_path);settings=authority.settings()
    changes={'runtime':{'trial_url':'https://192.0.2.10:8000','trial_confirm_url':'https://192.0.2.10:8001'}}
    result=authority.update(1,changes)
    assert result['network']['pending'] and result['network']['publicReachability']=='unverified'
    assert result['network']['active']['trial_url']=='https://192.0.2.10:8443'
    assert result['network']['saved']['trial_url']=='https://192.0.2.10:8000'
    assert entry(settings,'owner')['url'].startswith('https://192.0.2.10:8443/entry#')
    assert entry(settings,'owner',confirmation=True)['url'].startswith('https://192.0.2.10:9443/entry#')
    authority.settings()
    assert authority.view()['network']['active']==result['network']['active']
    reloaded=PlatformConfiguration.load(authority.path);restarted=reloaded.settings()
    assert entry(restarted,'owner')['url'].startswith('https://192.0.2.10:8000/entry#')
    assert not reloaded.view()['network']['pending']
    assert restarted.port==settings.port and restarted.public_url==settings.public_url
    assert 'PRIVATE-ADMISSION' not in json.dumps(result)
    with pytest.raises(LabError) as error:authority.update(1,changes)
    assert error.value.status==409


@pytest.mark.parametrize('bad',[{'port':9000},{'bind':'0.0.0.0'},{'public_url':'https://changed.example'},
    {'trial_admission_key':'changed'},{'trial_url':'https://foreign.example:8000'},
    {'trial_url':'http://192.0.2.10:8000'},{'trial_url':'https://192.0.2.10:0'},
    {'trial_url':'https://192.0.2.10:70000'},{'trial_url':False},
    {'trial_url':'https://192.0.2.10'},{'trial_url':'https://192.0.2.10:9443'}])
def test_trial_port_editor_rejects_other_startup_fields_and_unsafe_origins(tmp_path,bad):
    authority=network_configuration(tmp_path);authority.settings();before=authority.path.read_bytes();view=authority.view()
    with pytest.raises(LabError):authority.update(1,{'runtime':bad})
    assert authority.path.read_bytes()==before and authority.view()==view


@pytest.mark.parametrize('domain',[False,True])
def test_empty_trial_hosts_can_only_initialize_from_formal_https_ip(tmp_path,domain):
    authority=network_configuration(tmp_path,domain=domain,empty=True);authority.settings()
    host='formal.example' if domain else '192.0.2.10'
    changes={'runtime':{'trial_url':f'https://{host}:8000','trial_confirm_url':f'https://{host}:8001'}}
    if domain:
        with pytest.raises(LabError,match='hostname'):authority.update(1,changes)
    else:
        result=authority.update(1,changes)
        assert result['network']['pending'] and result['network']['active']['trial_url']==''


def test_saving_ports_does_not_claim_missing_admission_secret_is_configured(tmp_path):
    from ioalab.identity.trial import entry
    authority=network_configuration(tmp_path,empty=True)
    document=json.loads(authority.path.read_text());document['runtime']['trial_admission_key']=''
    authority.path.write_text(json.dumps(document),encoding='utf-8');authority=PlatformConfiguration(authority.path)
    authority.settings()
    result=authority.update(1,{'runtime':{'trial_url':'https://192.0.2.10:8000','trial_confirm_url':'https://192.0.2.10:8001'}})
    assert result['network']['admissionKeyConfigured'] is False
    with pytest.raises(LabError) as error:entry(PlatformConfiguration.load(authority.path).settings(),'owner')
    assert error.value.code=='not_ready'


def test_missing_and_hardlinked_private_configuration_are_rejected(configuration,tmp_path):
    with pytest.raises(FileNotFoundError):PlatformConfiguration.load(configuration.path.parent/'missing.json')
    alias=tmp_path/'configuration-alias.json';os.link(configuration.path,alias)
    try:
        with pytest.raises(LabError):PlatformConfiguration.load(configuration.path)
    finally:alias.unlink()


def test_cli_check_uses_only_explicit_private_file_and_rejects_missing_locator(configuration,monkeypatch,capsys):
    from ioalab.cli import main
    document=json.loads(configuration.path.read_text());document['runtime']['autoseed']=False
    configuration.path.write_text(json.dumps(document),encoding='utf-8')
    monkeypatch.delenv('IOALAB_CONFIG',raising=False)
    monkeypatch.setenv('IOALAB_ADMIN_PASSWORD','unrelated-old-password')
    with pytest.raises(ValueError,match='Select the private'):main(['check-config'])
    with pytest.raises(SystemExit) as result:main(['--config',str(configuration.path),'check-config'])
    assert result.value.code==0
    output=capsys.readouterr().out
    assert 'fixture-only-model-secret' not in output and 'unrelated-old-password' not in output


def test_restart_configuration_normalizes_public_identity_and_rejects_bad_urls_or_durations():
    document=defaults();document['runtime']['public_url']='https://platform.example/'
    assert validate(document)['runtime']['public_url']=='https://platform.example'
    for group,key,value in [('llm','base_url','https://models.example:invalid/v1'),
            ('runtime','public_url','https://platform.example:invalid'),('runtime','ws_lease_seconds',1),
            ('runtime','message_rate',0),('runtime','message_ttl_days',1000)]:
        invalid=deepcopy(document);invalid[group][key]=value
        with pytest.raises(LabError):validate(invalid)


def test_admin_email_saved_revision_restart_and_explicit_old_config_upgrade(tmp_path):
    from ioalab.configuration import upgrade_admin_email
    authority = network_configuration(tmp_path, empty=True)
    authority.settings()
    saved = authority.update(1, {'runtime':{'admin_email':'new-owner@example.test'}})
    assert saved['administratorAssistant']['savedEmail'] == 'new-owner@example.test'
    assert saved['administratorAssistant']['startupEmail'] == 'zhengw.cc@outlook.com'
    assert saved['administratorAssistant']['pending'] is True
    with pytest.raises(LabError): authority.update(2, {'runtime':{'admin_email':'bad\r\n@example.test'}})
    with pytest.raises(LabError): authority.update(1, {'runtime':{'admin_email':'other@example.test'}})
    restarted = PlatformConfiguration.load(authority.path)
    assert restarted.settings().admin_email == 'new-owner@example.test'
    assert not restarted.view()['administratorAssistant']['pending']
    legacy = json.loads(authority.path.read_text(encoding='utf-8'))
    del legacy['runtime']['admin_email']
    authority.path.write_text(json.dumps(legacy), encoding='utf-8')
    with pytest.raises(LabError): PlatformConfiguration.load(authority.path)
    upgrade_admin_email(authority.path, 'migrated@example.test')
    assert PlatformConfiguration.load(authority.path).settings().admin_email == 'migrated@example.test'
    before = authority.path.read_bytes()
    upgrade_admin_email(authority.path, 'do-not-overwrite@example.test')
    assert authority.path.read_bytes() == before
