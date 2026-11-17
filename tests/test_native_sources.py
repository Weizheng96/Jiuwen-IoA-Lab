"""SDK private source ingress and P10 lookup; no native process or model."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest

from ioalab_sdk.runtime import HostRegistry,HostRegistration,RuntimeStore,RuntimeFault,encode,native_source,source_scope,source_value
from ioalab_sdk.runtime.native_sources import NativeSources
from ioalab_sdk.runtime.ipc import IpcServer
from ioalab_sdk.runtime.files import read_json


@contextmanager
def ingress(tmp_path):
    class Adapter:registration=HostRegistration('fixture','1')
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    registry=HostRegistry({'host':Adapter()},'host');registry.bind(store)
    identities=[SimpleNamespace(profile='assistant',agent_id='actor')]
    service=NativeSources(store,registry,identities,{'releaseId':'fixture'},'account-binding',tmp_path)
    server=IpcServer(tmp_path,store.runtime_id,lambda *args,**kwargs:None,native_sources=service)
    service.attach(server.descriptor);issuer=service.provision('host','assistant')
    name=hashlib.sha256(encode(['host','assistant']).encode()).hexdigest()+'.json'
    descriptor=read_json(tmp_path/'roles'/'.native-sources'/name)
    server.start()
    try:yield service,server,descriptor,issuer
    finally:server.close();store.close()


def test_ordinary_rpc_token_cannot_issue_native_source(tmp_path):
    with ingress(tmp_path) as (service,server,descriptor,_):
        body={'sessionId':'actual-session','workspace':str(tmp_path),'captureId':'native-call'}
        headers={'Authorization':'Bearer '+server.token,'X-Ioa-Instance':server.boot_id}
        denied=httpx.post(descriptor['url'],json=body,headers=headers,trust_env=False)
        assert denied.status_code==401 and denied.json()['error']['code']=='authenticate'
        headers['Authorization']='Bearer '+descriptor['grant']
        issued=httpx.post(descriptor['url'],json=body,headers=headers,trust_env=False)
        assert issued.status_code==200
        context=service.lookup(issued.json()['data'])
        assert (context.host_ref,context.session_id,context.actor)==('host','actual-session','actor')
        # A source capability also does not grant ordinary business RPC authority.
        denied=httpx.post(server.descriptor['url']+'/v1/call',json={'operation':'source.issue','operationId':'op','arguments':body},headers=headers,trust_env=False)
        assert denied.status_code==401


def test_native_capture_is_bound_and_actor_cannot_come_from_business_body(tmp_path):
    with ingress(tmp_path) as (service,server,descriptor,issuer):
        body={'sessionId':'actual-session','workspace':str(tmp_path),'captureId':'native-call'}
        auth='Bearer '+descriptor['grant']
        reference=issuer(body,auth)
        assert issuer(body,auth)==reference
        with pytest.raises(RuntimeFault):issuer({**body,'hostRef':'another'},auth)
        with pytest.raises(RuntimeFault):issuer({**body,'sessionId':'another-session'},auth)
        context=service.lookup(reference)
        with source_scope(context):
            assert native_source('actor')==context
            with pytest.raises(RuntimeFault):native_source('other-actor')
        assert source_value(context)=={'hostRef':'host','sessionId':'actual-session','workspace':str(tmp_path)}
        with pytest.raises(RuntimeFault):service.lookup({'entryRef':reference['entryRef'],'actor':'other-actor'})


def test_entry_expiration_boot_and_account_change_do_not_rebind_business(tmp_path):
    with ingress(tmp_path) as (service,server,descriptor,issuer):
        reference=issuer({'sessionId':'actual-session','workspace':str(tmp_path),'captureId':'native-call'},'Bearer '+descriptor['grant'])
        original=service.lookup(reference)
        from ioalab_sdk.runtime import source_context
        restored=source_context(source_value(original),actor='actor')
        service.clock=lambda:10**12
        with pytest.raises(RuntimeFault):service.lookup(reference)
        with pytest.raises(RuntimeFault):issuer({'sessionId':'actual-session','workspace':str(tmp_path),'captureId':'native-call'},'Bearer '+descriptor['grant'])
        # Existing durable business ownership restores independently of a fresh entry TTL.
        with source_scope(restored):assert native_source('actor').session_id=='actual-session'
        service.clock=lambda:0
        service.boot_id=str(uuid.uuid4())
        with pytest.raises(RuntimeFault):service.lookup(reference)
        service.boot_id=server.boot_id;service.account_ref='another-account'
        with pytest.raises(RuntimeFault):service.lookup(reference)


def test_private_descriptor_write_retries_original_grant(tmp_path,monkeypatch):
    from ioalab_sdk.runtime import native_sources as module
    with ingress(tmp_path) as (service,_,_,_):
        service.registry.hosts['other']=service.registry.hosts['host']
        original=module.private_json;attempts=[]
        def fail_once(path,value):
            attempts.append(hashlib.sha256(value['grant'].encode()).hexdigest())
            if len(attempts)==1:raise OSError('fixture private descriptor write failure')
            return original(path,value)
        monkeypatch.setattr(module,'private_json',fail_once)
        with pytest.raises(OSError):service.provision('other','assistant')
        issuer=service.provision('other','assistant')
        assert callable(issuer) and attempts[0]==attempts[1]


def test_native_control_uses_grant_scope_and_same_ipc_listener(tmp_path):
    with ingress(tmp_path) as (service,server,descriptor,_):
        def handler(scope,payload):
            assert 'grant' not in scope
            original=scope['hostRef'];scope['hostRef']='mutated-copy'
            return {'routedTo':original,'body':payload}
        server.native_handlers={'host':handler}
        body={'opaque':'native transport payload','hostRef':'cannot-route'}
        headers={'Authorization':'Bearer '+server.token,'X-Ioa-Instance':server.boot_id}
        denied=httpx.post(descriptor['controlUrl'],json=body,headers=headers,trust_env=False)
        assert denied.status_code==401
        headers['Authorization']='Bearer '+descriptor['grant']
        accepted=httpx.post(descriptor['controlUrl'],json=body,headers=headers,trust_env=False)
        assert accepted.status_code==200 and accepted.json()['data']['routedTo']=='host'
        assert service.authenticate(headers['Authorization'])['hostRef']=='host'
