"""Private development profiles do not become a runtime configuration authority."""
from io import BytesIO
import json
from pathlib import Path

import pytest

from tools import llm


def source(tmp_path):
    path=tmp_path/'private-profile'
    value={'name':'test-model','base_url':'https://example.invalid/v1','api_key':'DUMMY-PRIVATE-KEY'}
    path.write_text(json.dumps({'profiles':{'test':value}}),encoding='utf-8')
    return path,value


def test_initialization_injects_only_selected_profile_into_existing_initializer(tmp_path):
    path,value=source(tmp_path);before=path.read_bytes();calls=[]
    def initializer(layout,*,llm):
        calls.append((layout,llm));return Path('instance/core/platform.json')
    result=llm.initialize('test','owned-layout',source=path,initializer=initializer)
    assert calls==[('owned-layout',{**value,'provider':'OpenAI'})]
    assert result==Path('instance/core/platform.json')
    assert path.read_bytes()==before
    assert list(tmp_path.iterdir())==[path]


@pytest.mark.parametrize('defect',['missing','invalid-json','profile','credential','url'])
def test_invalid_profile_fails_without_printing_secret_or_running_initializer(tmp_path,defect,capsys):
    path,value=source(tmp_path)
    if defect=='missing':path.unlink()
    elif defect=='invalid-json':path.write_text('DUMMY-PRIVATE-KEY')
    elif defect=='profile':path.write_text(json.dumps({'profiles':{}}))
    else:
        value['api_key' if defect=='credential' else 'base_url']='' if defect=='credential' else 'http://example.invalid'
        path.write_text(json.dumps({'profiles':{'test':value}}))
    def forbidden(*args,**kwargs):raise AssertionError('Initializer must not run')
    with pytest.raises(llm.ModelInputError) as error:
        llm.initialize('test',object(),source=path,initializer=forbidden)
    assert 'DUMMY-PRIVATE-KEY' not in str(error.value)+repr(error.value)+str(capsys.readouterr())


def test_original_initializer_failure_is_redacted_and_does_not_retry(tmp_path):
    path,_=source(tmp_path);calls=[]
    def fail(*args,**kwargs):
        calls.append(1);raise ValueError('DUMMY-PRIVATE-KEY')
    with pytest.raises(llm.ModelInputError,match='existing input preserved') as error:
        llm.initialize('test',object(),source=path,initializer=fail)
    assert calls==[1] and 'DUMMY-PRIVATE-KEY' not in str(error.value)


def test_explicit_completion_keeps_credential_out_of_body_and_environment(tmp_path,monkeypatch):
    path,_=source(tmp_path);requests=[]
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    def opener(request,*,timeout):
        requests.append(request)
        assert timeout==120
        assert request.headers['Authorization']=='Bearer DUMMY-PRIVATE-KEY'
        assert b'DUMMY-PRIVATE-KEY' not in request.data
        return BytesIO(b'{"choices": [{"message": {"content": "fixture"}}]}')
    assert llm.complete('test',[{'role':'user','content':'fixture'}],source=path,opener=opener)['choices']
    assert requests[0].full_url=='https://example.invalid/v1/chat/completions'
    import os
    assert 'OPENAI_API_KEY' not in os.environ


def test_provider_failure_is_redacted_without_automatic_retry(tmp_path):
    path,_=source(tmp_path);calls=[]
    def fail(*args,**kwargs):
        calls.append(1);raise RuntimeError('DUMMY-PRIVATE-KEY')
    with pytest.raises(llm.ModelInputError) as error:
        llm.complete('test',[],source=path,opener=fail)
    assert calls==[1] and 'DUMMY-PRIVATE-KEY' not in str(error.value)


def test_real_initializer_writes_one_platform_authority_and_refuses_replacement(tmp_path,capsys):
    from deploy.layout import Layout
    path,value=source(tmp_path)
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config')
    target=llm.initialize('test',layout,source=path)
    assert target==layout.config/'core/platform.json'
    document=json.loads(target.read_text(encoding='utf-8'))
    assert {key:document['llm'][key] for key in ('model','base_url','api_key')}=={
        'model':value['name'],'base_url':value['base_url'],'api_key':value['api_key']}
    before=target.read_bytes()
    value['api_key']='OTHER-DUMMY-PRIVATE-KEY'
    path.write_text(json.dumps({'profiles':{'test':value}}),encoding='utf-8')
    assert target.read_bytes()==before
    with pytest.raises(llm.ModelInputError) as error:llm.initialize('test',layout,source=path)
    assert target.read_bytes()==before
    assert 'DUMMY-PRIVATE-KEY' not in str(error.value)+str(capsys.readouterr())
    assert sorted(p.name for p in (layout.config/'core').iterdir())==['platform.json']


@pytest.mark.parametrize('failure',[None,'provider','close','empty'])
def test_narration_uses_unified_source_and_official_sdk_contract_without_global_key(tmp_path,monkeypatch,failure):
    import base64
    from types import SimpleNamespace
    import sys
    path,_=source(tmp_path);document=json.loads(path.read_text())
    document['tts']={'profile':'test','model':'test-tts','url':'wss://example.invalid/api-ws/v1/realtime'}
    path.write_text(json.dumps(document));calls=[]
    dashscope=SimpleNamespace(api_key='GLOBAL-SENTINEL')
    monkeypatch.setitem(sys.modules,'dashscope',dashscope)
    class SDK:
        def __init__(self,**kwargs):self.callback=kwargs['callback'];calls.append(kwargs)
        def connect(self):assert self.apikey=='DUMMY-PRIVATE-KEY'
        def update_session(self,**kwargs):calls.append(kwargs)
        def append_text(self,text):assert text=='approved narration'
        def finish(self):
            if failure=='provider':self.callback.on_event({'type':'error','message':'DUMMY-PRIVATE-KEY'})
            elif failure=='close':self.callback.on_close(1006,'DUMMY-PRIVATE-KEY')
            else:
                if failure!='empty':self.callback.on_event({'type':'response.audio.delta','delta':base64.b64encode(b'pcm-fixture').decode()})
                self.callback.on_event({'type':'session.finished'})
        def close(self):calls.append('closed')
    monkeypatch.setitem(sys.modules,'dashscope.audio.qwen_tts_realtime',SimpleNamespace(
        QwenTtsRealtime=SDK,QwenTtsRealtimeCallback=object,
        AudioFormat=SimpleNamespace(PCM_24000HZ_MONO_16BIT='pcm16')))
    client=llm.Aliyun(source=path,client_factory=SDK)
    if failure:
        with pytest.raises(llm.ModelInputError) as error:client.tts_realtime('approved narration',voice='test',instructions='clear',speech_rate=1.2)
        assert 'DUMMY-PRIVATE-KEY' not in str(error.value)
    else:assert client.tts_realtime('approved narration',voice='test',instructions='clear',speech_rate=1.2)==b'pcm-fixture'
    assert dashscope.api_key=='GLOBAL-SENTINEL' and calls[-1]=='closed'


def test_narration_consumer_uses_shared_adapter_instead_of_external_helper(monkeypatch,tmp_path):
    from tools.media import approved_voice
    sentinel=object();monkeypatch.setattr(llm,'Aliyun',lambda:sentinel)
    assert approved_voice.operator_client(tmp_path/'untrusted-external-helper.py') is sentinel
