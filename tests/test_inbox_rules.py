"""Local rule drafts, real publication and original-result fault recovery."""
import copy
from pathlib import Path
import uuid

import httpx
import pytest

from ioalab_sdk.assistant.rules import Rules,parse
from ioalab_sdk.runtime import RuntimeFault
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,private_text
from ioalab_sdk.runtime.host import ToolBinding
from ioalab_sdk.runtime.resident import ResidentRuntime
from test_resident_runtime import configuration


def uid():return str(uuid.uuid4())


INPUT={'type':'object','properties':{'text':{'type':'string','maxLength':100}},'required':['text'],'additionalProperties':False}
OUTPUT={'type':'object','properties':{'status':{'type':'string'}},'required':['status'],'additionalProperties':False}


def declaration(**changes):
    return {'name':'travel.update','description':'接收亲友进展','inputSchema':INPUT,'outputSchema':OUTPUT,
            'allowedSenders':None,'tools':[],**changes}


@pytest.fixture
def rules(platform,tmp_path):
    config,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(config))
    tool=ToolBinding('local.notice','向本地用户展示信息',INPUT,lambda *args:pytest.fail('Preflight executed a business tool'),'notification')
    rules=Rules(runtime,runtime.identities[0],{'local.notice':tool})
    yield rules
    runtime.close()


def accepted(rules,pending):
    reference=pending['data']['confirmationRef']
    manager=rules.runtime.confirmations
    value=manager.get(reference)
    manager.decide(reference,value['bindingDigest'],'approve',authority=manager.user_channel(),source='isolated-test-user')
    return reference


def draft(rules,**changes):
    reference=rules.draft(uid(),declaration(**changes),'按约定理解来信，仅调用已配置工具并返回结构化结果。')['draftRef']
    return reference,rules.preview(reference)


def test_draft_validate_and_preview_never_publish_or_execute_tools(platform,rules):
    reference,value=draft(rules,tools=['local.notice'])
    assert value['valid'] and value['preview']['tools'][0]['effect']=='notification'
    assert '所有已登录账号'==value['preview']['allowedSenders']
    assert platform.db.one('SELECT 1 FROM message_interfaces WHERE owner=?',rules.account.account['id']) is None
    operation=uid()
    pending=rules.publish(operation,reference,value['contentDigest'],0,'model-confirmed')
    assert pending['status']=='waiting'
    assert platform.db.one('SELECT 1 FROM message_interfaces WHERE owner=?',rules.account.account['id']) is None
    confirmation=accepted(rules,pending)
    result=rules.publish(operation,reference,value['contentDigest'],0,confirmation)
    assert result['status']=='completed' and result['data']['revision']==1
    row=platform.db.one('SELECT definition FROM message_interfaces WHERE owner=?',rules.account.account['id'])
    assert 'local.notice' not in row['definition'] and '按约定理解来信' not in row['definition']
    original=rules.version('travel.update',1)
    private_text(rules._path(reference),rules._path(reference).read_text(encoding='utf-8').replace('按约定理解来信','已编辑的新草稿'))
    assert rules.version('travel.update',1)==original
    assert rules.publish(operation,reference,value['contentDigest'],0,confirmation)==result


def test_publication_lost_response_recovers_after_expired_confirmation_and_edited_draft(platform,rules,monkeypatch):
    reference,value=draft(rules)
    operation=uid()
    confirmation=accepted(rules,rules.publish(operation,reference,value['contentDigest'],0))
    original=rules.account.api
    def drop(method,path,body=None):
        result=original(method,path,body)
        if method=='POST' and path=='/messaging/interfaces':raise httpx.ReadTimeout('Committed reply lost')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(rules.account,'api',drop)
        with pytest.raises(httpx.ReadTimeout):rules.publish(operation,reference,value['contentDigest'],0,confirmation)
    with rules.store.transaction() as conn:conn.execute('UPDATE runtime_confirmations SET expires=0 WHERE id=?',(confirmation,))
    private_text(rules._path(reference),'This edited draft must not replace the original frozen version')
    result=rules.publish(operation,reference,value['contentDigest'],0,confirmation)
    assert result['status']=='completed' and result['data']['revision']==1
    assert rules.version('travel.update',1)['contentDigest']==value['contentDigest']
    assert platform.db.one('SELECT revision FROM message_interfaces WHERE owner=?',rules.account.account['id'])['revision']==1


def test_platform_commit_survives_local_mapping_failure(platform,rules):
    reference,value=draft(rules)
    operation=uid()
    confirmation=accepted(rules,rules.publish(operation,reference,value['contentDigest'],0))
    with rules.store.lock:
        rules.store.db.execute("CREATE TRIGGER fail_rule_map BEFORE INSERT ON runtime_checkpoints WHEN NEW.key LIKE 'version:%' BEGIN SELECT RAISE(ABORT,'mapping failed'); END")
    try:
        with pytest.raises(Exception,match='mapping failed'):rules.publish(operation,reference,value['contentDigest'],0,confirmation)
        assert platform.db.one('SELECT revision FROM message_interfaces WHERE owner=?',rules.account.account['id'])['revision']==1
    finally:
        with rules.store.lock:rules.store.db.execute('DROP TRIGGER fail_rule_map')
    assert rules.publish(operation,reference,value['contentDigest'],0,confirmation)['data']['revision']==1
    assert rules.version('travel.update',1)['instructions'].startswith('按约定')


def test_actual_preview_change_cannot_reuse_user_approval(rules):
    reference,value=draft(rules)
    operation=uid()
    confirmation=accepted(rules,rules.publish(operation,reference,value['contentDigest'],0))
    private_text(rules._path(reference),rules._path(reference).read_text(encoding='utf-8').replace('亲友进展','新增处理范围'))
    with pytest.raises(RuntimeFault,match='Draft changed'):rules.publish(operation,reference,value['contentDigest'],0,confirmation)
    changed=rules.preview(reference)
    with pytest.raises(RuntimeFault,match='confirmed object'):rules.publish(operation,reference,changed['contentDigest'],0,confirmation)


@pytest.mark.parametrize('change',[
    {'tools':['unconfigured.shell']},
    {'inputSchema':{'type':'object','$ref':'http://external.invalid'}},
    {'inputSchema':{'type':'string'}},
    {'allowedSenders':['missing@account.invalid']},
])
def test_unsafe_or_unconfigured_rule_is_rejected_without_side_effects(rules,change):
    with pytest.raises((RuntimeFault,httpx.HTTPStatusError)):
        reference=rules.draft(uid(),declaration(**change),'不执行任何配置或安装指令')['draftRef']
        rules.validate(reference)


def test_rule_yaml_aliases_and_custom_tags_are_not_executed():
    for text in ('---\nname: &x test\ndescription: *x\n---\nbody',
                 '---\nname: !!python/object/apply:os.system [anything]\n---\nbody'):
        with pytest.raises(RuntimeFault):parse(text)
