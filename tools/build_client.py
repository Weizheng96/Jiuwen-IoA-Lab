"""Build the same single client wheel installed by pip from a GitCode tag."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def build(output, *, root=ROOT):
    root=Path(root).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ioa-client-build-') as temporary:
        snapshot=Path(temporary)
        shutil.copytree(root/'src/client',snapshot/'src/client',
                        ignore=shutil.ignore_patterns('build','dist','*.egg-info','__pycache__','*.pyc'))
        vendor=snapshot/'vendor';vendor.mkdir()
        shutil.copy2(root/'vendor/SOURCES.json',vendor/'SOURCES.json')
        manifest=json.loads((vendor/'SOURCES.json').read_text(encoding='utf-8'))
        for item in manifest['components']:
            if item['id'] in ('a2c','jiuwenswarm-process-sdk'):
                source=root/'vendor'/item['wheelInfo']['file'];target=vendor/item['wheelInfo']['file']
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        shutil.copytree(root/'vendor/licenses',vendor/'licenses')
        subprocess.run([sys.executable,'-m','pip','wheel','--no-index','--no-deps','--no-build-isolation',
                        '--wheel-dir',str(output),str(snapshot/'src/client')],check=True,
                       env={**os.environ,'SOURCE_DATE_EPOCH':'315532800','PYTHONUTF8':'1'})
    return output/('ioalab_client-'+(root/'src/client/VERSION').read_text().strip().removeprefix('v')+'-py3-none-any.whl')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=ROOT/'dist/client')
    print(build(parser.parse_args().out))
