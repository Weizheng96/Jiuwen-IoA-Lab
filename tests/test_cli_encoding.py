"""CLI results remain UTF-8 with Chinese identities under isolated/ascii startup."""
import json
import os
import subprocess
import sys

from ioalab_sdk.runtime.files import private_json


def test_chinese_validation_is_utf8_without_terminal_or_environment_assumptions(tmp_path):
    config=tmp_path/'中文配置.json'
    private_json(config,{'platform_url':'http://127.0.0.1:8000','username':'public-account','password':'private-test-value',
                        'data_dir':str(tmp_path/'never-created'),'identities':[{'key':'personal','role':'assistant','name':'中文个人助手'}]})
    result=subprocess.run([sys.executable,'-I','-m','ioalab_sdk.access','validate','--config',str(config)],
        cwd=tmp_path,env={**os.environ,'PYTHONIOENCODING':'ascii','PYTHONUTF8':'0'},capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')
    value=json.loads(result.stdout.decode('utf-8'))
    assert value['data']['identities'][0]['name']=='中文个人助手'
    assert b'private-test-value' not in result.stdout and not (tmp_path/'never-created').exists()
