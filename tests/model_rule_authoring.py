"""Opt-in owner rule authoring over Process SDK; no native webpage proof.

The original model writes every declaration and processing instruction.  This
bridge only relays the six public SDK operations and drives an isolated approval
after the first host run has ended with an actual pending confirmation.
"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import uuid

from a2c.codec import digest,digest_bytes
from ioalab_sdk.runtime.files import private_json
from ioalab_sdk.runtime.host import AgentDefinition,HostTurn,ToolBinding


ROOT=Path(__file__).resolve().parents[1]


def _schemas():
    empty={'type':'object','properties':{},'additionalProperties':False}
    reference={'type':'object','properties':{'draftRef':{'type':'string'}},
               'required':['draftRef'],'additionalProperties':False}
    declaration={'type':'object','properties':{
        'name':{'type':'string','pattern':'^[A-Za-z][A-Za-z0-9_.-]{0,63}$'},
        'description':{'type':'string','minLength':1,'maxLength':1000},
        'inputSchema':{'type':'object','description':'Explicit object JSON Schema using the SDK-supported subset.'},
        'outputSchema':{'type':'object','description':'Explicit object JSON Schema using the SDK-supported subset.'},
        'allowedSenders':{'type':['array','null'],'items':{'type':'string'},'maxItems':100,'uniqueItems':True},
        'enabled':{'type':'boolean'},
        'tools':{'type':'array','items':{'type':'string'},'maxItems':32,'uniqueItems':True}},
        'required':['name','description','inputSchema','outputSchema'],'additionalProperties':False}
    return {
        'assistant.tools.list':deepcopy(empty),
        'assistant.rules.draft':{'type':'object','properties':{'declaration':declaration,
            'instructions':{'type':'string','minLength':1}},'required':['declaration','instructions'],'additionalProperties':False},
        'assistant.rules.validate':deepcopy(reference),
        'assistant.rules.preview':deepcopy(reference),
        'assistant.rules.publish':{'type':'object','properties':{'draftRef':{'type':'string'},
            'contentDigest':{'type':'string','pattern':'^sha256:[0-9a-f]{64}$'},
            'expectedRevision':{'type':'integer','minimum':0},'confirmationRef':{'type':'string'}},
            'required':['draftRef','contentDigest','expectedRevision'],'additionalProperties':False},
        'assistant.rules.list':deepcopy(empty)}


def configure_with_original_model(receiver,inbox,host,allowed_account,workspace):
    """Return a private audit of model-authored rules and scripted user approval.

    ``receiver`` and ``inbox`` are the resident runtime and LocalClient yielded by
    ``test_assistant_role.runtime_at``; ``host`` is a probed original ProcessHost.
    ``workspace`` must be a fresh, isolated evidence directory outside SDK state.
    This function runs the model when called; importing this module does not.
    """
    from test_assistant_role import user_decision

    assert isinstance(allowed_account,str) and allowed_account
    assert host.capabilities().get('available'),host.capabilities()
    workspace=Path(workspace).resolve()
    evidence=workspace/'owner-rule-authoring.json'
    assert not evidence.exists(),'Preserve the original authoring evidence; use a fresh isolated workspace'
    flow_path=ROOT/'src/access/skills/assistant/references/inbox-rules.md'
    flow_bytes=flow_path.read_bytes();flow=flow_bytes.decode('utf-8')
    workflow=str(uuid.uuid4());schemas=_schemas()
    actor=next(identity.agent_id for identity in receiver.identities if identity.role=='assistant')
    request=(f'请只允许平台账号{allowed_account}预约会议，根据本机已配置的日程工具确定输入和回复格式并起草处理流程，'
             '每条来信只按它指定的原时间检查并调用一次预约工具，冲突只返回建议、等对方另发消息改约且不自行采用建议，'
             '不冲突时提交邀请并完整回复工具实际结果，先让我确认后再发布。')
    audit={'scope':'Original Process model authors owner rules from one natural-language request; isolated scripted local-browser approval',
           'workflowId':workflow,'model':host.capabilities().get('model'),'status':'running',
           'productFlow':str(flow_path.relative_to(ROOT)).replace('\\','/'),'productFlowDigest':digest_bytes(flow_bytes),
           'request':request,'sessionId':None,'sessions':[],'trace':[],'rounds':[],
           'limits':['Not a real human approval or native webpage Skill/menu acceptance',
                     'No calendar booking or external email is executed by this authoring helper']}
    phase='before_confirmation'

    def save():private_json(evidence,audit)

    def bind(session):
        assert isinstance(session,str) and session
        assert audit['sessionId'] in (None,session),'Original authoring Session changed'
        audit['sessionId']=session;audit['sessions'].append(session);save()

    def call(name,arguments,call_id):
        assert name in schemas and audit['sessionId'],'Only bound SDK tools are available'
        fingerprint=digest(arguments)
        operation=str(uuid.uuid5(uuid.NAMESPACE_URL,digest({'workflow':workflow,'operation':name,'argumentsDigest':fingerprint})))
        entry={'phase':phase,'name':name,'arguments':deepcopy(arguments),'argumentsDigest':fingerprint,
               'operationId':operation,'callId':call_id,'sessionId':audit['sessionId'],'result':None}
        audit['trace'].append(entry);save()
        try:
            # Preserve every model parameter; the SDK performs the actual validation.
            result=inbox.call(name,arguments,operation_id=operation)
            entry['result']=deepcopy(result)
            return result
        except Exception as exc:
            entry['errorType']=type(exc).__name__;raise
        finally:save()

    bindings=tuple(ToolBinding(name,'IoA Lab SDK operation '+name,schema,lambda *_:None)
                   for name,schema in schemas.items())
    definition=AgentDefinition('ioa_rule_author',
        '按用户需求和以下真实产品流程使用实际SDK工具，不编造工具能力、草稿或批准。'
        '首次publish返回waiting后结束本轮等待真实用户决定，不自行批准。'
        '收到实际批准通知后，原样重发该次publish的全部参数，不增删字段，再用rules.list核实已发布修订。\n'+flow,bindings)
    audit['definitionDigest']=digest({'name':definition.name,'instructions':definition.instructions,
                                      'tools':[tool.policy() for tool in definition.tools]})

    def run(text,session=None):
        result=host.run(HostTurn(workflow,actor,workflow,definition,text,str(workspace/'model'),
            timeout_seconds=300,session_id=session,on_session=bind,on_tool=call))
        audit['rounds'].append({'phase':phase,**asdict(result)});save()
        assert result.status=='completed' and result.session_id==audit['sessionId'],result
        return result

    save()
    try:
        run(request)
        before=list(audit['trace'])
        assert before and before[0]['name']=='assistant.tools.list','Model must discover actual local tool schemas first'
        pending=[item for item in before if item['name']=='assistant.rules.publish' and
                 (item.get('result') or {}).get('status')=='waiting']
        assert pending,'Model did not create an actual pending publication'
        assert not any(item['name']=='assistant.rules.publish' and (item.get('result') or {}).get('status')=='completed' for item in before)
        assert len({item['result']['data']['confirmationRef'] for item in pending})==1,'Expected one original approval object'
        original=pending[-1];parameters=original['arguments'];reference=original['result']['data']['confirmationRef']
        audit['waiting']=deepcopy(original)
        draft=next(item for item in before if item['name']=='assistant.rules.draft' and (item.get('result') or {}).get('status')=='completed'
                   and item['result']['data']['draftRef']==parameters['draftRef'])
        validations=[item for item in before if item['name']=='assistant.rules.validate' and (item.get('result') or {}).get('status')=='completed'
                     and item['arguments']['draftRef']==parameters['draftRef']]
        previews=[item for item in before if item['name']=='assistant.rules.preview' and (item.get('result') or {}).get('status')=='completed'
                  and item['arguments']['draftRef']==parameters['draftRef']]
        assert validations and previews,'Published draft must have actual validation and preview receipts'
        preview=previews[-1]['result']['data']
        assert preview['contentDigest']==parameters['contentDigest']
        assert preview['declaration']['allowedSenders']==[allowed_account]
        assert preview['declaration']['enabled'] and 'calendar.book' in preview['declaration']['tools']
        audit.update(draft=deepcopy(draft),preview=deepcopy(previews[-1]),validation=deepcopy(validations[-1]))
        confirmation=receiver.confirmations.get(reference,detail=True)
        assert confirmation['status']=='pending' and confirmation['operationId']==original['operationId']
        assert confirmation['stage']=='rules.publish' and confirmation['action']=='interface.publish'
        audit['confirmationBefore']=confirmation;save()
        decision=user_decision(receiver,reference)
        assert decision['status']=='approved' and decision['source'].startswith('local_user_ui:')
        audit['approval']={'performedBy':'isolated scripted local-browser user_decision','receipt':decision};save()
        phase='after_confirmation'
        run('本机可信用户通道已实际批准刚才待确认的发布，请按原操作原样恢复该次publish并核对真实发布结果。',audit['sessionId'])
        completed=[item for item in audit['trace'] if item['phase']==phase and item['name']=='assistant.rules.publish'
                   and (item.get('result') or {}).get('status')=='completed']
        assert completed,'Model did not complete publication after actual approval'
        published=completed[-1]
        assert published['operationId']==original['operationId'] and published['arguments']==parameters,'Publication must resume the original parameters and operation'
        lists=[item for item in audit['trace'] if item['phase']==phase and item['name']=='assistant.rules.list'
               and (item.get('result') or {}).get('status')=='completed']
        assert lists,'Model did not read back the published local rule revision'
        version=published['result']['data']
        assert version in lists[-1]['result']['data']['versions']
        actual=next(item for item in receiver.account.api('GET','/messaging/interfaces') if item['name']==version['name'])
        assert actual['revision']==version['revision'] and actual['contentDigest']==preview['contentDigest']
        assert actual['allowedSenders']==[allowed_account] and actual['enabled']
        assert all(actual[key]==preview['declaration'][key] for key in ('inputSchema','outputSchema'))
        assert 'instructions' not in actual,'Private processing instructions must stay local'
        audit.update(status='completed',published=deepcopy(published),rules=deepcopy(lists[-1]),
                     interface=actual,confirmationAfter=receiver.confirmations.get(reference),
                     toolCalls=[item['name'] for item in audit['trace']],evidenceFile=str(evidence))
        save()
        return audit
    except BaseException as exc:
        audit['status']='failed';audit['failureType']=type(exc).__name__;save();raise
