"""Actual original bytes/read gate through Store/P20; native boundary is a fixture."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from a2c.codec import digest,digest_bytes
from ioalab_sdk.runtime import (BindingAck,ExecutionResources,ResourceGrant,HostInput,
    HostRegistration,HostRegistry,HostResource,HostToolCall,MachineTerminal,RuntimeFault,
    RuntimeStore,SubmissionEvent,SubmissionResourcePort,SubmissionResult,freeze_definition)
from ioalab_sdk.runtime.host import AgentDefinition,ToolBinding
from ioalab_sdk.runtime.host_submission import execution_snapshot
from ioalab_sdk.runtime.professional_execution import ProfessionalExecution,READER_NAME
from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool


@pytest.fixture
def original(tmp_path):
    source=tmp_path/'selected';(source/'references').mkdir(parents=True)
    (source/'SKILL.md').write_bytes(b'# Original owner professional instructions\n')
    (source/'references/criteria.md').write_bytes(b'The result must preserve actual criteria.\n')
    (source/'settings.local.json').write_text('{"password":"never-return"}')
    binding={'actor':'actor','business':'business','revision':'1'};workspace=tmp_path/'definition'
    definition=freeze_definition(workspace,reference='profile:original',version='1',
        instructions='Follow original selected professional documents',skill_names=('selected',),
        sources={'selected':source},binding=binding)
    period={'actor':'actor','business':'business','period':2}
    material=HostResource('period-material','input.txt','text/plain',digest_bytes(b'period two'),digest(period),'platform:original')
    resources=ExecutionResources(binding=period,operation_id='operation',output_root=tmp_path/'outputs',
        grants=(ResourceGrant(material,lambda:b'period two'),),authorize=lambda *_:None)
    return SimpleNamespace(source=source,binding=binding,workspace=workspace,definition=definition,
        material=material,resources=resources)


def compose(original,*,authorize=lambda ref:None):
    return ProfessionalExecution(definition=original.definition,business_binding=original.binding,
        workspace=original.workspace,tools=(
            # The role owns this declaration and its effects; the reader is only technical.
            original.tool,),authorize_definition=authorize,period_resources=original.resources,
        material_resources=(original.material,))


@pytest.mark.parametrize('profile',['personal','provider'])
def test_original_role_snapshot_actual_reads_gate_business_via_p20(original,tmp_path,profile):
    identity=SimpleNamespace(agent_id='actor',profile=profile)
    role=AgentDefinition('original-role',original.definition.instructions,
        (ToolBinding('business.submit','Original SDK role action',{'type':'object'},lambda *_:None),),('selected',))
    business_digest=digest({'original':'role authorization and tools'})
    execution=execution_snapshot(identity,'business','original-job',role,definition_digest=business_digest,
        version='1',workspace=str(original.workspace),host_definition=original.definition)
    original.tool=execution.tools[0];composition=compose(original);effects=[];reads=[]
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    port=SubmissionResourcePort()
    class NativeBoundary:
        registration=HostRegistration('explicit-native-boundary-fixture','1')
        def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            receipt=SubmissionResult(op,input.attempt.nonce,'running',session_id or 'native-session','request-'+op,'accepted')
            assert on_event(SubmissionEvent('session','session',receipt))==BindingAck(receipt.session_id,receipt.request_id)
            reader,sink=port.for_host('native')(op,input.attempt)
            # Transported bytes alone are not evidence that the model read its instructions.
            assert reader(execution.definition.resources[0]).startswith(b'# Original')
            def call(ref,name,args):
                return on_tool(HostToolCall(op,input.attempt.nonce,receipt.session_id,receipt.request_id,ref,name,args))
            blocked=call('before','business.submit',{})
            assert blocked.error.code=='read_business_skill' and not effects
            rejected=call('foreign',READER_NAME,{'skill':'other'})
            assert rejected.error.code=='forbidden' and not effects
            main=call('main',READER_NAME,{'skill':'selected'})
            criteria=call('criteria',READER_NAME,{'skill':'selected','path':'references/criteria.md'})
            assert main.error is None and main.value['content']=='# Original owner professional instructions\n'
            assert main.value['digest']==digest_bytes(main.value['content'].encode())
            assert criteria.value['content']=='The result must preserve actual criteria.\n'
            assert criteria.value['digest']==digest_bytes(criteria.value['content'].encode())
            reads.extend((main.value,criteria.value))
            result=call('effect','business.submit',{})
            assert result.value=={'requiresRealUserApproval':True}
            assert call('effect','business.submit',{})==result and len(effects)==1
            assert reader(original.material)==b'period two'
            output=sink('actual.txt','text/plain',b'actual output',digest_bytes(b'actual output'))
            return replace(receipt,state='completed',resources=(output,),terminal=MachineTerminal(
                op,input.attempt.nonce,receipt.session_id,receipt.request_id,'completed','fixture-machine-end'))
    registry=HostRegistry({'native':NativeBoundary()},'native');registry.bind(store,identities=[identity])
    registry.callback_pool=CallbackPool(1);port.bind(registry._resources_for)
    callback=composition.wrap(lambda call:effects.append(call) or {'requiresRealUserApproval':True})
    try:
        snapshot=replace(execution,tools=composition.tools)
        result=registry.submit('actor','business','operation',HostInput('original',resources=(original.material,)),
            execution=snapshot,on_tool=callback,resource_io=composition.resource_io)
        assert result.state=='completed' and result.terminal and len(reads)==2
        assert original.resources.read_output(result.resources[0])==b'actual output'
        assert role.skills==('selected',) and len(role.tools)==1
        assert execution.definition==snapshot.definition==original.definition
        assert registry.submit('actor','business','operation',HostInput('original',resources=(original.material,)),
            execution=snapshot,on_tool=callback,resource_io=composition.resource_io)==result
        assert len(effects)==1
    finally:registry.callback_pool.close();store.close()


def test_resume_reads_original_snapshot_and_requires_fresh_actual_read(original):
    original.tool=SimpleNamespace(name='business.submit');composition=compose(original);effects=[]
    invoke=composition.wrap(lambda call:effects.append(call) or {'approved':False})
    def call(name,args):return HostToolCall('op','attempt','session','request','id',name,args)
    before=invoke(call(READER_NAME,{'skill':'selected'}))
    (original.source/'SKILL.md').write_text('# Changed source for next version')
    resumed=freeze_definition(original.workspace,reference='profile:original',version='1',
        instructions=original.definition.instructions,skill_names=('selected',),sources={},
        binding=original.binding,resume=True,expected_digest=original.definition.digest)
    assert resumed==original.definition
    resumed_composition=compose(original)
    resumed_composition.read_resource(resumed.resources[0])
    new_callback=resumed_composition.wrap(lambda c:effects.append(c) or {'approved':False})
    with pytest.raises(RuntimeFault) as error:new_callback(call('business.submit',{}))
    assert error.value.code=='read_business_skill' and not effects
    assert new_callback(call(READER_NAME,{'skill':'selected'}))==before
    assert new_callback(call('business.submit',{}))=={'approved':False} and len(effects)==1


def test_original_authorization_or_bytes_failure_does_not_grant_read(original):
    original.tool=SimpleNamespace(name='business.submit');allowed=[];effects=[]
    def authorize(ref):
        if not allowed:raise RuntimeFault('forbidden','Original role material authorization is pending')
    composition=compose(original,authorize=authorize)
    callback=composition.wrap(lambda call:effects.append(call))
    main=HostToolCall('op','attempt','session','request','main',READER_NAME,{'skill':'selected'})
    with pytest.raises(RuntimeFault) as error:callback(main)
    assert error.value.code=='forbidden'
    allowed.append(True)
    (original.workspace/'skill-documents/selected/SKILL.md').write_text('# tampered snapshot')
    with pytest.raises(RuntimeFault) as error:callback(main)
    assert error.value.code=='identity_conflict'
    with pytest.raises(RuntimeFault) as error:callback(replace(main,name='business.submit',arguments={}))
    assert error.value.code=='read_business_skill' and not effects


def test_exact_original_refs_do_not_expand_to_other_scope_or_paths(original):
    original.tool=SimpleNamespace(name='business.submit');composition=compose(original)
    ref=original.definition.resources[0]
    for changed in (replace(ref,access_scope=original.material.access_scope),replace(ref,locator='file:C:/secret.md')):
        with pytest.raises(RuntimeFault) as error:composition.read_resource(changed)
        assert error.value.code=='forbidden'
    callback=composition.wrap(lambda call:None)
    for args in ({'skill':'selected','path':'../SKILL.md'},{'skill':'selected','path':'settings.local.json'},
                 {'resource_id':original.material.resource_id}):
        with pytest.raises(RuntimeFault) as error:callback(HostToolCall('op','attempt','session','request','id',READER_NAME,args))
        assert error.value.code=='forbidden'


def test_standalone_document_is_required_and_read_by_original_ref(original,tmp_path):
    document=tmp_path/'standalone.md';document.write_bytes(b'# Selected standalone Rules document\n')
    name='selected/references/only.md'
    definition=freeze_definition(tmp_path/'standalone-snapshot',reference='Rules:1',version='1',instructions='',
        skill_names=(name,),sources={name:document},binding=original.binding)
    composition=ProfessionalExecution(definition=definition,business_binding=original.binding,
        workspace=tmp_path/'standalone-snapshot',tools=(SimpleNamespace(name='business.submit'),),
        authorize_definition=lambda ref:None,period_resources=original.resources)
    callback=composition.wrap(lambda call:{'requiresRealUserApproval':True})
    call=HostToolCall('op','attempt','session','request','id','business.submit',{})
    with pytest.raises(RuntimeFault) as error:callback(call)
    assert error.value.code=='read_business_skill'
    result=callback(replace(call,name=READER_NAME,arguments={'resource_id':definition.resources[0].resource_id}))
    assert result['content']=='# Selected standalone Rules document\n' and result['digest']==digest_bytes(document.read_bytes())
    assert 'skill' not in result and 'path' not in result
    assert callback(call)=={'requiresRealUserApproval':True}
