"""Owner-selected business document bounds and real original-host read evidence."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest
from a2c.codec import digest_bytes
from ioalab_jiuwen.process import ProcessHost
from ioalab_jiuwen.skills import FrozenSkills,documents,MAX_FILE
from ioalab_sdk.runtime.files import private_text
from ioalab_sdk.runtime.host import AgentDefinition,HostTurn,ToolBinding
from ioalab_sdk.runtime.protocol import RuntimeFault


def source(root):
    (root/'references').mkdir(parents=True)
    (root/'SKILL.md').write_text('# Owner business skill\nRead the actual criteria.\n',encoding='utf-8')
    (root/'references/criteria.md').write_text('The result must contain the agreed content.\n',encoding='utf-8')
    (root/'settings.local.json').write_text('{"password":"not-for-the-model"}')
    return root


def test_original_business_snapshot_survives_source_change_and_restart_but_detects_tampering(tmp_path):
    original=source(tmp_path/'source');work=tmp_path/'work'
    snapshot=FrozenSkills(work,('selected',),{'selected':original},'original-definition')
    before=snapshot.read({'skill':'selected'})
    (original/'SKILL.md').write_text('# New source is for a later interaction\n')
    resumed=FrozenSkills(work,('selected',),{},'original-definition',resume=True)
    assert resumed.read({'skill':'selected'})==before
    private_text(work/'skill-documents/selected/SKILL.md','# modified')
    with pytest.raises(RuntimeFault,match='bytes changed'):resumed.read({'skill':'selected'})
    assert (work/'skill-documents/selected/SKILL.md').read_text()=='# modified'
    with pytest.raises(RuntimeFault):FrozenSkills(work,('selected',),{'selected':original},'original-definition',resume=True)


@pytest.mark.parametrize('arguments',[{'skill':'other'}, {'skill':'selected','path':'settings.local.json'},
    {'skill':'selected','path':'../other/SKILL.md'},{'skill':'selected','path':'C:\\private\\secret.md'},
    {'skill':'selected','path':'references/.private.md'},{'skill':'selected','path':'references/missing.md'}])
def test_reader_cannot_leave_the_selected_frozen_documents(tmp_path,arguments):
    snapshot=FrozenSkills(tmp_path/'work',('selected',),{'selected':source(tmp_path/'source')},'definition')
    with pytest.raises(RuntimeFault):snapshot.read(arguments)
    assert 'not-for-the-model' not in str(snapshot.manifest)


def test_missing_resumed_snapshot_is_not_rebuilt_from_new_sources(tmp_path):
    root=source(tmp_path/'source');work=tmp_path/'work'
    with pytest.raises(RuntimeFault,match='original business documents'):FrozenSkills(work,('selected',),{'selected':root},'original',resume=True)
    assert not work.exists()
    (root/'SKILL.md').write_bytes(b'x'*(MAX_FILE+1))
    with pytest.raises(RuntimeFault,match='size bound'):documents(root)


def test_linked_source_references_are_rejected_before_any_document_escape(tmp_path):
    root=source(tmp_path/'source');outside=tmp_path/'outside';outside.mkdir();(outside/'secret.md').write_text('outside')
    link=root/'references/escape'
    if os.name=='nt':
        assert link.resolve().is_relative_to(tmp_path.resolve()) and outside.resolve().is_relative_to(tmp_path.resolve())
        result=subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(outside)],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode==0,result.stderr
    else:link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(RuntimeFault,match='links'):documents(root)
    assert (outside/'secret.md').read_text()=='outside'


def test_host_requires_actual_scoped_read_before_role_actions_and_reuses_original_snapshot(tmp_path,monkeypatch):
    root=source(tmp_path/'source');seen=[];requests=[]
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request);await callbacks['on_event']({'session_id':'actual-session'})
            assert 'skill_tool' not in request['agent']['tools']
            reader=next(item['name'] for item in request['host_tools'] if item['name'].startswith('ioa_read_business_skill'))
            business=request['host_tools'][0]['name']
            async def call(name,args):return await callbacks['on_tool_call']({'session_id':'actual-session','payload':{'name':name,'arguments':args,'call_id':'native-call'}})
            assert (await call(business,{}))['error']['code']=='read_business_skill'
            assert not seen or len(seen)==len(requests)-1
            with pytest.raises(RuntimeFault):await call(reader,{'skill':'other'})
            doc=await call(reader,{'skill':'selected','call_goal':'read actual instructions'})
            assert doc['content'].startswith('# Owner business skill')
            assert (await call(reader,{'skill':'selected','path':'references/criteria.md'}))['content'].startswith('The result')
            assert await call(business,{})=={'stillRequiresActualUser':True}
            return {'status':'completed','session_id':'actual-session'}
    definition=AgentDefinition('business','Use selected documents only',(ToolBinding('business.submit','A role action',{'type':'object'},lambda *_:None),),('selected',))
    for index in range(2):
        host=ProcessHost(['original-process'],tmp_path,skill_directories={'selected':root} if not index else {})
        host._capabilities={'available':True}
        monkeypatch.setattr(host,'_client',lambda **kwargs:Process())
        result=host.run(HostTurn('op','actor','iid',definition,'data',str(tmp_path/'work'),session_id='actual-session' if index else None,
            on_tool=lambda *args:seen.append(args) or {'stillRequiresActualUser':True}))
        assert result.status=='completed'
        (root/'SKILL.md').write_text('# changed current source')
    assert len(seen)==2 and requests[0]['agent']==requests[1]['agent']


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
def test_real_original_host_reads_exact_current_skill_documents_through_the_bound_reader(tmp_path):
    root=Path(__file__).resolve().parents[1]/'src/access/skills/assistant'
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],
        model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'),skill_directories={'ioalab-delegate':root})
    capabilities=host.probe()
    assert capabilities['available'],capabilities
    assert capabilities['skillSources']['ioalab-delegate']
    observed=[];events=[]
    definition=AgentDefinition('skill_read_probe',
        '只读检查：使用本单受限读取工具，实际读取已选技能的SKILL.md与references/delegation.md，再调用proof提交已读文件的两个digest。不得执行技能流程、脚本、配置修改或交易。收到recorded后结束。',
        (ToolBinding('proof','Record actual document digests',{'type':'object','properties':{'digests':{'type':'array','items':{'type':'string'},'minItems':2,'maxItems':2}},'required':['digests'],'additionalProperties':False},lambda *_:None),),('ioalab-delegate',))
    expected=[digest_bytes((root/path).read_bytes()) for path in ('SKILL.md','references/delegation.md')]
    def record(name,args,call):
        assert sorted(args['digests'])==sorted(expected)
        observed.append(args);return {'status':'recorded'}
    result=host.run(HostTurn('real-read','local-probe','real-read',definition,'仅读取实际主文件和委托流程页，然后记录其digest，不执行任何业务。',str(tmp_path/'work'),timeout_seconds=120,on_tool=record,on_event=events.append))
    assert result.status=='completed' and observed
    reads=[event['payload'] for event in events if event.get('event_type')=='chat.tool_result' and event.get('payload',{}).get('tool_name','').startswith('ioa_read_business_skill')]
    assert len(reads)>=2 and all(item['success'] for item in reads)
    if os.environ.get('IOALAB_REAL_SKILL_REPORT'):
        Path(os.environ['IOALAB_REAL_SKILL_REPORT']).write_text(json.dumps({'scope':'Original Jiuwen public Process SDK, current product skill bytes and bounded host reader; not native web menu or business execution',
            'model':host.capabilities()['model'],'sessionId':result.session_id,'mainAndFlowRead':True,'documentDigests':expected,'nativeGlobalReaderExposed':False},ensure_ascii=False,indent=2),encoding='utf-8')
