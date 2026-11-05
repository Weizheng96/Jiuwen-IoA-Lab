"""Resource collection reports real counters or fails; no synthetic measurements."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    path=Path(__file__).parents[1]/'experiments/trial_resources.py'
    spec=importlib.util.spec_from_file_location('trial_resource_experiment',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def arguments(monkeypatch,output,*extra):
    import sys
    monkeypatch.setattr(sys,'argv',['trial_resources','--deployment-profile','linux-server','--deployment','ioa-trial','--phase','three-active',
        '--seconds','1','--out',str(output),*extra])


def test_collector_includes_seat_and_proxy_and_marks_unmeasured_fields(tmp_path,monkeypatch):
    experiment=module();output=tmp_path/'metrics.jsonl';arguments(monkeypatch,output)
    moments=iter([0,0,2]);waits=[]
    experiment.time=SimpleNamespace(monotonic=lambda:next(moments),sleep=waits.append)
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv);assert kwargs['check'] is True
        assert argv[:3]==['docker','--host','unix:///var/run/docker.sock']
        if argv[3]=='ps':
            assert argv[5]=='name=^ioa-trial-'
            return SimpleNamespace(stdout='seat-id\nproxy-id\ningress-id\n')
        assert argv[-3:]==['seat-id','proxy-id','ingress-id']
        return SimpleNamespace(stdout='{"ID":"seat-id","CPUPerc":"12.5%"}\n{"ID":"proxy-id","CPUPerc":"1.5%"}\n{"ID":"ingress-id","CPUPerc":"0.5%"}\n')
    monkeypatch.setattr(experiment.subprocess,'run',run)
    experiment.main()
    row=json.loads(output.read_text())
    assert [v['ID'] for v in row['containers']]==['seat-id','proxy-id','ingress-id']
    assert row['phase']=='three-active' and row['missing'] and waits==[1]


def test_missing_docker_does_not_report_empty_success(tmp_path,monkeypatch):
    experiment=module();output=tmp_path/'metrics.jsonl';arguments(monkeypatch,output)
    experiment.time=SimpleNamespace(monotonic=lambda:0,sleep=lambda _:None)
    def missing(*args,**kwargs):raise FileNotFoundError('docker')
    monkeypatch.setattr(experiment.subprocess,'run',missing)
    with pytest.raises(FileNotFoundError):experiment.main()
    assert output.read_text()==''


@pytest.mark.parametrize('extra',[['--seconds','0'],['--deployment','.*']])
def test_invalid_sampling_never_overwrites_existing_report(tmp_path,monkeypatch,extra):
    experiment=module();output=tmp_path/'metrics.jsonl';output.write_text('original')
    arguments(monkeypatch,output,*extra)
    with pytest.raises(SystemExit):experiment.main()
    assert output.read_text()=='original'


def test_windows_profile_rejects_three_seat_phases_before_output(tmp_path,monkeypatch):
    experiment=module();output=tmp_path/'metrics.jsonl'
    arguments(monkeypatch,output,'--deployment-profile','windows-wsl')
    with pytest.raises(SystemExit):experiment.main()
    assert not output.exists()


def test_windows_idle_records_real_single_capacity(tmp_path,monkeypatch):
    experiment=module();output=tmp_path/'metrics.jsonl'
    arguments(monkeypatch,output,'--deployment-profile','windows-wsl','--phase','one-idle')
    moments=iter([0,0,2]);experiment.time=SimpleNamespace(monotonic=lambda:next(moments),sleep=lambda _:None)
    monkeypatch.setattr(experiment.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=''))
    experiment.main()
    row=json.loads(output.read_text())
    assert row['capacity']==1 and row['deployment_profile']=='windows-wsl' and row['containers']==[]


def test_standalone_help_and_dependency_failure_do_not_use_pytest_pythonpath(tmp_path):
    import subprocess
    import sys
    script=Path(__file__).parents[1]/'experiments/trial_resources.py'
    # Isolated interpreter without site packages cannot inherit pytest's imports.
    command=[sys.executable,'-I','-S',str(script)]
    help_result=subprocess.run([*command,'--help'],capture_output=True,text=True,check=False)
    assert help_result.returncode==0 and '--deployment-profile' in help_result.stdout
    output=tmp_path/'untouched.jsonl'
    args=['--deployment','ioa-trial','--deployment-profile','windows-wsl','--phase','one-idle','--seconds','1','--out',str(output)]
    missing=subprocess.run([*command,*args],capture_output=True,text=True,check=False)
    assert missing.returncode==2 and 'ioalab-trial-controller/bin/python' in missing.stderr
    assert 'Traceback' not in missing.stderr and not output.exists()
    invalid=subprocess.run([*command,*args,'--seconds','0'],capture_output=True,text=True,check=False)
    assert invalid.returncode==2 and 'seconds must be' in invalid.stderr
    assert 'Traceback' not in invalid.stderr and not output.exists()
