"""Opt-in original model nail production; backend evidence, not native onboarding."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

import pytest
from a2c import objects
from ioalab_sdk.runtime.files import private_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.templates import TERMS, template_from_definition
from conftest import wait_for
from test_assistant_role import user_decision
from test_resident_contracts import confirm, status
from test_resident_delegation import participants, request, terminal
from test_resident_fulfillment import invoke, operation, accept_preview


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_COMMAND'),reason='Opt-in original Jiuwen/model nail production')
def test_original_model_produces_nail_source_and_completes_signed_contract(platform,tmp_path):
    from ioalab_jiuwen.process import ProcessHost
    from scenario import nail_services
    definition=next(d for _,d in nail_services() if d['id']=='nail-mashangfei')
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    turns=[];events=[];original=host.run
    def event(value):
        if value.get('event_type') in ('chat.tool_call','chat.tool_result'):
            events.append(value)
            private_json(tmp_path/'model-events.json',events)
    def execute(turn):
        turns.append(turn)
        return original(replace(turn,on_event=event))
    host.run=execute
    def terms(_):
        template=template_from_definition(definition,packages=None,payment_provider=platform.payment_provider)
        value={key:value for key,value in template.items() if key in TERMS}
        value['pricing']={key:amount for key,amount in value['pricing'].items() if key!='paymentProviders'}
        return value
    business={'native_tools':{'write_file':'allow'},'instructions':
        '按请求及合同生产美甲预约微信小程序源码和可验核心，不使用预制交付件。'
        '报价用resources.outputVerifier作为booking的script验收器，报价为resources.quoteAmount，验收时长resources.acceptWithin。'
        '未覆盖微信实际注册、主体审核上线、微信客户端执行及真正多线程压力测试，报价时如实列明。'
        '生产时先请求合同允许的realname_registration环节授权；没有真实granted不得继续。此隔离演示不连接微信、不收集身份资料。'
        '交付booking.json：format为ioalab.booking；shop/open/close/capacity及两个services依合同逐项填写，服务顺序不变。'
        'allowCancel和idempotent为true；engineSource为纯Python BookingEngine(capacity)源码，'
        'reserve(request,slot)返回{id,slot}或满员None，list返回未取消记录，cancel(id)可重复调用；'
        '同request重试返回原预约且不多占名额，各slot独立计数，无外部依赖。'
        'miniProgram是实际微信源码文本映射，app.json的pages对应非空.js/.wxml文件，页面包含预约、列表、取消。'
        '用原生write_file把真实JSON写入输入artifactDirectory的booking.json。'
        '随后用files={"booking.json":{"artifactPath":"booking.json","mediaType":"application/json"}}调用provider.task.selftest；'
        '只有全部通过才以同一文件调用provider.task.deliver。失败先修生产内容，不能修改已签验收器。等待授权或交付后结束本轮。'}
    with participants(platform,tmp_path,agent_host=host,verifier_source=definition['verifier']['source'],
            verifier_reads=definition['verifier']['reads'],quote_amount=299,business=business,template_terms=terms) as (provider,assistant,host,template,task):
        platform.grant(assistant.account.account['id'],1000,by='admin',note='explicit isolated nail production virtual credit')
        started,_=request(assistant,provider,template,task,spec={'booking':definition['sample_spec']})
        quote_ref=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,quote_ref),timeout=180)
        quote=terminal(assistant,quote_ref);assert quote['status']=='completed',quote
        assert quote['data']['card']['derived']['total']==299,quote
        selected,_,_=confirm(assistant,quote['data']);ref=selected['data']['contractRef']
        def current():
            value=status(assistant,ref)
            return value['data'] if value and value['status']=='completed' else {}
        wait_for(lambda:current().get('formed'),timeout=180)
        key,_=invoke(assistant,ref,'start');wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:current().get('stepRequest'),timeout=180)
        step=current()['stepRequest']
        key,pending=invoke(assistant,ref,'answer_step',stepRequestDigest=objects.object_digest(step))
        user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        LocalClient(assistant.config.home).call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':objects.object_digest(step)},operation_id=key)
        wait_for(lambda:current().get('availablePeriods')==1,timeout=180)
        key,_=invoke(assistant,ref,'fetch',period=1);wait_for(lambda:operation(assistant,ref,key))
        files=operation(assistant,ref,key)['data']['files'];assert 'booking.json' in files,files
        key,pending=accept_preview(assistant,ref)
        preview=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']
        assert preview['report']['status']=='pass' and preview['amount']==299,preview
        user_decision(assistant,pending['data']['confirmationRef'])
        LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed'
        wait_for(lambda:current().get('closed'))
        actual=current();assert actual['payment']=='paid'
        assert len({turn.session_id for turn in turns if turn.session_id})==1
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
        assert sum(json.loads(turn.input)['phase']=='production' for turn in turns)>=2
        report={'scope':'Original public Process model produces actual booking.json; HTTP/WS/A2C/WASM/AP2; explicit isolated user confirmation driver',
            'model':host.capabilities()['model'],'contractRef':ref,'interactionId':actual['interactionId'],
            'verifierSourceSha256':hashlib.sha256(definition['verifier']['source'].encode()).hexdigest(),
            'files':files,'wasm':preview['report'],'contractAmount':299,'proposalFee':1,'payment':'paid','closed':True,'reputationCompleted':1,
            'limits':['One provider backend production; not native Skill import, registration, frontend confirmation or film',
                      'No proof of WeChat client execution, real registration, three-provider comparison or external mail']}
        if os.environ.get('IOALAB_REAL_NAIL_REPORT'):
            private_json(Path(os.environ['IOALAB_REAL_NAIL_REPORT']),report)
