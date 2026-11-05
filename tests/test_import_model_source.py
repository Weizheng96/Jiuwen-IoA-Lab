"""One-time import uses fake credentials; no external service or existing runtime input."""
import json
import os
import subprocess

import pytest

from tools import import_model_source as importer
from tools.llm import ModelInputError,profile


def csv_source(tmp_path,text=None):
    path=tmp_path/'authorized.csv'
    path.write_text(text or 'apiHost,example.invalid\napiKey,DUMMY-CSV-KEY\nopenAiCompatible,https://example.invalid/spaces/original/v1\n',encoding='utf-8')
    return path


def test_import_keeps_exact_space_endpoint_original_source_and_protected_destination(tmp_path):
    source=csv_source(tmp_path);before=source.read_bytes();target=tmp_path/'llm_apikey'
    importer.import_csv(source,name='default',model='test-model',destination=target)
    selected=profile('default',target)
    assert selected['base_url']=='https://example.invalid/spaces/original/v1'
    assert selected['api_key']=='DUMMY-CSV-KEY' and source.read_bytes()==before
    assert json.loads(target.read_text())['tts']['url']=='wss://example.invalid/api-ws/v1/realtime'
    if os.name=='nt':
        acl=subprocess.run(['icacls',str(target)],capture_output=True,check=True).stdout
        assert b'(I)' not in acl
    else:assert target.stat().st_mode&0o777==0o600
    with pytest.raises(ModelInputError):importer.import_csv(source,name='default',model='other',destination=target)
    assert profile('default',target)==selected


@pytest.mark.parametrize('defect',['duplicate','missing','host','url','extra-column','protection'])
def test_import_refuses_bad_source_or_protection_failure_without_secret_or_partial_file(tmp_path,monkeypatch,defect,capsys):
    text='apiHost,example.invalid\napiKey,DUMMY-CSV-KEY\nopenAiCompatible,https://example.invalid/spaces/original/v1\n'
    if defect=='duplicate':text+='apiKey,OTHER-DUMMY-KEY\n'
    if defect=='missing':text=text.replace('apiKey,DUMMY-CSV-KEY\n','')
    if defect=='host':text=text.replace('apiHost,example.invalid','apiHost,other.invalid')
    if defect=='url':text=text.replace('https://','http://')
    if defect=='extra-column':text=text.replace('apiKey,DUMMY-CSV-KEY','apiKey,DUMMY-CSV-KEY,unexpected')
    if defect=='protection':
        def refuse(*_):raise RuntimeError('DUMMY-CSV-KEY')
        monkeypatch.setattr(importer,'protect',refuse)
    source=csv_source(tmp_path,text);before=source.read_bytes();target=tmp_path/'llm_apikey'
    with pytest.raises(ModelInputError) as error:importer.import_csv(source,name='default',model='test-model',destination=target)
    assert not target.exists() and source.read_bytes()==before
    assert 'DUMMY-CSV-KEY' not in str(error.value)+str(capsys.readouterr())
