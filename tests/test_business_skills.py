"""Owner-selected business document bounds and real original-host read evidence."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest
from a2c.codec import digest,digest_bytes
from ioalab_jiuwen.process import ProcessHost
from ioalab_sdk.runtime import (FrozenSkills,documents,ExecutionResources,HostInput,HostRegistry,
    RuntimeStore,SubmissionResourcePort,freeze_definition)
from ioalab_sdk.runtime.business_skills import MAX_FILE
from ioalab_sdk.runtime.files import private_text
from ioalab_sdk.runtime.host import AgentDefinition,ToolBinding
from ioalab_sdk.runtime.host_submission import execution_snapshot
from ioalab_sdk.runtime.professional_execution import ProfessionalExecution
from ioalab_sdk.runtime.protocol import RuntimeFault,stamp
from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool


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
    expected_main=digest_bytes((root/'SKILL.md').read_bytes())
    expected_reference=digest_bytes((root/'references/criteria.md').read_bytes())
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            await callbacks['on_event']({'type':'event','request_id':request['request_id'],'session_id':'actual-session'})
            assert 'skill_tool' not in request['agent']['tools']
            reader=next(item['name'] for item in request['host_tools'] if item['name'].startswith('ioa_read_business_skill'))
            business=request['host_tools'][0]['name']
            sequence=0
            async def call(name,args):
                nonlocal sequence
                sequence+=1
                return await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'actual-session',
                    'payload':{'name':name,'arguments':args,'call_id':'native-call-'+str(sequence)}})
            assert (await call(business,{}))['error']['code']=='read_business_skill'
            assert not seen or len(seen)==len(requests)-1
            assert (await call(reader,{'skill':'other'}))['error']['code']=='forbidden'
            doc=await call(reader,{'skill':'selected','call_goal':'read actual instructions'})
            assert doc['content'].startswith('# Owner business skill')
            assert doc['digest']==expected_main
            criteria=await call(reader,{'skill':'selected','path':'references/criteria.md'})
            assert criteria['content'].startswith('The result')
            assert criteria['digest']==expected_reference
            assert await call(business,{})=={'stillRequiresActualUser':True}
            return {'type':'result','status':'completed','request_id':request['request_id'],'session_id':'actual-session','exit_code':0}
    definition=AgentDefinition('business','Use selected documents only',(ToolBinding('business.submit','A role action',{'type':'object'},lambda *_:None),),('selected',))
    binding={'actor':'actor','business':'iid','revision':'1'};workspace=tmp_path/'work'
    identity=SimpleNamespace(agent_id='actor',profile='personal')
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');pool=CallbackPool(1)
    snapshot=None
    try:
        for index in range(2):
            snapshot=freeze_definition(workspace,reference='business',version='1',instructions=definition.instructions,
                skill_names=definition.skills,sources={'selected':root} if not index else {},binding=binding,
                resume=bool(index),expected_digest=snapshot.digest if snapshot else None)
            port=SubmissionResourcePort()
            host=ProcessHost(['original-process'],tmp_path,resource_factory=port.for_host('process'))
            monkeypatch.setattr(host,'_client',lambda **kwargs:Process())
            registry=HostRegistry({'process':host},'process');registry.bind(store,identities=[identity]);registry.callback_pool=pool
            port.bind(registry._resources_for)
            execution=execution_snapshot(identity,'iid','job-'+str(index),definition,definition_digest=digest({'business':'original'}),
                version='1',workspace=str(workspace),host_definition=snapshot)
            resources=ExecutionResources(binding={'actor':'actor','business':'iid','period':index},operation_id='op-'+str(index),
                output_root=tmp_path/('output-'+str(index)),authorize=lambda *_:None)
            professional=ProfessionalExecution(definition=snapshot,business_binding=binding,workspace=workspace,tools=execution.tools,
                authorize_definition=lambda ref:None,period_resources=resources)
            result=registry.submit('actor','iid','op-'+str(index),HostInput('data'),execution=replace(execution,tools=professional.tools),
                on_tool=professional.wrap(lambda call:seen.append(call) or {'stillRequiresActualUser':True}),resource_io=professional.resource_io)
            assert result.state=='completed' and result.terminal is not None,result
            (root/'SKILL.md').write_text('# changed current source')
    finally:pool.close();store.close()
    assert len(seen)==2 and requests[0]['agent']==requests[1]['agent']


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
def test_real_original_host_reads_exact_current_skill_documents_through_the_bound_reader(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[1]/'src/client/skills/assistant'
    port=SubmissionResourcePort()
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],
        model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'),resource_factory=port.for_host('process'))
    observed=[];events=[]
    definition=AgentDefinition('skill_read_probe',
        '只读检查：使用本单受限读取工具，实际读取已选技能的SKILL.md与references/delegation.md，再调用proof提交已读文件的两个digest。不得执行技能流程、脚本、配置修改或交易。收到recorded后结束。',
        (ToolBinding('proof','Record actual document digests',{'type':'object','properties':{'digests':{'type':'array','items':{'type':'string'},'minItems':2,'maxItems':2}},'required':['digests'],'additionalProperties':False},lambda *_:None),),('ioalab-delegate',))
    expected=[digest_bytes((root/path).read_bytes()) for path in ('SKILL.md','references/delegation.md')]
    # Transparent tracing of the real public Process SDK, without replacing its model/transport.
    original_client=host._client
    class TracedClient:
        def __init__(self,client):self.client=client
        async def run(self,request,**callbacks):
            original_event=callbacks.get('on_event')
            async def event(value):
                events.append(value)
                if original_event:await original_event(value)
            return await self.client.run(request,**{**callbacks,'on_event':event})
    monkeypatch.setattr(host,'_client',lambda **kwargs:TracedClient(original_client(**kwargs)))
    def record(call):
        assert sorted(call.arguments['digests'])==sorted(expected)
        observed.append(call.arguments);return {'status':'recorded'}
    binding={'actor':'local-probe','business':'real-read','revision':'1'};workspace=tmp_path/'work'
    snapshot=freeze_definition(workspace,reference='skill-read-probe',version='1',instructions=definition.instructions,
        skill_names=definition.skills,sources={'ioalab-delegate':root},binding=binding)
    identity=SimpleNamespace(agent_id='local-probe',profile='personal')
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['local-probe'],owner_id='owner');pool=CallbackPool(1)
    registry=HostRegistry({'process':host},'process');registry.bind(store,identities=[identity]);registry.callback_pool=pool
    port.bind(registry._resources_for)
    execution=execution_snapshot(identity,'real-read','real-read-job',definition,definition_digest=digest({'probe':'readonly'}),
        version='1',workspace=str(workspace),host_definition=snapshot,deadline=stamp(time.time()+120))
    resources=ExecutionResources(binding=binding,operation_id='real-read',output_root=tmp_path/'outputs',authorize=lambda *_:None)
    professional=ProfessionalExecution(definition=snapshot,business_binding=binding,workspace=workspace,tools=execution.tools,
        authorize_definition=lambda ref:None,period_resources=resources)
    try:
        result=registry.submit('local-probe','real-read','real-read',HostInput('仅读取实际主文件和委托流程页，然后记录其digest，不执行任何业务。'),
            execution=replace(execution,tools=professional.tools),on_tool=professional.wrap(record),resource_io=professional.resource_io)
        assert result.state=='completed' and result.terminal is not None and observed,result
    finally:pool.close();store.close()
    reads=[event['payload'] for event in events if event.get('event_type')=='chat.tool_result' and event.get('payload',{}).get('tool_name','').startswith('ioa_read_business_skill')]
    assert len(reads)>=2 and all(item['success'] for item in reads)
    if os.environ.get('IOALAB_REAL_SKILL_REPORT'):
        Path(os.environ['IOALAB_REAL_SKILL_REPORT']).write_text(json.dumps({'scope':'Original Jiuwen public Process SDK, current product skill bytes and bounded host reader; not native web menu or business execution',
            'model':host.model,'sessionId':result.session_id,'mainAndFlowRead':True,'documentDigests':expected,'nativeGlobalReaderExposed':False},ensure_ascii=False,indent=2),encoding='utf-8')
