"""Initialization consumes explicit public input, independent of checkout layout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from ioalab.seed.domains import load_domains

ROOT=Path(__file__).resolve().parents[1]


def test_configuration_reports_missing_initialization_input(tmp_path):
    from ioalab.config import Settings
    settings=Settings(data=tmp_path,autoseed=True,seed_file=None,stub=True)
    assert any('IOALAB_SEED_FILE' in problem for problem in settings.check())


def test_explicit_seedless_initialization_is_available(tmp_path):
    env={**os.environ,'PYTHONUTF8':'1','IOALAB_DATA':str(tmp_path/'data'),
         'IOALAB_STUB':'1','IOALAB_MODE':'dev','IOALAB_AUTOSEED':'1'}
    env.pop('IOALAB_SEED_FILE',None)
    result=subprocess.run([sys.executable,'-m','ioalab','init','--no-seed'],cwd=tmp_path,
                          env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert (tmp_path/'data/ioalab.sqlite').is_file()


def test_seedless_catalog_is_empty(platform,client,monkeypatch):
    monkeypatch.setattr(platform.s,'seed_file',None)
    result=client.get('/api/services/definitions')
    assert result.status_code==200
    assert result.json()==[]


def test_explicit_manifest_works_from_another_directory(tmp_path, monkeypatch):
    source=ROOT/'deploy/default-seed.json'
    manifest=tmp_path/'public.json'
    manifest.write_bytes(source.read_bytes())
    monkeypatch.chdir(tmp_path)
    domains=load_domains(manifest)
    assert len(domains)==8
    assert sum(len(d['services']) for d in domains)==50
    assert {d['account'] for d in domains}=={f'ISP_{i:03d}' for i in range(1,9)}
    assert all('sample' not in s and 'verifier' not in s for d in domains for s in d['services'])


@pytest.mark.parametrize('kind',['missing','empty','duplicate_account','duplicate_category'])
def test_bad_initialization_does_not_fall_back_to_source(kind,tmp_path):
    data=json.loads((ROOT/'deploy/default-seed.json').read_text(encoding='utf-8'))
    if kind=='missing':
        with pytest.raises(ValueError,match='IOALAB_SEED_FILE'):
            load_domains(None)
        return
    if kind=='empty':data['domains']=[]
    elif kind=='duplicate_account':data['domains'][1]['account']=data['domains'][0]['account']
    else:data['domains'][1]['category']['id']=data['domains'][0]['category']['id']
    path=tmp_path/'bad.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError):load_domains(path)
