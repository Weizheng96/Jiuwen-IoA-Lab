"""Opt-in background product installation and real contract takeover through public SDK calls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def install_products(folder):
    skills=[];manifests=[]
    for role,name in [('assistant','ioalab-delegate'),('provider','ioalab-provider')]:
        with zipfile.ZipFile(ROOT/'dist'/('ioalab-'+role+'-skill.zip')) as archive:
            for member in archive.namelist():
                if not (folder/'bundle'/member).resolve().is_relative_to((folder/'bundle').resolve()):raise ValueError('Invalid skill archive path')
            archive.extractall(folder/'bundle')
        skill=folder/'bundle'/name;skills.append(skill)
        manifests.append((skill/'assets/runtime.json').read_bytes())
    assert manifests[0]==manifests[1]
    env={**os.environ,'IOALAB_ACCESS_CACHE':str(folder/'runtime'),'PYTHONUTF8':'1'}
    def call(skill,operation):
        before=time.monotonic()
        result=subprocess.run([sys.executable,'-I','-X','utf8',str(skill/'scripts/ioalab.py'),operation],
            cwd=folder,env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        packet=json.loads(result.stdout)
        if result.returncode:
            log=packet.get('data',{}).get('logPath')
            if log and Path(log).is_file():print(Path(log).read_text(encoding='utf-8',errors='replace')[-12000:],file=sys.stderr)
            raise AssertionError(result.stdout+result.stderr)
        return packet,round(time.monotonic()-before,3)
    # Both launchers must exit normally while one separate worker owns the installation.
    with ThreadPoolExecutor(max_workers=2) as pool:
        initial=list(pool.map(lambda skill:call(skill,'runtime.install'),skills))
    waiting=[packet for packet,_ in initial if packet['status']=='waiting']
    assert waiting and all(packet['status'] in ('waiting','completed') for packet,_ in initial)
    attempt=waiting[0]['data']['attemptId']
    assert all(packet['data']['attemptId']==attempt for packet in waiting)
    assert all(packet['data']['requestSubmitted'] is False for packet,_ in initial)
    after_exit,_=call(skills[0],'runtime.install.status')
    assert after_exit['status']=='waiting' and after_exit['data']['attemptId']==attempt,after_exit
    until=time.monotonic()+300;polls=0
    while True:
        packet,_=call(skills[polls%2],'runtime.install.status');polls+=1
        if packet['status']=='completed':break
        assert packet['status']=='waiting' and packet['data']['attemptId']==attempt,packet
        if time.monotonic()>=until:raise TimeoutError('Original dependency worker did not reach verified completion')
        time.sleep(.5)
    python=Path(packet['data']['python'])
    for skill in skills:
        reused,_=call(skill,'runtime.install.status')
        assert reused['status']=='completed' and Path(reused['data']['python'])==python
    tasks=list((folder/'runtime').glob('*.install.json'));assert len(tasks)==1
    task=json.loads(tasks[0].read_text(encoding='utf-8'))
    assert task['attemptId']==attempt and task['phase'] in ('installing','completed')
    proof={'initialStatuses':[packet['status'] for packet,_ in initial],
           'launcherSeconds':[seconds for _,seconds in initial],'originalAttemptId':attempt,
           'statusQueries':polls,'sharedTaskCount':len(tasks),'workerCompletedAfterLauncherExit':True}
    return python,manifests[0],proof


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path);args=parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='ioalab-clean-install-') as temporary:
        folder=Path(temporary);python,manifest,background=install_products(folder)
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
                     'src/access/skills/_shared/ioalab.py','src/access/skills/_shared/installer.py',
                     'tools/build_access_skills.py','tools/check_bootstrap.py','tests/test_installed_upgrade.py']
            report={'scope':'Clean Windows background installation from both ZIPs; installed SDK HTTP/WS takeover after safe stop with actual A2C contract and AP2 proposal fee',
                    'releaseId':hashlib.sha256(manifest).hexdigest(),'manifest':json.loads(manifest),'backgroundInstallation':background,
                    'verification':{'exitCode':0,'test':'tests/test_installed_upgrade.py','result':result.stdout.strip().splitlines()[-1]},
                    'sources':{name:'sha256:'+hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
                    'limits':['Contract host and user approval were explicit isolated test drivers','No Jiuwen model invocation, native web or new video proof','Windows only; no Linux/systemd upgrade claim']}
            args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Clean background shared installation, optional tools and installed contract takeover passed');return 0


if __name__=='__main__':raise SystemExit(main())
