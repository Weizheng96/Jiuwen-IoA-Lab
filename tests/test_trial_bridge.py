"""Persistent model IPC arguments, identity fences and unknown-effect handling."""
import json
from pathlib import Path
import sys

import httpx
import pytest

from ioalab_trial import model_bridge as bridge
from ioalab_sdk.runtime.protocol import RuntimeFault


def test_argument_id_is_durable_before_first_send_and_reused(tmp_path,monkeypatch,capsys):
    path=tmp_path/'original.json';path.write_text('{"contractRef":"original-card"}',encoding='utf-8')
    actual=bridge.arguments
    monkeypatch.setattr(bridge,'arguments',lambda value:actual(value,root=tmp_path))
    original_read=Path.read_text
    def read(self,*args,**kwargs):
        if self.as_posix()=='/epoch/native/model-bridge.json':return '{"key":"private-bridge-key"}'
        return original_read(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    sent=[]
    def post(url,**kwargs):
        body=kwargs['json'];sent.append(body)
        assert kwargs['trust_env'] is False
        assert json.loads(path.read_text())['operationId']==body['operationId']
        assert body['arguments']=={'contractRef':'original-card'}
        return httpx.Response(200,json={'operationId':body['operationId'],'status':'completed','data':{'receipt':'original'}},
                              request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',post)
    monkeypatch.setattr(sys,'argv',['ioalab.py','assistant.delegation.pay','--args-file',str(path)])
    assert bridge.main()==0
    first=json.loads(capsys.readouterr().out)
    assert bridge.main()==0
    second=json.loads(capsys.readouterr().out)
    assert first==second and sent[0]['operationId']==sent[1]['operationId']
    assert 'private-bridge-key' not in json.dumps(first)


@pytest.mark.parametrize('kind',['secret','settings','outside','parent','invalid-json','array','invalid-id'])
def test_argument_file_boundaries_fail_without_rewrite(tmp_path,kind):
    root=tmp_path/'native';root.mkdir()
    path=root/'input.json';payload='{}'
    if kind=='secret':path=root/'model-bridge.json'
    elif kind=='settings':
        path=root/'.ioalab/settings.local.json';path.parent.mkdir()
    elif kind=='outside':path=tmp_path/'sdk-private.json'
    elif kind=='parent':
        path=root/'../sdk-private.json'
    elif kind=='invalid-json':payload='not JSON'
    elif kind=='array':payload='[]'
    elif kind=='invalid-id':payload='{"operationId":42}'
    path.write_text(payload,encoding='utf-8')
    with pytest.raises(ValueError):bridge.arguments(path,root=root)
    assert path.read_text('utf-8')==payload


def test_argument_symlink_cannot_rewrite_sdk_or_secret(tmp_path):
    root=tmp_path/'native';root.mkdir()
    actual=tmp_path/'sdk-private.json';actual.write_text('{"private":"unchanged"}')
    link=root/'arguments.json'
    try:link.symlink_to(actual)
    except OSError:pytest.skip('Host cannot create symlinks; Linux specialist acceptance still required')
    with pytest.raises(ValueError):bridge.arguments(link,root=root)
    assert actual.read_text()=='{"private":"unchanged"}'


@pytest.fixture
def sdk_boundary(monkeypatch):
    import ioalab_sdk.runtime.ipc as ipc
    import ioalab_sdk.runtime.config as config
    import ioalab_sdk.runtime.__main__ as commands
    import ioalab_sdk.runtime.installation as installation
    calls=[]
    state={'bootId':'original-boot','installation':{'assembly':'ioalab-jiuwen','releaseId':'original-release'}}
    class Client:
        def __init__(self,path,*,timeout,boot_id=None):
            calls.append(('client',path,boot_id));self.boot=boot_id
        def call(self,operation,arguments=None,*,operation_id=None):
            calls.append(('call',operation,arguments,operation_id,self.boot))
            if operation=='runtime.status':return {'status':'completed','data':state}
            return {'status':'completed','operationId':operation_id,'data':{'original':True}}
    monkeypatch.setattr(ipc,'LocalClient',Client)
    selected=object()
    monkeypatch.setattr(config.RuntimeConfig,'load',staticmethod(lambda path:selected))
    monkeypatch.setattr(commands,'require_configuration',lambda data,current,**kwargs:calls.append(('configuration',data,current,kwargs)))
    monkeypatch.setattr(installation,'require_matching',lambda value,**kwargs:calls.append(('installation',value,kwargs)))
    return calls,state,selected


def request(operation='assistant.delegation.pay'):
    return {'operation':operation,'arguments':{'contractRef':'original-card'},'operationId':'persistent-original'}


def test_forward_fences_configuration_installation_and_original_boot(sdk_boundary):
    calls,state,selected=sdk_boundary
    result=bridge.forward(request())
    assert result['operationId']=='persistent-original'
    config=next(item for item in calls if item[0]=='configuration')
    installed=next(item for item in calls if item[0]=='installation')
    assert config[1] is state and config[2] is selected and config[3]=={'assembly':'ioalab-jiuwen'}
    assert installed[1] is state['installation'] and installed[2]=={'assembly':'ioalab-jiuwen'}
    effect=next(item for item in calls if item[:2]==('call','assistant.delegation.pay'))
    assert effect[2]=={'contractRef':'original-card'} and effect[3]=='persistent-original' and effect[4]=='original-boot'
    assert calls.index(config)<calls.index(effect) and calls.index(installed)<calls.index(effect)


@pytest.mark.parametrize('operation',['account.login','runtime.configure','runtime.open_ui','confirmations.decide',
                                      'provider.publish','assistant.delegation.pay.evil'])
def test_finite_bridge_rejects_privileged_or_new_operations(sdk_boundary,operation):
    calls,_,_=sdk_boundary
    with pytest.raises(ValueError):bridge.forward(request(operation))
    assert calls==[]


@pytest.mark.parametrize('boundary',['configuration','installation'])
def test_mismatched_runtime_never_sends_effect(sdk_boundary,monkeypatch,boundary):
    calls,_,_=sdk_boundary
    def reject(*args,**kwargs):raise RuntimeFault('instance_changed','Keep original identity')
    target=('ioalab_sdk.runtime.__main__.require_configuration' if boundary=='configuration'
            else 'ioalab_sdk.runtime.installation.require_matching')
    monkeypatch.setattr(target,reject)
    with pytest.raises(RuntimeFault,match='original identity'):bridge.forward(request())
    assert not any(item[:2]==('call','assistant.delegation.pay') for item in calls)


@pytest.mark.parametrize('failure',['timeout','lost-response','server-error','invalid-response'])
def test_bridge_lost_reply_is_unknown_with_original_id(tmp_path,monkeypatch,capsys,failure):
    path=tmp_path/'original.json';path.write_text('{"operationId":"keep-me","contractRef":"original"}')
    actual=bridge.arguments
    monkeypatch.setattr(bridge,'arguments',lambda value:actual(value,root=tmp_path))
    original_read=Path.read_text
    def read(self,*args,**kwargs):
        if self.as_posix()=='/epoch/native/model-bridge.json':return '{"key":"secret"}'
        return original_read(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    def post(url,**kwargs):
        assert kwargs['json']['operationId']=='keep-me'
        if failure=='timeout':raise httpx.ReadTimeout('private transport detail')
        if failure=='lost-response':raise httpx.RemoteProtocolError('private transport detail')
        if failure=='server-error':return httpx.Response(503,request=httpx.Request('POST',url))
        return httpx.Response(200,content=b'not-json',request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',post)
    monkeypatch.setattr(sys,'argv',['ioalab.py','assistant.delegation.pay','--args-file',str(path)])
    assert bridge.main()==1
    raw=capsys.readouterr().out
    result=json.loads(raw)
    assert result['status']=='unknown' and result['operationId']=='keep-me'
    assert result['error']['code']=='unknown_result' and result['error']['recovery']=='operations.get'
    assert json.loads(path.read_text())['operationId']=='keep-me'
    assert 'private transport detail' not in raw
