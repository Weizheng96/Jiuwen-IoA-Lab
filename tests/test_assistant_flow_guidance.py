"""Composition recovery reuses the actual task and authorization child IDs."""
from types import SimpleNamespace
import threading

import pytest

from ioalab_sdk.assistant.flow import begin, guidance
from ioalab_sdk.assistant.role import AssistantRole
from ioalab_sdk.runtime import RuntimeStore, RuntimeFault


def test_begin_reuses_children_across_recreation_and_rejects_changed_input(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    prepared=[];authorized=[];approved=[False]
    def prepare(operation,args):
        prepared.append(operation)
        return {'status':'completed','data':{'taskRef':'original-task'}}
    def authorize(operation,args):
        authorized.append((operation,args))
        return {'status':'completed' if approved[0] else 'waiting','data':{'confirmationRef':'original-confirmation'}}
    def role():return SimpleNamespace(fields=AssistantRole.fields,lock=threading.RLock(),store=store,
        identity=SimpleNamespace(agent_id='actor'),delegation=SimpleNamespace(prepare=prepare,authorize=authorize))
    args={'purpose':'build a site','budget':10,'proposalFeeLimit':1}
    try:
        assert begin(role(),'parent',args)['status']=='waiting'
        approved[0]=True
        assert begin(role(),'parent',args)['status']=='completed'
        assert len(prepared)==1 and authorized[0]==authorized[1]
        assert authorized[0][1]=={'taskRef':'original-task'}
        with pytest.raises(RuntimeFault,match='changed'):begin(role(),'parent',{**args,'budget':20})
    finally:store.close()


def test_guidance_waits_for_original_and_unknown_does_not_restart():
    args={'quoteRef':'q'}
    result=guidance('delegation.confirm','original',args,{'status':'waiting','data':{'confirmationRef':'c'}})
    assert result['nextActions'][1]=={'operation':'assistant.delegation.confirm','arguments':args,
        'resumeOriginal':True,'operationId':'original'}
    for status in ('unknown','failed','rejected'):
        assert guidance('delegation.confirm','original',args,{'status':status,'data':{'contractRef':'x'}})['nextActions']==[]
    result=guidance('delegation.compare','read',{}, {'status':'accepted','data':{'reference':{'kind':'job','id':'actual'}}})
    assert result['nextActions'][0]['arguments']=={'reference':{'kind':'job','id':'actual'}}


@pytest.mark.parametrize('data', [[{'interfaceRef':'real'}], 'real text', None])
def test_guidance_preserves_nonobject_results(data):
    value=guidance('messaging.interfaces','read',{}, {'status':'completed','data':data})
    assert value=={'status':'completed','data':data,'nextActions':[]}
