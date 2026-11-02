"""Native terminal boundaries preserve values and unknown effects without retries."""
import json
from types import SimpleNamespace

import pytest

from ioalab_sdk.runtime.files import read_arguments
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_access_products import bootstrap


@pytest.mark.parametrize('case',['syntax','encoding','missing','scalar'])
def test_argument_failure_diagnoses_the_actual_boundary_without_rewriting(tmp_path,case):
    config=tmp_path/'settings.json';config.write_text('{}',encoding='utf-8')
    path=tmp_path/'arguments.json';home=tmp_path/'sdk-state'
    contents={'syntax':b'{"text":"private-input-canary" "other":1}',
              'encoding':b'{"text":"\xff"}', 'scalar':b'[]'}
    if case in contents:path.write_bytes(contents[case])
    before=config.read_bytes()
    with pytest.raises(RuntimeFault) as error:read_arguments(path,configuration=config,home=home)
    value=error.value
    assert value.code=='invalid_input' and 'private-input-canary' not in str(value)
    if case=='syntax':assert 'syntax' in str(value) and 'line 1, column' in str(value)
    elif case=='encoding':assert 'must use UTF-8' in str(value)
    elif case=='missing':assert 'unavailable or unreadable' in str(value)
    else:assert 'JSON object' in str(value)
    assert config.read_bytes()==before and not home.exists()
    if case in contents:assert path.read_bytes()==contents[case]
    else:assert not path.exists()


def test_valid_utf8_arguments_are_preserved_without_normalization(tmp_path):
    config=tmp_path/'settings.json';config.write_text('{}',encoding='utf-8')
    path=tmp_path/'参数.json';original='{"text":"每时段最多两人与自助取消","number":2}'
    path.write_text(original,encoding='utf-8')
    value=read_arguments(path,configuration=config,home=tmp_path/'sdk-state')
    assert value=={'text':'每时段最多两人与自助取消','number':2}
    assert path.read_bytes()==original.encode('utf-8')


def test_real_sdk_json_values_survive_native_utf8_gbk_and_ascii_decoding(capsys):
    module=bootstrap()
    original={'status':'completed','data':{'features':'每时段最多两人，顾客可以取消预约',
        'path':'C:/用户/九问/参数.json','reference':{'kind':'job','id':'original-id'}}}
    result=SimpleNamespace(stdout=json.dumps(original,ensure_ascii=False),returncode=0)
    assert module.relay(result)==0
    raw=capsys.readouterr().out.encode('ascii')
    for encoding in ('ascii','utf-8','gbk'):
        assert json.loads(raw.decode(encoding))==original


@pytest.mark.parametrize('stdout',['','{"status":"completed",','[]'])
def test_missing_or_invalid_sdk_reply_preserves_unknown_and_does_not_invoke_again(capsys,monkeypatch,stdout):
    module=bootstrap()
    monkeypatch.setattr(module,'invoke',lambda *_:pytest.fail('No retry is authorized by an invalid result'))
    assert module.relay(SimpleNamespace(stdout=stdout,returncode=1))==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='unknown' and result['error']['code']=='unknown_result'
    assert result['error']['recovery']=='resume_original'
