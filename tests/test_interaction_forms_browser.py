"""Isolated real-browser checks; not acceptance of any third-party agent frontend."""
import json
import os

import pytest

from conftest import wait_for
from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.runtime import ClientOperations, ResidentRuntime, RuntimeConfig, form_page
from test_interaction_forms import rule_values
from test_resident_runtime import configuration

playwright = pytest.importorskip('playwright.sync_api', reason='Run explicitly with the browser-test environment')
pytestmark = pytest.mark.skipif(os.environ.get('IOA_BROWSER_TESTS') != '1', reason='Explicit isolated Chromium check')


def test_account_page_registers_without_exposing_generated_password(platform, tmp_path):
    from ioalab_sdk.runtime.onboarding import Onboarding
    from ioalab_sdk.runtime.user_ui import UserUi
    from ioalab_sdk.runtime.ipc import IpcServer
    onboarding = Onboarding(tmp_path / 'settings.json')
    onboarding.home.mkdir(parents=True)
    ui = UserUi(onboarding=onboarding)
    server = IpcServer(onboarding.home, 'onboarding-browser', lambda *_: {}, user_ui=ui).start()
    try:
        with playwright.sync_playwright() as manager:
            browser = manager.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1080, 'height': 900})
            page.goto(ui.entry_url())
            page.get_by_label('平台地址').fill(platform.s.base_url)
            page.get_by_label('接入方式').select_option('register')
            page.get_by_role('button', name='保存并接入', exact=True).click()
            playwright.expect(page.locator('#message')).to_contain_text('账号已就绪')
            page.get_by_role('button', name='刷新状态', exact=True).click()
            playwright.expect(page.locator('#configuration')).to_be_visible()
            assert not RuntimeConfig.load(onboarding.path).password
            playwright.expect(page.locator('#secret')).to_be_hidden()
            assert not page.evaluate('location.hash')
            page.get_by_role('button', name='一次性取回注册密码', exact=True).click()
            playwright.expect(page.locator('#secret')).to_be_visible()
            secret=page.locator('#secret').inner_text().split('密码：')[1]
            assert len(secret)>=24
            page.get_by_role('button', name='隐藏密码', exact=True).click()
            assert secret not in page.content()
            page.get_by_role('button',name='查看设备列表',exact=True).click()
            playwright.expect(page.locator('#deviceList')).to_contain_text('当前设备')
            page.on('dialog',lambda dialog:dialog.accept())
            page.get_by_role('button',name='退出当前设备',exact=True).click()
            playwright.expect(page.locator('#deviceMessage')).to_contain_text('已撤销')
            page.get_by_label('会话到期或撤销后重新登录').fill(secret)
            page.get_by_role('button',name='登录当前设备',exact=True).click()
            playwright.expect(page.locator('#deviceMessage')).to_contain_text('登录成功')
            assert secret not in page.content()
            page.screenshot(path=str(tmp_path / 'account-ready.png'), full_page=True)
            browser.close()
    finally: server.close()


def test_browser_preserves_edits_across_conflicts_preview_failure_and_unknown_submit(platform, tmp_path, monkeypatch):
    path, _ = configuration(platform, tmp_path, service=False)
    launches = []
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open', lambda url: launches.append(url) or True)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)) as runtime:
        wait_for(lambda: runtime.connection.connected)
        api = ClientOperations(path)
        form = api.call('runtime.forms.open', {'kind': 'messaging.interface', 'values': rule_values()})['data']
        api.call('runtime.forms.open_ui', {'reference': form['reference']})
        with playwright.sync_playwright() as manager:
            browser = manager.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1080, 'height': 900})
                page.goto(launches[-1])
                playwright.expect(page.get_by_label('用途说明')).to_be_visible()
                api.call('runtime.forms.update', {'reference': form['reference'], 'revision': 0,
                    'values': {**rule_values(), 'description': '另一入口已保存的说明'}})
                page.get_by_label('用途说明').fill('本页未保存的说明')
                page.get_by_role('button', name='保存草稿', exact=True).click()
                playwright.expect(page.get_by_role('alert')).to_contain_text('revision changed')
                playwright.expect(page.get_by_label('用途说明')).to_have_value('本页未保存的说明')
                page.get_by_role('button', name='放弃本页修改并刷新', exact=True).click(timeout=2000)
                playwright.expect(page.get_by_label('用途说明')).to_have_value('另一入口已保存的说明')
                page.get_by_label('用途说明').fill('保存后仍能恢复的旅行进展')
                failed = []
                def preview_failure(route):
                    if route.request.post_data_json.get('action') == 'preview' and not failed:
                        failed.append(True)
                        route.fulfill(status=400, content_type='application/json', body=json.dumps({'error': {'message': 'isolated preview rejection'}}))
                    else: route.continue_()
                page.route('**/user/forms/api', preview_failure)
                page.get_by_role('button', name='生成预览', exact=True).click()
                playwright.expect(page.get_by_role('alert')).to_contain_text('isolated preview rejection')
                page.get_by_role('button', name='生成预览', exact=True).click()
                playwright.expect(page.locator('#previewSection')).to_be_visible()
                assert api.call('runtime.forms.get', {'reference': form['reference']})['data']['revision'] == 2
                page.unroute('**/user/forms/api', preview_failure)
                lost = []
                def lose_submit(route):
                    if route.request.post_data_json.get('action') == 'submit' and not lost:
                        lost.append(True)
                        route.fetch()  # The server accepted the original action; only the response is lost.
                        route.abort('failed')
                    else: route.continue_()
                page.route('**/user/forms/api', lose_submit)
                page.get_by_role('button', name='提交并申请确认', exact=True).click()
                playwright.expect(page.get_by_role('alert')).to_be_visible()
                playwright.expect(page.locator('#submit')).to_be_disabled()
                page.get_by_role('button', name='刷新状态', exact=True).click()
                playwright.expect(page.locator('#state')).to_have_text('等待确认')
                page.reload()
                playwright.expect(page.locator('#state')).to_have_text('等待确认')
                playwright.expect(page.get_by_label('用途说明')).to_have_value('保存后仍能恢复的旅行进展')
                assert page.url.split('#')[0] == page.url
                page.evaluate("window.waitingField = document.getElementById('field-description')")
                page.get_by_role('button', name='打开确认页面', exact=True).focus()
                page.wait_for_timeout(5500)  # Two real polling cycles must leave the DOM and focus intact.
                assert page.evaluate("window.waitingField === document.getElementById('field-description')")
                playwright.expect(page.get_by_role('button', name='打开确认页面', exact=True)).to_be_focused()
                with page.expect_popup() as popup:
                    page.get_by_role('button', name='打开确认页面', exact=True).click()
                confirmation_page = popup.value
                playwright.expect(confirmation_page.get_by_role('button', name='允许此操作', exact=True)).to_be_visible()
                confirmation_page.close()
                page.screenshot(path=str(tmp_path/'form-waiting.png'), full_page=True)
            finally:
                browser.close()


def test_app_bridge_runs_in_opaque_sandbox_and_preserves_host_tool_result(tmp_path):
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1000, 'height': 900})
            page.clock.install()
            page.set_content('<html><body><iframe id="app" sandbox="allow-scripts" style="width:950px;height:800px;border:0"></iframe></body></html>')
            form = {'reference':'saved-form','kind':'messaging.interface','title':'定义消息接口','revision':0,
                    'phase':'draft','values':{'description':'来自宿主的原草稿'},'schema':{'type':'object','properties':{
                        'description':{'type':'string','title':'用途说明'},
                        'optionalMaterial':{'type':'object','title':'可选材料','properties':{'text':{'type':'string','title':'材料正文'}},'required':['text']}}},'preview':None,'result':None,'readOnly':False}
            page.evaluate('''form => {
                window.events=[];
                window.addEventListener('message', event => {
                    const m=event.data; if(event.source!==document.getElementById('app').contentWindow)return;
                    events.push(m);
                    if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26',hostCapabilities:{},hostInfo:{name:'isolated-host',version:'1'},hostContext:{}}},'*');
                    if(m.method==='ui/notifications/initialized'){
                        event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-input',params:{arguments:{kind:'messaging.interface'}}},'*');
                        event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{content:[],structuredContent:{status:'completed',data:form,error:null}}},'*');
                    }
                });
            }''', form)
            page.locator('#app').evaluate('(node, html) => node.srcdoc=html', form_page('app'))
            frame = page.frame_locator('#app')
            playwright.expect(frame.get_by_label('用途说明')).to_have_value('来自宿主的原草稿')
            assert page.evaluate("events.some(event=>event.method==='ui/notifications/initialized')")
            frame.get_by_label('用途说明').fill('用户正在编辑')
            page.evaluate('''form=>document.getElementById('app').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{content:[],structuredContent:{status:'completed',data:form,error:null}}},'*')''', form)
            playwright.expect(frame.get_by_label('用途说明')).to_have_value('用户正在编辑')
            frame.get_by_role('button', name='保存草稿', exact=True).click()
            page.wait_for_function("events.some(event=>event.method==='tools/call')")
            sent = page.evaluate("events.find(event=>event.method==='tools/call').params.arguments.values")
            assert sent == {'description': '用户正在编辑'}
            # A native host may wait for its user longer than our former RPC timeout.
            page.clock.fast_forward(40000)
            playwright.expect(frame.get_by_role('status')).to_contain_text('等待宿主授权或工具返回')
            playwright.expect(frame.get_by_role('alert')).not_to_be_visible()
            playwright.expect(frame.get_by_role('button', name='接入与客户端配置', exact=True)).to_be_disabled()
            playwright.expect(frame.get_by_role('button', name='保存草稿', exact=True)).to_be_disabled()
            form.update(revision=1, phase='waiting', readOnly=True,
                        values={'description':'用户正在编辑'}, result={'data':{'confirmationRef':'confirmation-1'}})
            page.evaluate("""form=>{
                const request=events.find(event=>event.method==='tools/call');
                document.getElementById('app').contentWindow.postMessage({jsonrpc:'2.0',id:request.id,
                    result:{structuredContent:{status:'completed',data:form}}},'*');
            }""",form)
            playwright.expect(frame.locator('#state')).to_have_text('等待确认')
            playwright.expect(frame.get_by_role('status')).not_to_be_visible()
            page.clock.fast_forward(60000)
            assert page.evaluate("events.filter(event=>event.method==='tools/call').length")==1
            frame.get_by_role('button',name='刷新状态',exact=True).click()
            page.wait_for_function("events.filter(event=>event.method==='tools/call').length===2")
            page.evaluate("""()=>{
                const request=events.filter(event=>event.method==='tools/call').at(-1);
                document.getElementById('app').contentWindow.postMessage({jsonrpc:'2.0',id:request.id,
                    result:{isError:true,content:[{type:'text',text:'User declined tool call'}]}},'*');
            }""")
            playwright.expect(frame.get_by_role('alert')).to_contain_text('User declined')
            playwright.expect(frame.get_by_role('button',name='刷新状态',exact=True)).to_be_enabled()
        finally:
            browser.close()
