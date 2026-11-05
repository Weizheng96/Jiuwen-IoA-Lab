import gzip
import hashlib
import json
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import container_images as images


@pytest.fixture
def manifest(tmp_path):
    config=json.dumps({'os':'linux','architecture':'amd64','rootfs':{'diff_ids':['sha256:layer']}}).encode()
    config_digest='sha256:'+hashlib.sha256(config).hexdigest()
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as bundle:
        for name,data in [('manifest.json',json.dumps([{'Config':'config.json'}]).encode()),('config.json',config)]:
            info=tarfile.TarInfo(name);info.size=len(data);bundle.addfile(info,io.BytesIO(data))
    content=buffer.getvalue();blob=gzip.compress(content,mtime=0)
    (tmp_path/'base.gz').write_bytes(blob)
    value={'format':'ioalab.container-image/1','compression':'gzip','parts':[{'file':'base.gz','bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()}],
           'archiveBytes':len(blob),'archiveSha256':hashlib.sha256(blob).hexdigest(),
           'uncompressedBytes':len(content),'uncompressedSha256':hashlib.sha256(content).hexdigest(),
           'configDigest':config_digest,'indexDigest':'sha256:index','reference':'python:fixture','repoDigest':'python@sha256:index','platform':'linux/amd64'}
    path=tmp_path/'manifest.json';path.write_text(json.dumps(value));return path


@pytest.mark.parametrize('engine',['classic','containerd'])
def test_verified_parts_are_reassembled_without_registry_access(manifest,monkeypatch,engine):
    calls=[]
    value=json.loads(manifest.read_text())
    def run(argv,**kwargs):
        calls.append(argv)
        if argv[1]=='load':
            with tarfile.open(argv[-1]) as archive:assert archive.extractfile('config.json') is not None
        return SimpleNamespace(stdout=json.dumps([{'Id':value['configDigest'] if engine=='classic' else value['indexDigest'],
            'RepoDigests':[] if engine=='classic' else [value['repoDigest']],
            'RootFS':{'Layers':['sha256:layer']},'Os':'linux','Architecture':'amd64'}]))
    monkeypatch.setattr(images.subprocess,'run',run)
    assert images.load(manifest)['status']=='completed'
    assert [call[1] for call in calls]==['load','image']


@pytest.mark.parametrize('fault',['checksum','traversal','decompressed-size'])
def test_changed_archive_rejected_before_docker_runs(manifest,monkeypatch,fault):
    value=json.loads(manifest.read_text())
    if fault=='checksum':value['parts'][0]['sha256']='0'*64
    elif fault=='traversal':value['parts'][0]['file']='../base.gz'
    else:value['uncompressedBytes']=1
    manifest.write_text(json.dumps(value))
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**k:pytest.fail('Docker ran before archive validation'))
    with pytest.raises(ValueError):images.load(manifest)


def test_loaded_image_identity_mismatch_is_rejected(manifest,monkeypatch):
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps([{'Id':'sha256:other'}])))
    with pytest.raises(ValueError,match='differs'):images.load(manifest)
