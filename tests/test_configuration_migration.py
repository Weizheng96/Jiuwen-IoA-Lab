"""Explicit import preserves credentials, original records and owned business data."""
import json
from pathlib import Path

import pytest

from deploy.layout import Layout,environment_text
from deploy.migrate_configuration import imported,migrate
from deploy.release import read_json
from ioalab.configuration import defaults,validate


def environment(layout):
    return {'IOALAB_MODE':'trial','IOALAB_BIND':'127.0.0.1','IOALAB_PORT':'8750',
        'IOALAB_PUBLIC_URL':'https://old.example','IOALAB_DATA':str(layout.state/'core'),
        'IOALAB_ADMIN_PASSWORD':'original-manual-password','IOALAB_SECRET':'original-session-secret',
        'IOALAB_ASSISTANT_PASSWORD':'original-assistant-secret','IOALAB_SEED_FILE':'',
        'IOALAB_AUTOSEED':'0','IOALAB_LOGIN_CREDIT':'25.00','IOALAB_LLM_API_KEY':'original-model-key',
        'IOALAB_LLM_BASE_URL':'https://models.example/v1','IOALAB_LLM_MODEL':'original-model'}


def test_environment_import_keeps_original_manual_values_and_rejects_unknowns(tmp_path):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config')
    env=environment(layout);value=validate(imported(env,defaults()))
    assert value['admin']['password']=='original-manual-password'
    assert value['runtime']['session_secret']=='original-session-secret' and value['runtime']['seed_file'] is None
    assert value['llm']['api_key']=='original-model-key' and value['llm']['model']=='original-model'
    assert value['credits']['login']=='25.00' and value['credits']['repeat_daily'] is False
    with pytest.raises(ValueError):imported({**env,'IOALAB_UNKNOWN_FIELD':'preserve-me'},defaults())
    with pytest.raises(ValueError):imported({**env,'IOALAB_ADMIN_PASSWORD':''},defaults())


def test_completed_install_import_is_recoverable_without_changing_original_business_files(tmp_path,monkeypatch):
    layout=Layout(tmp_path/'runtime',tmp_path/'state',tmp_path/'config');identity=layout.claim()
    core=layout.config/'core';core.mkdir();envpath=core/'core.env'
    envpath.write_text(environment_text(environment(layout)),encoding='utf-8')
    (layout.state/'core').mkdir();business=layout.state/'core/contracts.sqlite';business.write_bytes(b'original-signed-business-records')
    initial={'format':'ioalab.initial-configuration/1','deploymentId':identity['deploymentId'],'inputDigest':'old',
        'input':{'public_url':'https://old.example','pypi_index':'https://pypi.tuna.tsinghua.edu.cn/simple','max_model_runs':2},
        'credentials':{'secret':'keep'},'files':{str(envpath):{'phase':'ready'}}}
    path=layout.private/'initial.json';path.write_text(json.dumps(initial),encoding='utf-8');before=path.read_bytes()
    (layout.private/'current-job.json').write_text(json.dumps({'operationId':'a'*32}))
    monkeypatch.setattr('deploy.jobs.Job.observe',lambda self:{'status':'waiting'})
    with pytest.raises(RuntimeError,match='Complete'):migrate(layout)
    assert path.read_bytes()==before and not (core/'platform.json').exists()
    monkeypatch.setattr('deploy.jobs.Job.observe',lambda self:{'status':'completed'})
    original_environment=envpath.read_bytes();result=migrate(layout)
    assert result['status']=='completed' and migrate(layout)==result
    assert envpath.read_bytes()==original_environment and business.read_bytes()==b'original-signed-business-records'
    assert Path(result['preservedInitialInput']).read_bytes()==before
    current=read_json(path)
    assert current['input']['platform_config']==str(core/'platform.json')
    assert 'credentials' not in current and str(envpath) not in current['files']
    assert read_json(core/'platform.json')['admin']['password']=='original-manual-password'
    changed=read_json(core/'platform.json');changed['revision']=2
    (core/'platform.json').write_text(json.dumps(changed),encoding='utf-8')
    with pytest.raises(ValueError):migrate(layout)
    assert read_json(core/'platform.json')['revision']==2
