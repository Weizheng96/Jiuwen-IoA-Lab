"""Opt-in clean product installation and real contract takeover through public SDK calls."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='ioalab-clean-install-') as folder:
        folder=Path(folder);installed=None;manifests=[]
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
            manifests.append((skill/'assets/runtime.json').read_bytes())
        assert manifests[0]==manifests[1]
        for entry in ('ioalab_sdk.runtime','ioalab_jiuwen','ioalab_tools.schedule_cli'):
            subprocess.run([str(python),'-I','-m',entry,'--help'],check=True,cwd=folder,capture_output=True,text=True)
        subprocess.run([str(python),'-I','-c','import importlib.util as u; from ioalab_sdk.runtime.installation import current; assert current()["releaseId"]; assert u.find_spec("ioalab") is None; assert u.find_spec("jiuwenswarm") is None; assert u.find_spec("ioalab_tools") is not None'],check=True,cwd=folder)
        result=subprocess.run([sys.executable,'-B','-m','pytest','-q','tests/test_installed_upgrade.py','--maxfail=1'],cwd=ROOT,
            env={**os.environ,'PYTHONUTF8':'1','IOALAB_STUB':'1','IOALAB_INSTALLED_ACCESS_PYTHON':str(python)},
            capture_output=True,text=True,encoding='utf-8',timeout=240)
        print(result.stdout,end='');print(result.stderr,end='',file=sys.stderr)
        if result.returncode:return result.returncode
        if args.report:
            sources=['src/access/sdk/ioalab_sdk/runtime/installation.py','src/access/sdk/ioalab_sdk/runtime/__main__.py',
                     'src/access/sdk/ioalab_sdk/runtime/ipc.py','src/access/sdk/ioalab_sdk/runtime/resident.py',
                     'src/access/skills/_shared/ioalab.py','tools/build_access_skills.py','tests/test_installed_upgrade.py']
            report={'scope':'Clean Windows product install from both ZIPs; installed SDK HTTP/WS takeover after safe stop with actual A2C contract and AP2 proposal fee',
                    'releaseId':hashlib.sha256(manifests[0]).hexdigest(),'manifest':json.loads(manifests[0]),
                    'verification':{'exitCode':0,'test':'tests/test_installed_upgrade.py','result':result.stdout.strip().splitlines()[-1]},
                    'sources':{name:'sha256:'+hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
                    'limits':['Contract host and user approval were explicit isolated test drivers','No Jiuwen model invocation, native web or new video proof','Windows only; no Linux/systemd upgrade claim']}
            args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Clean shared product installation, optional tools and installed contract takeover passed')
    return 0


if __name__=='__main__':raise SystemExit(main())
