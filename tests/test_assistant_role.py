"""Real HTTP/WS resident roles; the host and business effect are explicit test doubles."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import uuid
from urllib.parse import urlsplit

import httpx
import pytest

from conftest import wait_for
from ioalab_sdk.assistant.role import AssistantRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.host import HostResult,ToolBinding
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_runtime import configuration


def uid():return str(uuid.uuid4())


INPUT={'type':'object','properties':{'slot':{'type':'string'}},'required':['slot'],'additionalProperties':False}
OUTPUT={'type':'object','properties':{'status':{'type':'string'},'suggested':{'type':'string'}},'required':['status'],'additionalProperties':False}


class ScriptedHost:
    """Not model evidence: exercise the exact host callback boundary deterministically."""
    def __init__(self,*,question=False):
        self.turns=[];self.cancelled=[];self.question=question

    def capabilities(self):return {'available':True,'testDouble':True}

    def cancel(self,operation):self.cancelled.append(operation);return True

    def run(self,turn):
        self.turns.append(turn)
        sid=turn.session_id or 'isolated-session-'+uid()
        turn.on_session(sid)
        if self.question:
            answer=turn.on_interaction({'request_id':'isolated-native-request','session_id':sid,
                'payload':{'interaction_id':'isolated-question','interaction':{'questions':[
                    {'question':'选择本次回复','options':[{'value':'continue','label':'继续'}]}]}}})
            assert answer[0]['selected_options']==['continue']
        incoming=json.loads(turn.input.split('\n',1)[1])['input']
        try:
            result=turn.on_tool('reserve_slot',incoming,'isolated-call')
            turn.on_tool('ioa_reply',{'output':result},'isolated-reply')
        except Exception:
            return HostResult('unknown',sid,{}, {'code':'test_tool_unknown'})
        return HostResult('completed',sid,{'testDouble':True})


@contextmanager
def runtime_at(platform,path,host=None,tools=()):
    path.mkdir()
    config,user=configuration(platform,path,service=False)
    module=AssistantRole(host,{tool.name:tool for tool in tools})
    runtime=ResidentRuntime(RuntimeConfig.load(config),modules=(module,),host_status=host.capabilities if host else None)
    runtime.start()
    try:
        wait_for(lambda:runtime.connection.connected)
        yield runtime,LocalClient(runtime.config.home),module,user
    finally:runtime.close()


def user_decision(runtime,reference,*,decision='approve',answers=None):
    """Use the browser-only channel, never the model/RPC token or a fake confirmed flag."""
    ui=runtime.user_ui
    with httpx.Client(base_url=ui.origin,trust_env=False) as browser:
        response=browser.post('/user/session',json={'token':urlsplit(ui.entry_url()).fragment},headers={'Origin':ui.origin})
        response.raise_for_status()
        value=runtime.confirmations.get(reference)
        body={'reference':reference,'bindingDigest':value['bindingDigest'],'decision':decision}
        if answers is not None:body['answers']=answers
        response=browser.post('/user/decision',json=body,headers={'Origin':ui.origin,'X-Ioa-Csrf':response.json()['csrf']})
        response.raise_for_status()
        return response.json()


def publish(runtime,client,sender,*,name='slot.request',output_schema=None,instructions=None):
    draft=client.call('assistant.rules.draft',{'declaration':{'name':name,'description':'本地测试接口',
        'inputSchema':INPUT,'outputSchema':output_schema or OUTPUT,'allowedSenders':[sender],'tools':['reserve_slot']},
        'instructions':instructions or '每条请求只处理input.slot指定的值。调用reserve_slot时slot必须原样使用，不得自行调整或尝试建议值。'
                       '仅调用一次业务工具，将该工具的完整结果原样作为ioa_reply的output返回。若冲突，只返回建议，等发送方再次请求。'})
    assert draft['status']=='completed',draft
    reference=draft['data']['draftRef']
    preview=client.call('assistant.rules.preview',{'draftRef':reference})
    assert preview['status']=='completed',preview
    operation=uid();args={'draftRef':reference,'contentDigest':preview['data']['contentDigest'],'expectedRevision':0}
    pending=client.call('assistant.rules.publish',args,operation_id=operation)
    assert pending['status']=='waiting',pending
    user_decision(runtime,pending['data']['confirmationRef'])
    result=client.call('assistant.rules.publish',args,operation_id=operation)
    assert result['status']=='completed',result
    return result


def send_and_read(client,args,*,operation=None):
    sent=client.call('assistant.messaging.send',args,operation_id=operation or uid())
    assert sent['status']=='accepted',sent
    result=client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef'],'waitSeconds':15})
    assert result['status']=='completed',result
    return sent,result['data']


def test_two_requests_share_receiver_uuid_and_real_host_session_and_do_not_repeat(platform,tmp_path):
    host=ScriptedHost();effects=[]
    def reserve(args,context):
        effects.append((args,context))
        return {'status':'conflict','suggested':'second'} if args['slot']=='first' else {'status':'reserved'}
    tool=ToolBinding('reserve_slot','测试时段处理器',INPUT,reserve,'reservation')
    with runtime_at(platform,tmp_path/'sender') as (sender,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,module,target):
            publish(receiver,inbox,user['username'])
            interfaces=client.call('assistant.messaging.interfaces',{'account':target['username']})
            assert len(interfaces['data'])==1
            interface=interfaces['data'][0]['interfaceRef'];operation=uid()
            first_args={'interfaceRef':interface,'input':{'slot':'first'}}
            sent,first=send_and_read(client,first_args,operation=operation)
            assert first['state']=='completed' and first['result']['output']=={'status':'conflict','suggested':'second'},first
            assert client.call('assistant.messaging.send',first_args,operation_id=operation)==sent
            changed=client.call('assistant.messaging.send',{**first_args,'input':{'slot':'changed'}},operation_id=operation)
            assert changed['status']=='rejected' and changed['error']['code']=='id_conflict'
            _,second=send_and_read(client,{'interactionRef':first['interactionRef'],'input':{'slot':'second'}})
            assert second['interactionRef']==first['interactionRef'] and second['result']['output']=={'status':'reserved'},second
            assert len(effects)==len(host.turns)==2
            assert host.turns[0].session_id is None and host.turns[1].session_id==effects[0][1]['sessionId']
            assert effects[1][1]['sessionId']==effects[0][1]['sessionId']
            assert effects[0][1]['interactionId']==effects[1][1]['interactionId']==first['interactionRef']
            wait_for(lambda:receiver.scheduler.status()['states'].get('completed')==2)
            assert receiver.status()['identities'][0]['executionReady']
            closed=client.call('assistant.messaging.close',{'interactionRef':first['interactionRef']})
            assert closed['status']=='accepted'
            wait_for(lambda:client.call('assistant.messaging.status',{'interactionRef':first['interactionRef']})['data']['closed'])
            third=client.call('assistant.messaging.send',{'interactionRef':first['interactionRef'],'input':{'slot':'third'}})
            assert third['status']=='rejected' and third['error']['code']=='closed' and len(effects)==2
            assert not sender.scheduler.status()['states']  # Results never become new inbound requests.


def test_unknown_business_effect_holds_session_and_is_not_replayed(platform,tmp_path):
    host=ScriptedHost();effects=[]
    def uncertain(args,context):
        effects.append(context)
        raise OSError('effect happened; result is unknown')
    tool=ToolBinding('reserve_slot','测试未知外部效果',INPUT,uncertain,'reservation')
    with runtime_at(platform,tmp_path/'sender') as (_,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,_,target):
            publish(receiver,inbox,user['username'])
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            _,result=send_and_read(client,{'interfaceRef':interface,'input':{'slot':'first'}})
            wait_for(lambda:receiver.scheduler.status()['states'].get('unknown')==1)
            # A durable unknown job excludes this interaction even if another request arrives.
            sent=client.call('assistant.messaging.send',{'interactionRef':result['interactionRef'],'input':{'slot':'second'}})
            assert sent['status']=='accepted'
            wait_for(lambda:receiver.scheduler.status()['states'].get('queued')==1)
            assert len(effects)==len(host.turns)==1 and host.cancelled
            assert result['state']=='unknown'


def test_native_question_waits_for_browser_answer_without_approving_another_action(platform,tmp_path):
    host=ScriptedHost(question=True)
    tool=ToolBinding('reserve_slot','无副作用测试',INPUT,lambda args,context:{'status':'answered'})
    with runtime_at(platform,tmp_path/'sender') as (_,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,_,target):
            publish(receiver,inbox,user['username'])
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}})
            wait_for(lambda:any(value['kind']=='question' for value in receiver.confirmations.list()))
            card=next(value for value in receiver.confirmations.list() if value['kind']=='question')
            assert inbox.call('confirmations.answer',{'reference':card['reference'],'confirmed':True})['status']=='rejected'
            assert client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data']['result'] is None
            user_decision(receiver,card['reference'],decision='answer',answers=[{'selected_options':['continue']}])
            result=client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef'],'waitSeconds':15})
            assert result['data']['result']['output']=={'status':'answered'}
            assert receiver.confirmations.get(card['reference'])['source'].startswith('local_user_ui:')


def test_committed_reply_lost_callback_reconciles_original_job_without_reexecuting_tool(platform,tmp_path,monkeypatch):
    host=ScriptedHost();effects=[]
    tool=ToolBinding('reserve_slot','可观察的测试效果',INPUT,lambda args,context:(effects.append(context) or {'status':'reserved'}),'reservation')
    with runtime_at(platform,tmp_path/'sender') as (_,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,module,target):
            publish(receiver,inbox,user['username'])
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            original=receiver.connection.wait;dropped=[]
            def drop(actor,kind,operation,**kwargs):
                result=original(actor,kind,operation,**kwargs)
                if not dropped and any(record.get('operationId')==operation for key,record in receiver.store.checkpoints(module.namespace).items() if key.startswith('reply:')):
                    dropped.append(operation)
                    raise RuntimeFault('unknown_result','Committed reply response lost')
                return result
            monkeypatch.setattr(receiver.connection,'wait',drop)
            _,result=send_and_read(client,{'interfaceRef':interface,'input':{'slot':'first'}})
            assert result['result']['output']=={'status':'reserved'}
            wait_for(lambda:receiver.scheduler.status()['states'].get('completed')==1)
            assert dropped and len(effects)==len(host.turns)==1


def test_unhandled_request_expiry_is_recovered_from_platform_without_model_polling(platform,tmp_path):
    tool=ToolBinding('reserve_slot','尚未启动的处理器',INPUT,lambda *_:pytest.fail('Host is absent; no effect is permitted'))
    with runtime_at(platform,tmp_path/'sender') as (_,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',tools=[tool]) as (receiver,inbox,_,target):
            publish(receiver,inbox,user['username'])
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'},'ttlSeconds':1})
            status=client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef'],'waitSeconds':3})
            assert status['data']['state']==status['data']['requestState']=='expired',status
            assert not receiver.status()['identities'][0]['executionReady'] and not receiver.scheduler.status()['states']


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
def test_real_jiuwen_process_handles_two_incoming_requests_in_one_session(platform,tmp_path):
    from ioalab_jiuwen.process import ProcessHost
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],
                     model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    effects=[]
    def reserve(args,context):
        effects.append({'input':args,'context':context})
        return {'status':'conflict','suggested':'second'} if args['slot']=='first' else {'status':'reserved','suggested':''}
    tool=ToolBinding('reserve_slot','检查指定slot并尝试处理，返回是否成功及建议；仅用本条请求的slot调用一次，不得自行调整',INPUT,reserve,'reservation')
    with runtime_at(platform,tmp_path/'sender') as (_,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,_,target):
            publish(receiver,inbox,user['username'],output_schema={**OUTPUT,'required':['status','suggested']})
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            first=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}})
            received={}
            def output(sent):
                value=client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data']
                received.update(value)
                return value['result'] is not None
            wait_for(lambda:output(first),timeout=180)
            assert received['result']['output']=={'status':'conflict','suggested':'second'},{'result':received,'effects':effects}
            interaction=received['interactionRef']
            second=client.call('assistant.messaging.send',{'interactionRef':interaction,'input':{'slot':'second'}})
            wait_for(lambda:output(second),timeout=180)
            assert received['result']['output']=={'status':'reserved','suggested':''},received
            assert received['interactionRef']==interaction
            assert [value['input'] for value in effects]==[{'slot':'first'},{'slot':'second'}]
            assert len({value['context']['sessionId'] for value in effects})==1
            wait_for(lambda:receiver.scheduler.status()['states'].get('completed')==2)
            evidence={'scope':'Real original Jiuwen via public Process SDK; real HTTP/WS resident roles; isolated scripted user confirmation through local browser channel; deterministic test tool; no mail/calendar/UI visibility claim',
                      'model':host.capabilities()['model'],'protocol':host.capabilities()['protocol'],
                      'interactionId':interaction,'sessionId':effects[0]['context']['sessionId'],
                      'requests':[first['data']['messageRef'],second['data']['messageRef']],
                      'results':[{'status':'conflict','suggested':'second'},received['result']['output']],
                      'toolEffects':len(effects),'jobs':receiver.scheduler.status()['states']}
            if os.environ.get('IOALAB_REAL_JIUWEN_REPORT'):
                Path(os.environ['IOALAB_REAL_JIUWEN_REPORT']).write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
def test_real_jiuwen_native_question_uses_same_child_and_browser_answer(platform,tmp_path):
    from ioalab_jiuwen.process import ProcessHost
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],
                     model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available']
    effects=[]
    tool=ToolBinding('reserve_slot','返回用户选定的值',INPUT,lambda args,context:(effects.append(context) or {'status':args['slot']}))
    instructions=('每条请求必须先调用原生ask_user，询问本地用户"本次回复中使用哪个标记？"，使用一个无选项的自由文本问题。'
                  '收到真实答复后将用户答复原样作为slot调用reserve_slot一次，然后把结果原样用ioa_reply返回。'
                  '不能在用户回答前调用业务工具，不能自行编造答复。此问题仅是内容澄清，不是批准SDK动作。')
    with runtime_at(platform,tmp_path/'sender') as (_,client,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,_,target):
            publish(receiver,inbox,user['username'],instructions=instructions)
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'requires-user-input'}})
            questions=lambda:[value for value in receiver.confirmations.list() if value['kind']=='question' and value['status']=='pending']
            wait_for(questions,timeout=120)
            card=questions()[0]
            assert len(card['questions'])==1 and not effects
            assert receiver.scheduler.status()['waitingForUser']==1
            user_decision(receiver,card['reference'],decision='answer',answers=[{'selected_options':[],'custom_input':'actual-user-marker'}])
            received={}
            def complete():
                received.update(client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data'])
                return received['result'] is not None
            wait_for(complete,timeout=120)
            assert received['result']['output']=={'status':'actual-user-marker'},received
            assert len(effects)==1 and effects[0]['sessionId']==card['binding']['subject']['sessionId']
            assert receiver.confirmations.get(card['reference'])['source'].startswith('local_user_ui:')
            if os.environ.get('IOALAB_REAL_JIUWEN_REPORT'):
                report=Path(os.environ['IOALAB_REAL_JIUWEN_REPORT']).with_name('assistant-native-question-evidence.json')
                report.write_text(json.dumps({'scope':'Real native ask_user in original Jiuwen Process SDK, isolated scripted browser user; not a real human or native web UI acceptance',
                    'model':host.capabilities()['model'],'sessionId':effects[0]['sessionId'],
                    'nativeRequestId':card['binding']['subject']['requestId'],'nativeInteractionId':card['binding']['subject']['interactionId'],
                    'questionCount':len(card['questions']),'userAnswer':'actual-user-marker','result':received['result']['output'],
                    'effectsBeforeAnswer':0,'toolEffects':len(effects),'modelSlotReleased':True},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
