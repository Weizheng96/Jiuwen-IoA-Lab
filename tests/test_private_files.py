"""Private binary uploads, durable quota and message-transaction read grants."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import uuid

import httpx
import pytest
from a2c.codec import digest_bytes
from ioalab.errors import LabError
from ioalab_sdk.runtime.files_api import PrivateFiles
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_service_publication import service


def uid():return str(uuid.uuid4())


def test_binary_upload_original_receipt_and_recipient_read_grant_share_transaction(platform,local_client,service):
    local=local_client;account,identity=service
    sender=PrivateFiles(local.session,local.signer.id);receiver=PrivateFiles(account,identity.agent_id)
    data=b'original\x00\xff'+uid().encode();operation=uid()
    ref=sender.put('materials/说明.bin',data,operation)
    assert sender.put('materials/说明.bin',data,operation)==ref
    assert sender.get(ref)==data
    with pytest.raises(httpx.HTTPStatusError) as missing:receiver.get(ref)
    assert missing.value.response.status_code==404
    with pytest.raises(RuntimeError):
        with platform.db.tx() as conn:
            platform.files.grant_in(conn,local.signer.id,identity.agent_id,{ref['name']:ref})
            raise RuntimeError('test message transaction aborted')
    with pytest.raises(httpx.HTTPStatusError):receiver.get(ref)
    with platform.db.tx() as conn:platform.files.grant_in(conn,local.signer.id,identity.agent_id,{ref['name']:ref})
    assert receiver.get(ref)==data
    with pytest.raises(LabError,match='original uploader'):
        with platform.db.tx() as conn:platform.files.grant_in(conn,identity.agent_id,'unrelated-actor',{ref['name']:ref})


def test_file_owner_digest_name_and_exact_original_operation_are_checked(platform,client,local_client,service):
    local=local_client;account,identity=service;data=uid().encode()
    url=local.base+'/api/v1/files/'+local.signer.id
    params={'operationId':uid(),'name':'brief.txt','digest':digest_bytes(data)}
    assert client.post(url,params=params,content=data).status_code==401
    assert client.post(url,headers={'Authorization':'Bearer '+account.token},params=params,content=data).status_code==403
    sender=PrivateFiles(local.session,local.signer.id);ref=sender.put(params['name'],data,params['operationId'])
    with pytest.raises(httpx.HTTPStatusError) as changed:sender.put('other.txt',data,params['operationId'])
    assert changed.value.response.status_code==409
    for name in ('../keys.json','/private/key','C:/keys.json','a\\b','a\nsecret'):
        with pytest.raises(httpx.HTTPStatusError) as invalid:sender.put(name,data,uid())
        assert invalid.value.response.status_code==400
    response=client.post(url,headers={'Authorization':'Bearer '+local.token},params={**params,'operationId':uid(),'digest':'sha256:'+'0'*64},content=data)
    assert response.status_code==400
    assert len(platform.db.rows('SELECT * FROM private_files WHERE actor=?',local.signer.id))==1


def test_concurrent_upload_quota_is_reserved_before_disk_io(platform,local_client,monkeypatch):
    import ioalab.files as module
    local=local_client;sender=PrivateFiles(local.session,local.signer.id)
    monkeypatch.setattr(module,'ACCOUNT_QUOTA',6)
    def put(number):
        try:return sender.put('part'+str(number),b'abcd',uid())
        except httpx.HTTPStatusError as exc:return exc.response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(put,(1,2)))
    assert results.count(429)==1 and sum(isinstance(v,dict) for v in results)==1
    rows=platform.db.rows('SELECT * FROM private_files WHERE actor=?',local.signer.id)
    assert len(rows)==1 and rows[0]['size']==4 and rows[0]['ready']==1


def test_interrupted_disk_write_resumes_same_pending_reference_and_quota(platform,local_client,monkeypatch):
    local=local_client;sender=PrivateFiles(local.session,local.signer.id);data=uid().encode();operation=uid()
    original=Path.replace
    def fail_upload(path,target):
        if path.parent==platform.files.root and path.suffix=='.upload':raise OSError('test interrupted blob commit')
        return original(path,target)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'replace',fail_upload)
        with pytest.raises(httpx.HTTPStatusError) as failed:sender.put('artifact.bin',data,operation)
        assert failed.value.response.status_code==503
    rows=platform.db.rows('SELECT * FROM private_files WHERE actor=?',local.signer.id)
    assert len(rows)==1 and not rows[0]['ready']
    pending=platform.files.descriptor(rows[0])
    with pytest.raises(httpx.HTTPStatusError):sender.get(pending)
    restored=sender.put('artifact.bin',data,operation)
    assert restored==pending and sender.get(restored)==data
    assert len(platform.db.rows('SELECT * FROM private_files WHERE actor=?',local.signer.id))==1


def test_corruption_and_tampered_reference_never_return_verified_bytes(platform,local_client):
    local=local_client;sender=PrivateFiles(local.session,local.signer.id);data=uid().encode()
    ref=sender.put('delivery.bin',data,uid())
    with pytest.raises(RuntimeFault,match='digest and size'):sender.get({**ref,'digest':'sha256:'+'0'*64})
    path=platform.files.path(ref['digest'])
    try:
        path.write_bytes(b'corrupted')
        with pytest.raises(httpx.HTTPStatusError) as corrupt:sender.get(ref)
        assert corrupt.value.response.status_code==409
    finally:path.write_bytes(data)
    assert sender.get(ref)==data
