"""Initialization consumes explicit public input, independent of checkout layout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from ioalab.seed.domains import load_domains

ROOT=Path(__file__).resolve().parents[1]


def test_manifest_source_digests_survive_checkout_newlines_but_detect_content_changes(tmp_path,monkeypatch):
    from ioalab_services import catalog
    source=tmp_path/'domains';source.mkdir()
    monkeypatch.setattr(catalog,'DOMAINS_DIR',source)
    domain={'account':'ISP_001','label':'领域','category':{'id':'sample'},'services':[]}
    raw=(json.dumps(domain,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    path=source/'domain.json';path.write_bytes(raw)
    expected=catalog.seed_manifest()
    path.write_bytes(raw.replace(b'\n',b'\r\n'))
    assert catalog.seed_manifest()==expected
    domain['label']='修改后的领域'
    path.write_bytes(json.dumps(domain,ensure_ascii=False,indent=2).encode('utf-8'))
    assert catalog.seed_manifest()['source']!=expected['source']


def test_configuration_reports_missing_initialization_input(tmp_path):
    from ioalab.config import Settings
    settings=Settings(data=tmp_path,autoseed=True,seed_file=None,stub=True)
    assert any('runtime.seed_file' in problem for problem in settings.check(seed_manifest=load_domains))
    assert not any('seed' in problem for problem in Settings(data=tmp_path,autoseed=False,stub=True).check(seed_manifest=load_domains))


def test_command_self_check_reports_missing_oversized_and_invalid_manifests(tmp_path):
    from ioalab.cli import _problems
    from ioalab.config import Settings
    large=tmp_path/'large.json';large.write_bytes(b' '*(2*1024*1024+1))
    invalid=tmp_path/'invalid.json';invalid.write_text(json.dumps({'domains':[{'account':'ISP_000','label':'x','category':{'id':'a','name':'A'}}]}),encoding='utf-8')
    for path,message in ((None,'runtime.seed_file is required when initializing accounts and categories'),
                         (large,'Seed manifest must be a readable JSON file of at most 2 MiB'),
                         (invalid,'Each domain requires a unique ISP account')):
        assert message in _problems(Settings(data=tmp_path,autoseed=True,seed_file=path,stub=True))


def test_explicit_seedless_initialization_is_available(tmp_path):
    from conftest import write_platform_configuration
    configuration=write_platform_configuration(tmp_path/'configuration',data=str(tmp_path/'data'),autoseed=True)
    env={**os.environ,'PYTHONUTF8':'1','IOALAB_CONFIG':str(configuration.path)}
    result=subprocess.run([sys.executable,'-m','ioalab','init','--no-seed'],cwd=tmp_path,
                          env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert (tmp_path/'data/ioalab.sqlite').is_file()


def test_explicit_manifest_works_from_another_directory(tmp_path, monkeypatch):
    source=ROOT/'deploy/default-seed.json'
    manifest=tmp_path/'public.json'
    manifest.write_bytes(source.read_bytes())
    monkeypatch.chdir(tmp_path)
    domains=load_domains(manifest)
    assert len(domains)==8
    assert sum(len(d['services']) for d in domains)==52
    assert {d['account'] for d in domains}=={f'ISP_{i:03d}' for i in range(1,9)}
    assert all('sample' not in s and 'verifier' not in s for d in domains for s in d['services'])


@pytest.mark.parametrize('kind',['missing','empty','duplicate_account','duplicate_category'])
def test_bad_initialization_does_not_fall_back_to_source(kind,tmp_path):
    data=json.loads((ROOT/'deploy/default-seed.json').read_text(encoding='utf-8'))
    if kind=='missing':
        with pytest.raises(ValueError,match='runtime.seed_file'):
            load_domains(None)
        return
    if kind=='empty':data['domains']=[]
    elif kind=='duplicate_account':data['domains'][1]['account']=data['domains'][0]['account']
    else:data['domains'][1]['category']['id']=data['domains'][0]['category']['id']
    path=tmp_path/'bad.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError):load_domains(path)
