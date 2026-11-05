"""Deployment ownership and literal configuration protect data before privileged effects."""
import json
from pathlib import Path
import uuid

import pytest

from deploy.configuration import validate
from deploy.layout import Layout, MARKER, environment_text, read_environment, write_initial


def layout(base):
    return Layout(base/'runtime',base/'state',base/'configuration')


def test_claim_reuses_one_identity_and_does_not_rewrite_operator_data(tmp_path):
    selected=layout(tmp_path);first=selected.claim()
    sentinel=selected.state/'operator.txt';sentinel.write_text('keep original')
    assert selected.claim()==first==selected.inspect()
    assert sentinel.read_text()=='keep original'
    assert all(json.loads((p/MARKER).read_text())==first for p in (selected.root,selected.state,selected.config))


def test_unknown_nonempty_directory_is_never_adopted(tmp_path):
    selected=layout(tmp_path);selected.state.mkdir();sentinel=selected.state/'old.sqlite';sentinel.write_bytes(b'old database')
    with pytest.raises(ValueError,match='no ownership'):
        selected.claim()
    assert sentinel.read_bytes()==b'old database'
    assert not selected.root.exists() and not (selected.state/MARKER).exists()


def test_partial_claim_continues_same_identity(tmp_path):
    selected=layout(tmp_path);selected.root.mkdir()
    identity={**selected.identity,'deploymentId':uuid.uuid4().hex}
    (selected.root/MARKER).write_text(json.dumps(identity))
    assert selected.claim()==identity
    assert selected.inspect()==identity


def test_different_claim_identity_is_rejected_without_overwrite(tmp_path):
    selected=layout(tmp_path);selected.claim()
    marker=selected.config/MARKER;before=marker.read_bytes()
    changed=Layout(selected.root,selected.state,tmp_path/'other-config')
    with pytest.raises(ValueError,match='ownership differs'):
        changed.claim()
    assert marker.read_bytes()==before and not changed.config.exists()


@pytest.mark.parametrize('case',['same_roots','nested_roots','same_users','privileged_user','invalid_user'])
def test_invalid_roots_and_accounts_fail_before_creation(tmp_path,case):
    args={'root':tmp_path/'runtime','state':tmp_path/'state','config':tmp_path/'conf'}
    if case=='same_roots':args['state']=args['root']
    elif case=='nested_roots':args['state']=args['root']/'data'
    elif case=='same_users':args.update(core_user='ioalab',agents_user='ioalab')
    elif case=='privileged_user':args['core_user']='root'
    else:args['core_user']='name;command'
    with pytest.raises(ValueError):Layout(**args)
    assert not list(tmp_path.iterdir())


def test_literal_environment_roundtrip_and_existing_file_preservation(tmp_path):
    values={'IOALAB_ADMIN_PASSWORD':'literal$(touch x)`cmd`"quote"\\slash', 'IOALAB_PUBLIC_URL':'https://测试.example', 'IOALAB_PORT':'8750','IOALAB_SEED_FILE':'','IOALAB_BUNDLES_DIR':'',
            'OWNER_WINDOWS_PATH':r'D:\demo\assistant','OWNER_JSON':'["a","b"]'}
    path=tmp_path/'core.env'
    content=environment_text(values).encode('utf-8');write_initial(path,content)
    assert read_environment(path)==values
    write_initial(path,content)
    with pytest.raises(ValueError,match='preserve and review'):
        write_initial(path,b'replacement')
    assert path.read_bytes()==content and not (tmp_path/'x').exists()


@pytest.mark.parametrize('data',['VALUE="one"\nVALUE="two"\n','VALUE=$(command)\n','export VALUE="one"\n','VALUE="escaped\\nline"\n'])
def test_environment_data_has_no_shell_or_duplicate_value_fallback(tmp_path,data):
    path=tmp_path/'bad.env';path.write_text(data)
    with pytest.raises(ValueError):read_environment(path)


def test_notification_defaults_and_secret_validation():
    config=validate({'public_url':'https://ioa.example','model':None})
    assert config['admin_assistant']=={'enabled':True,'recipient':'zhengw.cc@outlook.com','notification':None}
    complete=validate({'public_url':'https://ioa.example','admin_assistant':{'notification':{'transport':'agentmail','sender':'ioa@agentmail.to','api_key':'private-sentinel'}}})
    assert complete['admin_assistant']['notification']['api_key']=='private-sentinel'
    with pytest.raises(ValueError) as error:
        validate({'public_url':'https://ioa.example','admin_assistant':{'notification':{'transport':'agentmail','sender':'ioa@agentmail.to','api_key':'private-sentinel','arbitrary':True}}})
    assert 'private-sentinel' not in str(error.value)


def test_ports_and_capture_are_explicit_and_do_not_redirect_credentials():
    with pytest.raises(ValueError,match='distinct'):
        validate({'public_url':'https://ioa.example','swarm_ports':{'frontend':8750}})
    capture={'transport':'smtp-capture','sender':'ioa@example.com','host':'127.0.0.1','port':1025}
    with pytest.raises(ValueError,match='development'):
        validate({'public_url':'https://ioa.example','admin_assistant':{'notification':capture}})
    assert validate({'public_url':'https://ioa.example','mode':'dev','admin_assistant':{'notification':capture}})['admin_assistant']['notification']['port']==1025
    with pytest.raises(ValueError,match='loopback'):
        validate({'public_url':'https://ioa.example','mode':'dev','admin_assistant':{'notification':{**capture,'host':'external.example'}}})


@pytest.mark.parametrize('separator',['\u0085','\u2028','\u2029'])
def test_literal_unicode_separators_roundtrip_as_data_not_physical_lines(tmp_path,separator):
    values={'MODEL_LABEL':'中文'+separator+'label','OWNER_WINDOWS_PATH':r'D:\demo\assistant'}
    path=tmp_path/'literal.env';write_initial(path,environment_text(values).encode('utf-8'))
    assert read_environment(path)==values
    assert path.read_bytes().count(b'\n')==3  # header and two physical records


@pytest.mark.parametrize('control',['\n','\r','\t'])
def test_literal_environment_control_characters_are_rejected_by_both_sides(tmp_path,control):
    with pytest.raises(ValueError):environment_text({'VALUE':'a'+control+'b'})
    path=tmp_path/'bad-control.env';path.write_text('VALUE='+json.dumps('a'+control+'b')+'\n',encoding='utf-8')
    with pytest.raises(ValueError):read_environment(path)
