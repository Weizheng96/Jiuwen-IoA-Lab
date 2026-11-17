"""Actual local RPC/source/control setup boundary; no native process or model."""
from contextlib import contextmanager
import json

import httpx
import pytest

from ioalab_sdk.runtime import (HostRegistry,HostRegistration,LocalClient,ResidentRuntime,
    RuntimeConfig,NATIVE_ENTRY_BIND_OPERATION,native_source_descriptor,read_json,private_json,RuntimeFault)
from test_resident_runtime import configuration


@contextmanager
def running(path,*,resource=None):
    class Adapter:
        registration=HostRegistration('setup-fixture','1')
        def submit(self,*args,**kwargs):pytest.fail('Setup dispatched a model')
        def check(self):pytest.fail('Setup/start/status probed a host')
    registry=HostRegistry({'native':Adapter()},'native')
    runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=registry,native_entry_required=True,
        native_resources={'native':resource} if resource else None,
        native_handlers={'native':resource.exchange} if resource else None).start()
    try:
        descriptor=read_json(native_source_descriptor(runtime.config.home,'native','personal'))
        yield runtime,LocalClient(runtime.config.home),descriptor
    finally:runtime.close()


def native_post(descriptor,url,body,*,token=None):
    return httpx.post(url,json=body,headers={'Authorization':'Bearer '+(token or descriptor['grant']),
        'X-Ioa-Instance':descriptor['bootId']},trust_env=False,timeout=5)


def capture(descriptor,tmp_path,identifier='capture-original'):
    response=native_post(descriptor,descriptor['url'],{'sessionId':'actual-original-session',
        'workspace':str(tmp_path),'captureId':identifier})
    assert response.status_code==200,response.text
    return response.json()['data']


def control(descriptor,action,**body):
    return native_post(descriptor,descriptor['controlUrl'],{'purpose':'sdk.entry','action':action,**body})


def test_explicit_setup_uses_real_source_and_same_private_control_without_model(platform,tmp_path):
    path,_=configuration(platform,tmp_path,service=False)
    with running(path) as (runtime,rpc,descriptor):
        reference=capture(descriptor,tmp_path)
        assert control(descriptor,'pending',**reference).json()['data'] is None
        status=rpc.call('runtime.status')['data']
        assert status['nativeEntryBindings']['native']['state']=='unknown'
        denied=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='missing-source')
        assert denied['status']=='rejected' and denied['error']['code']=='not_ready'
        invalid=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{'hostRef':'model-selected'},operation_id='bad-args',context_ref=reference)
        assert invalid['status']=='rejected' and invalid['error']['code']=='invalid_input'
        accepted=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='original-setup',context_ref=reference)
        assert accepted['status']=='waiting' and accepted['data']['phase']=='intent'
        pending=control(descriptor,'pending',**reference).json()['data']
        assert pending['operationId']=='original-setup'
        claim=control(descriptor,'claim',**reference,intentRef=pending['intentRef'],targetDigest='opaque-actual-native-target').json()['data']
        saved=control(descriptor,'complete',claimRef=claim['claimRef'],targetDigest=claim['targetDigest'],savedReceiptRef='adapter-original-save')
        assert saved.status_code==200 and saved.json()['data']['state']=='completed'
        runtime.native_sources.clock=lambda:10**12
        recovered=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='original-setup')
        assert recovered['status']=='completed' and recovered['data']==saved.json()['data']
        assert control(descriptor,'pending',**reference).status_code==400
        projection=rpc.call('runtime.status')['data']['nativeEntryBindings']['native']
        assert projection==recovered['data']
        public=json.dumps(projection)
        assert not any(secret in public for secret in (descriptor['grant'],claim['claimRef'],pending['intentRef'],str(tmp_path)))


def test_restart_reserved_lookup_and_saved_receipt_ack_need_no_new_capture(platform,tmp_path):
    path,_=configuration(platform,tmp_path,service=False)
    saved_file=tmp_path/'adapter-saved-receipt.json'
    with running(path) as (_,rpc,descriptor):
        reference=capture(descriptor,tmp_path)
        assert rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='saved-setup',context_ref=reference)['status']=='waiting'
        pending=control(descriptor,'pending',**reference).json()['data']
        claim=control(descriptor,'claim',**reference,intentRef=pending['intentRef'],targetDigest='original-target').json()['data']
        private_json(saved_file,{**claim,'savedReceiptRef':'actual-original-adapter-file'})
        original_boot=descriptor['bootId']
    with running(path) as (_,rpc,descriptor):
        assert descriptor['bootId']!=original_boot
        # No capture is made in the replacement runtime. The old source is not
        # renewed or guessed; only the already saved original claim is read.
        pending=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='saved-setup')
        assert pending['status']=='waiting' and pending['data']['phase']=='reserved'
        assert pending['data']['action']=='recover_saved_receipt'
        saved=read_json(saved_file)
        response=control(descriptor,'complete',claimRef=saved['claimRef'],targetDigest=saved['targetDigest'],savedReceiptRef=saved['savedReceiptRef'])
        assert response.status_code==200 and response.json()['data']['state']=='completed'
        completed=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='saved-setup')
        assert completed['status']=='completed' and completed['data']==response.json()['data']
        assert read_json(saved_file)==saved


def test_private_setup_rejects_other_credentials_and_metadata_before_permission(platform,tmp_path):
    path,_=configuration(platform,tmp_path,service=False)
    with running(path) as (runtime,rpc,descriptor):
        reference=capture(descriptor,tmp_path)
        assert rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='original',context_ref=reference)['status']=='waiting'
        denied=native_post(descriptor,descriptor['controlUrl'],{'purpose':'sdk.entry','action':'pending',**reference},token=runtime.ipc.token)
        assert denied.status_code==401
        for body in ({'purpose':'sdk.entry','action':[]},
                     {'purpose':'sdk.entry','action':'pending',**reference,'hostRef':'other'},
                     {'purpose':'sdk.entry','action':'complete','claimRef':'missing','targetDigest':'target','savedReceiptRef':'file'}):
            refused=native_post(descriptor,descriptor['controlUrl'],body)
            assert refused.status_code==400 and refused.json()['status']=='rejected'
        pending=control(descriptor,'pending',**reference).json()['data']
        assert pending['operationId']=='original'
        assert rpc.call('runtime.status')['data']['nativeEntryBindings']['native']['phase']=='intent'


def test_public_stop_refuses_new_save_obligations_but_allows_original_saved_ack(platform,tmp_path):
    path,_=configuration(platform,tmp_path,service=False)
    with running(path) as (runtime,rpc,descriptor):
        reference=capture(descriptor,tmp_path)
        assert rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='original',context_ref=reference)['status']=='waiting'
        pending=control(descriptor,'pending',**reference).json()['data']
        claim=control(descriptor,'claim',**reference,intentRef=pending['intentRef'],targetDigest='original-target').json()['data']
        stopped=rpc.call('runtime.stop',{'bootId':runtime.ipc.boot_id})
        assert stopped['status']=='completed'
        new=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='after-stop',context_ref=reference)
        assert new['status']=='rejected' and new['error']['code']=='closed'
        refused=control(descriptor,'claim',**reference,intentRef=pending['intentRef'],targetDigest='original-target')
        assert refused.status_code==400 and refused.json()['error']['code']=='closed'
        saved=control(descriptor,'complete',claimRef=claim['claimRef'],targetDigest=claim['targetDigest'],savedReceiptRef='original-written-before-stop')
        assert saved.status_code==200 and saved.json()['data']['state']=='completed'


def test_native_resource_gets_only_sdk_verified_permission_before_its_actual_save(platform,tmp_path):
    path,_=configuration(platform,tmp_path,service=False)
    saved_file=tmp_path/'native-private-context.json'
    class NativeResource:
        callback=None
        def bind_entry_control(self,callback):
            assert self.callback is None
            self.callback=callback
        def drain(self,timeout):return True
        def exchange(self,scope,body):
            if set(body)!={'kind','entryRef'} or body['kind']!='capture':
                raise RuntimeFault('invalid_input','Bare model or wrapper claim references do not authorize a save')
            pending=self.callback(scope,{'purpose':'sdk.entry','action':'pending','entryRef':body['entryRef']})
            if pending is None:raise RuntimeFault('not_ready','Complete the explicit original setup before capture')
            with pytest.raises(RuntimeFault) as foreign:
                self.callback({**scope,'hostRef':'other-host'},
                    {'purpose':'sdk.entry','action':'claim','entryRef':body['entryRef'],
                     'intentRef':pending['intentRef'],'targetDigest':'other-target'})
            assert foreign.value.code=='identity_conflict' and not saved_file.exists()
            claim=self.callback(scope,{'purpose':'sdk.entry','action':'claim','entryRef':body['entryRef'],
                'intentRef':pending['intentRef'],'targetDigest':'actual-native-capture-target'})
            # The real adapter owns its OS lock/path validation. At this SDK
            # boundary the test native system persists the granted receipt first.
            private_json(saved_file,{**claim,'actualCapture':'original native API target'})
            return self.callback(scope,{'purpose':'sdk.entry','action':'complete','claimRef':claim['claimRef'],
                'targetDigest':claim['targetDigest'],'savedReceiptRef':'actual-private-file'})
    resource=NativeResource()
    with running(path,resource=resource) as (_,rpc,descriptor):
        assert callable(resource.callback)
        reference=capture(descriptor,tmp_path)
        raw={'kind':'capture',**reference,'claimRef':'model-invented'}
        assert native_post(descriptor,descriptor['controlUrl'],raw).status_code==400
        assert not saved_file.exists()
        before=native_post(descriptor,descriptor['controlUrl'],{'kind':'capture',**reference})
        assert before.status_code==400 and before.json()['error']['code']=='not_ready'
        assert not saved_file.exists()
        assert rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='resource-setup',context_ref=reference)['status']=='waiting'
        actual=native_post(descriptor,descriptor['controlUrl'],{'kind':'capture',**reference})
        assert actual.status_code==200 and actual.json()['data']['state']=='completed'
        assert read_json(saved_file)['targetDigest']=='actual-native-capture-target'
        result=rpc.call(NATIVE_ENTRY_BIND_OPERATION,{},operation_id='resource-setup')
        assert result['status']=='completed' and result['data']==actual.json()['data']
