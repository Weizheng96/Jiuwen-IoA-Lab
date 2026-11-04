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
    monkeypatch.setattr(sys,'argv',['trial_resources','--deployment','ioa-trial','--phase','three-active',
        '--seconds','1','--out',str(output),*extra])


def test_collector_includes_seat_and_proxy_and_marks_unmeasured_fields(tmp_path,monkeypatch):
    experiment=module();output=tmp_path/'metrics.jsonl';arguments(monkeypatch,output)
    moments=iter([0,0,2]);waits=[]
    experiment.time=SimpleNamespace(monotonic=lambda:next(moments),sleep=waits.append)
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv);assert kwargs['check'] is True
        if argv[1]=='ps':
            assert argv[3]=='name=^ioa-trial-'
            return SimpleNamespace(stdout='seat-id\nproxy-id\n')
        assert argv[-2:]==['seat-id','proxy-id']
        return SimpleNamespace(stdout='{"ID":"seat-id","CPUPerc":"12.5%"}\n{"ID":"proxy-id","CPUPerc":"1.5%"}\n')
    monkeypatch.setattr(experiment.subprocess,'run',run)
    experiment.main()
    row=json.loads(output.read_text())
    assert [v['ID'] for v in row['containers']]==['seat-id','proxy-id']
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
