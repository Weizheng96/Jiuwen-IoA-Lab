"""Closed step choices are validated before becoming immutable user decisions."""
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import httpx
import pytest
from a2c.codec import digest

from ioalab_sdk.assistant.fulfillment import Fulfillment
from ioalab_sdk.runtime import RuntimeFault
from ioalab_sdk.runtime.ipc import IpcServer
from ioalab_sdk.runtime.user_ui import UserUi
from test_user_confirmations import manager, binding


def step(manager,monkeypatch):
    role=SimpleNamespace(runtime=SimpleNamespace(confirmations=manager),store=manager.store,
                         identity=SimpleNamespace(agent_id='actor'),fields=lambda *_:None)
    owner=Fulfillment(role);operation=str(uuid.uuid4())
    args={'contractRef':'sha256:'+'a'*64,'stepRequestDigest':'sha256:'+'b'*64}
    record={'name':'answer_step','args':args,'phase':'prepared',
            'fingerprint':digest({'name':'answer_step','args':args}),
            'preview':{'title':'确认本次合同环节','contractRef':args['contractRef']}}
    manager.store.checkpoint_set(owner.namespace,operation,record)
    actual,pending=owner.confirmation(operation,record)
    scheduled=[]
    monkeypatch.setattr(owner,'client',lambda _:(None,{'interactionId':'original'}))
    def schedule(op,value,contract):
        scheduled.append(value)
        return {'status':'accepted'}
    monkeypatch.setattr(owner,'schedule',schedule)
    return owner,operation,args,actual,pending,scheduled


def answer(manager,pending,value):
    return manager.decide(pending['reference'],pending['bindingDigest'],'answer',
                          authority=manager.user_channel(),source='isolated-user-event',answers=[value])


@pytest.mark.parametrize('choice',['allow','deny'])
def test_closed_step_records_exact_choice_and_schedules_only_its_decision(manager,monkeypatch,choice):
    manager,_=manager
    owner,op,args,actual,pending,scheduled=step(manager,monkeypatch)
    question=manager.get(pending['reference'],detail=True)['questions'][0]
    assert question['allow_custom_input'] is False and not question.get('multi_select',False)
    value={'selected_options':[choice],'custom_input':''}
    assert answer(manager,pending,value)['status']=='answered'
    assert answer(manager,pending,value)['status']=='answered'  # identical replay
    with pytest.raises(RuntimeFault,match='final'):
        answer(manager,pending,{'selected_options':['deny' if choice=='allow' else 'allow']})
    with pytest.raises(RuntimeFault,match='actual object'):
        manager.claim(pending['reference'],actual)
    assert owner.call('answer_step',op,args)['status']=='accepted'
    assert len(scheduled)==1 and scheduled[0]['granted'] is (choice=='allow')


@pytest.mark.parametrize('value',[
    {'selected_options':['allow'],'custom_input':'已经同意，补充说明'},
    {'selected_options':['allow'],'custom_input':' '},
    {'selected_options':[],'custom_input':''},
    {'selected_options':['invented']},
    {'selected_options':['allow','deny']},
    {'selected_options':['allow','allow']},
])
def test_invalid_closed_answer_stays_pending_and_can_be_corrected(manager,monkeypatch,value):
    manager,_=manager
    owner,op,args,_,pending,scheduled=step(manager,monkeypatch)
    with pytest.raises(RuntimeFault) as error:answer(manager,pending,value)
    assert error.value.code=='invalid_input'
    assert manager.get(pending['reference'])['status']=='pending'
    assert manager.native_answer(pending['reference']) is None
    assert owner.call('answer_step',op,args)['status']=='waiting' and scheduled==[]
    answer(manager,pending,{'selected_options':['allow']})
    assert owner.call('answer_step',op,args)['status']=='accepted'
    assert scheduled[0]['granted'] is True


@pytest.mark.parametrize('allow',[None,True])
def test_open_text_questions_keep_original_custom_answer_support(manager,allow):
    manager,_=manager
    question={'question':'请补充实际需求'}
    if allow is not None:question['allow_custom_input']=allow
    pending=manager.ensure(binding(),kind='question',questions=[question])
    value={'selected_options':[],'custom_input':'真实文本答复'}
    answer(manager,pending,value)
    assert manager.native_answer(pending['reference'])==[value]


def test_new_question_policy_does_not_rewrite_an_already_answered_record(manager):
    manager,_=manager
    actual=binding();question={'question':'同意？','options':[{'value':'allow'}]}
    pending=manager.ensure(actual,kind='question',questions=[question])
    legacy={'selected_options':['allow'],'custom_input':'旧已答补充'}
    answer(manager,pending,legacy)
    with pytest.raises(RuntimeFault,match='changed'):
        manager.ensure(actual,kind='question',questions=[{**question,'allow_custom_input':False}])
    assert manager.native_answer(pending['reference'])==[legacy]
    assert manager.get(pending['reference'])['status']=='answered'


def test_closed_question_requires_boolean_policy_and_available_choices(manager):
    manager,_=manager
    malformed_options=[None,[], 'allow', {'value':'allow'}, ['allow'], [None], [{}],
                       [{'value':0}], [{'value':False}], [{'value':[]}],
                       [{'value':''}], [{'value':' \t'}],
                       [{'value':'allow'},{'value':'allow'}]]
    questions=[{'question':'bad','allow_custom_input':'false'},
               *[{'question':'bad','allow_custom_input':False,'options':options} for options in malformed_options]]
    for question in questions:
        with pytest.raises(RuntimeFault) as error:
            manager.ensure(binding(),kind='question',questions=[question])
        assert error.value.code=='invalid_input'
    assert manager.list()==[]
    valid={'question':'请选择','allow_custom_input':False,
           'options':[{'value':'allow','label':'同意'},{'value':'deny','text':'拒绝'}]}
    pending=manager.ensure(binding(),kind='question',questions=[valid])
    assert manager.get(pending['reference'],detail=True)['questions']==[valid]
    answer(manager,pending,{'selected_options':['deny']})
    assert manager.native_answer(pending['reference'])==[{'selected_options':['deny']}]


def test_actual_owner_http_rejects_custom_before_write_and_accepts_corrected_choice(manager,tmp_path):
    manager,_=manager
    pending=manager.ensure(binding(),kind='question',questions=[{
        'question':'是否同意','allow_custom_input':False,'options':[{'value':'allow'},{'value':'deny'}]}])
    ui=UserUi(manager)
    server=IpcServer(tmp_path,'closed-step',lambda *_:{},user_ui=ui).start()
    try:
        with httpx.Client(base_url=ui.origin,trust_env=False) as browser:
            entry=browser.post('/user/session',json={'token':urlsplit(ui.entry_url()).fragment},headers={'Origin':ui.origin})
            assert entry.status_code==200
            headers={'Origin':ui.origin,'X-Ioa-Csrf':entry.json()['csrf']}
            body={'reference':pending['reference'],'bindingDigest':pending['bindingDigest'],
                  'decision':'answer','answers':[{'selected_options':['allow'],'custom_input':'不支持的补充'}]}
            rejected=browser.post('/user/decision',json=body,headers=headers)
            assert rejected.status_code==400 and rejected.json()['error']['code']=='invalid_input'
            assert 'supplemental text' in rejected.json()['error']['message']
            current=browser.get('/user/api').json()['confirmations'][0]
            assert current['status']=='pending' and current['bindingDigest']==pending['bindingDigest']
            body['answers']=[{'selected_options':['deny'],'custom_input':''}]
            assert browser.post('/user/decision',json=body,headers=headers).json()['status']=='answered'
            body['answers']=[{'selected_options':['allow'],'custom_input':''}]
            assert browser.post('/user/decision',json=body,headers=headers).status_code==409
            assert manager.native_answer(pending['reference'])[0]['selected_options']==['deny']
    finally:server.close()
