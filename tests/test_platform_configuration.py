"""Real private configuration persistence, revision conflicts and failure atomicity."""
from copy import deepcopy
import json
import os

import pytest

from ioalab.config import Settings
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
    settings=Settings.load(configuration.path)
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
    settings._configuration.update(1,{'credits':{'login':'7.25','repeat_daily':True},'authentication':{'max_failures':9}})
    assert settings.policy()['credits']['login']=='7.25'
    assert settings.policy()['authentication']['max_failures']==9
    assert Settings.load(configuration.path).login_credit=='7.25'
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


def test_missing_and_hardlinked_private_configuration_are_rejected(configuration,tmp_path):
    with pytest.raises(FileNotFoundError):Settings.load(configuration.path.parent/'missing.json')
    alias=tmp_path/'configuration-alias.json';os.link(configuration.path,alias)
    try:
        with pytest.raises(LabError):Settings.load(configuration.path)
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
