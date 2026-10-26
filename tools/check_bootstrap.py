"""Opt-in clean SDK bootstrap check. Requires the built bundle and dependency network."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='ioalab-clean-install-') as folder:
        folder=Path(folder)
        with zipfile.ZipFile(ROOT/'dist/ioalab-client.zip') as archive:
            archive.extractall(folder/'bundle')
        skill=folder/'bundle/ioalab-client/skills/ioalab-delegate'
        spec=importlib.util.spec_from_file_location('bootstrap',skill/'scripts/ioalab.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        python=module.ensure_runtime(skill/'assets',folder/'runtime')
        subprocess.run([str(python),'-m','ioalab_sdk.cli','--help'],check=True,cwd=folder)
        subprocess.run([str(python),'-c','import importlib.util as u; import ioalab_sdk; assert u.find_spec("ioalab") is None; assert u.find_spec("openjiuwen") is None'],check=True,cwd=folder)
        assert module.ensure_runtime(skill/'assets',folder/'runtime')==python
    print('Clean bootstrap, SDK independence and cached reuse passed')


if __name__=='__main__': main()
