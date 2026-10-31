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
        installed=None
        for role,name in [('assistant','ioalab-delegate'),('provider','ioalab-provider')]:
            with zipfile.ZipFile(ROOT/'dist'/('ioalab-'+role+'-skill.zip')) as archive:
                for member in archive.namelist():
                    if not (folder/'bundle'/member).resolve().is_relative_to((folder/'bundle').resolve()):raise ValueError('Invalid skill archive path')
                archive.extractall(folder/'bundle')
            skill=folder/'bundle'/name
            spec=importlib.util.spec_from_file_location('bootstrap_'+role,skill/'scripts/ioalab.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            python=module.ensure_runtime(skill/'assets',folder/'runtime')
            if installed is not None:assert python==installed
            installed=python
            assert module.ensure_runtime(skill/'assets',folder/'runtime')==python
        subprocess.run([str(python),'-I','-m','ioalab_sdk.runtime','--help'],check=True,cwd=folder)
        subprocess.run([str(python),'-I','-m','ioalab_jiuwen','--help'],check=True,cwd=folder)
        subprocess.run([str(python),'-I','-c','import importlib.util as u; import ioalab_sdk,ioalab_jiuwen; assert u.find_spec("ioalab") is None; assert u.find_spec("jiuwenswarm") is None'],check=True,cwd=folder)
    print('Both product skills, isolated SDK/host install and shared cached reuse passed')


if __name__=='__main__': main()
