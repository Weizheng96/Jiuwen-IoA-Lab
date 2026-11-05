"""Verify and load the vendored official base image without registry access."""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

DEFAULT=Path(__file__).resolve().parents[1]/'vendor/container-images/python-3.11-slim-bookworm.json'


@contextmanager
def verified_archive(manifest):
    manifest=Path(manifest).resolve();value=json.loads(manifest.read_text('utf-8'))
    if value.get('format')!='ioalab.container-image/1' or value.get('compression')!='gzip':
        raise ValueError('Unsupported vendored image manifest')
    parts=value.get('parts')
    if not isinstance(parts,list) or not parts:raise ValueError('Missing image archive parts')
    names=[part.get('file') for part in parts]
    if len(set(names))!=len(names):raise ValueError('Duplicate image archive part')
    with tempfile.TemporaryDirectory(prefix='ioalab-base-') as temporary:
        compressed=Path(temporary)/'image.tar.gz';combined=hashlib.sha256();count=0
        with compressed.open('wb') as output:
            for part in parts:
                name=part['file']
                if not isinstance(name,str) or Path(name).name!=name or '/' in name or '\\' in name:
                    raise ValueError('Image part escaped its manifest directory')
                path=manifest.parent/name
                if path.is_symlink() or not path.is_file() or path.stat().st_size!=part['bytes']:
                    raise ValueError('Image archive part size or ownership changed')
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    while chunk:=stream.read(1024*1024):
                        digest.update(chunk);combined.update(chunk);count+=len(chunk);output.write(chunk)
                if digest.hexdigest()!=part['sha256']:raise ValueError('Image archive part checksum changed')
        if count!=value['archiveBytes'] or combined.hexdigest()!=value['archiveSha256']:
            raise ValueError('Combined image archive checksum changed')
        archive=Path(temporary)/'image.tar';digest=hashlib.sha256();count=0
        with gzip.open(compressed,'rb') as stream,archive.open('wb') as output:
            while chunk:=stream.read(1024*1024):
                count+=len(chunk)
                if count>value['uncompressedBytes']:raise ValueError('Image archive exceeds declared size')
                digest.update(chunk);output.write(chunk)
        if count!=value['uncompressedBytes'] or digest.hexdigest()!=value['uncompressedSha256']:
            raise ValueError('Uncompressed image archive checksum changed')
        with tarfile.open(archive) as bundle:
            entries=json.load(bundle.extractfile('manifest.json'))
            if len(entries)!=1:raise ValueError('Image archive must select one platform image')
            config=bundle.extractfile(entries[0]['Config']).read()
            if 'sha256:'+hashlib.sha256(config).hexdigest()!=value['configDigest']:
                raise ValueError('Image config digest differs from the manifest')
            image_config=json.loads(config)
            if image_config.get('os')+'/'+image_config.get('architecture')!=value['platform']:
                raise ValueError('Image config platform differs from the manifest')
        yield value,archive,image_config


def verify(manifest=DEFAULT):
    with verified_archive(manifest) as (value,_,_):
        return {'status':'completed','configDigest':value['configDigest'],'indexDigest':value['indexDigest'],'repoDigest':value['repoDigest'],
                'platform':value['platform'],'archiveSha256':value['archiveSha256']}


def load(manifest=DEFAULT):
    with verified_archive(manifest) as (value,archive,config):
        subprocess.run(['docker','load','--input',str(archive)],check=True,stdout=subprocess.DEVNULL)
        result=subprocess.run(['docker','image','inspect',value['reference']],check=True,capture_output=True,text=True)
        images=json.loads(result.stdout)
        if len(images)!=1:raise ValueError('Docker returned an ambiguous loaded image')
        image=images[0]
        runtime_id=image.get('Id');digests=image.get('RepoDigests',[])
        classic=runtime_id==value['configDigest']
        containerd=runtime_id==value['indexDigest'] and value['repoDigest'] in digests
        if (not (classic or containerd)
                or image.get('Os')+'/'+image.get('Architecture')!=value['platform']
                or image.get('RootFS',{}).get('Layers')!=config.get('rootfs',{}).get('diff_ids')):
            raise ValueError('Loaded image differs from the reviewed official image')
        return {'status':'completed','loadedImageId':runtime_id,'configDigest':value['configDigest'],
                'indexDigest':value['indexDigest'],'repoDigest':value['repoDigest'],'platform':value['platform']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('verify','load'));parser.add_argument('--manifest',type=Path,default=DEFAULT)
    args=parser.parse_args()
    result=load(args.manifest) if args.command=='load' else verify(args.manifest)
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
