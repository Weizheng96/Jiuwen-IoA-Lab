"""Published locator commands use only public app paths and never run the target."""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name',['ioalab-delegate','ioalab-provider'])
@pytest.mark.parametrize('mode',['data','home','missing'])
def test_published_location_command_respects_explicit_data_root_and_has_no_execution(tmp_path,name,mode):
    text=(ROOT/'src/access/skills/_shared/references/location.md').read_text(encoding='utf-8').replace('__IOALAB_SKILL_NAME__',name)
    commands=re.findall(r'python -X utf8 -c "([^"\n]+)"',text)
    assert len(commands)==1
    home=tmp_path/'九问用户';data=tmp_path/'指定数据'
    base=home/'.jiuwenswarm' if mode in ('home','missing') else data
    entry=base/('agent/workspace/skills/'+name+'/scripts/ioalab.py')
    entry.parent.mkdir(parents=True)
    entry.write_text('raise RuntimeError("The locator must not execute this file")',encoding='utf-8')
    env={**os.environ,'JIUWENSWARM_HOME':str(home)}
    env.pop('JIUWENSWARM_DATA_DIR',None)
    if mode=='data':env['JIUWENSWARM_DATA_DIR']=str(data)
    if mode=='missing':env['JIUWENSWARM_DATA_DIR']=str(tmp_path/'not-created')
    result=subprocess.run([sys.executable,'-X','utf8','-c',commands[0]],env=env,capture_output=True,encoding='utf-8')
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==('NOT_FOUND' if mode=='missing' else str(entry.resolve()))
    assert not (tmp_path/'not-created').exists()
