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
                playwright.expect(page.get_by_role('alert')).to_contain_text('本页版本已过期')
                playwright.expect(page.locator('#saveStatus')).to_be_hidden()
                playwright.expect(page.get_by_label('用途说明')).to_have_value('本页未保存的说明')
                page.get_by_role('button', name='放弃本页修改并刷新', exact=True).click(timeout=2000)
                playwright.expect(page.get_by_label('用途说明')).to_have_value('另一入口已保存的说明')
                page.get_by_role('button', name='保存草稿', exact=True).click()
                playwright.expect(page.locator('#saveStatus')).to_have_text('没有新的修改需要保存。')
                page.get_by_label('用途说明').fill('明确保存成功的说明')
                page.get_by_role('button', name='保存草稿', exact=True).click()
                playwright.expect(page.locator('#saveStatus')).to_have_text('草稿已保存。')
                page.get_by_label('用途说明').fill('保存后仍能恢复的旅行进展')
                playwright.expect(page.locator('#saveStatus')).to_be_hidden()
                failed = []
                def preview_failure(route):
                    if route.request.post_data_json.get('action') == 'preview' and not failed:
                        failed.append(True)
                        route.fulfill(status=400, content_type='application/json', body=json.dumps({'error': {'message': 'isolated preview rejection'}}))
                    else: route.continue_()
                page.route('**/user/forms/api', preview_failure)
                page.get_by_role('button', name='生成预览', exact=True).click()
                playwright.expect(page.get_by_role('alert')).to_contain_text('isolated preview rejection')
                playwright.expect(page.locator('#saveStatus')).to_contain_text('草稿已保存；后续操作未完成')
                page.get_by_role('button', name='生成预览', exact=True).click()
                playwright.expect(page.locator('#previewSection')).to_be_visible()
                assert api.call('runtime.forms.get', {'reference': form['reference']})['data']['revision'] == 3
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
            playwright.expect(frame.locator('#saveStatus')).to_have_text('正在保存草稿…')
            sent = page.evaluate("events.find(event=>event.method==='tools/call').params.arguments.values")
            assert sent == {'description': '用户正在编辑'}
            # A native host may wait for its user longer than our former RPC timeout.
            page.clock.fast_forward(40000)
            playwright.expect(frame.locator('#transportStatus')).to_contain_text('等待宿主授权或工具返回')
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
            playwright.expect(frame.locator('#saveStatus')).to_have_text('草稿已保存。')
            playwright.expect(frame.locator('#transportStatus')).not_to_be_visible()
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
            # Missing UI-only metadata must never fall back to an unguarded browser call.
            frame.get_by_role('button', name='打开确认页面', exact=True).click()
            playwright.expect(frame.get_by_role('alert')).to_contain_text('刷新原表单')
            assert page.evaluate("events.filter(event=>event.method==='tools/call').length")==2
            page.evaluate("""form=>{
                document.getElementById('app').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',
                    params:{structuredContent:{status:'completed',data:form},
                        _meta:{'ioalab/navigation':{reference:form.reference,token:'view-only-token'}}}},'*');
            }""", form)
            frame.get_by_role('button', name='打开确认页面', exact=True).click()
            page.wait_for_function("events.filter(event=>event.method==='tools/call').length===3")
            assert page.evaluate("events.filter(event=>event.method==='tools/call').at(-1).params") == {
                'name':'ioa_open_browser','arguments':{'reference':'saved-form','uiNavigationToken':'view-only-token'}}
            assert 'view-only-token' not in frame.locator('body').inner_text()
        finally:
            browser.close()


def test_existing_account_page_offers_explicit_reuse(platform, tmp_path):
    import hashlib
    from _support import new_user, PASSWORD
    from ioalab_sdk.runtime.protocol import encode
    from ioalab_sdk.runtime.files import private_json
    from ioalab_sdk.runtime.onboarding import Onboarding
    from ioalab_sdk.runtime.user_ui import UserUi
    from ioalab_sdk.runtime.ipc import IpcServer
    user = new_user(platform)
    home = tmp_path / 'clients' / hashlib.sha256(encode([platform.s.base_url, user['username']]).encode()).hexdigest()[:24]
    original = tmp_path / 'original.json'
    private_json(original, {'platform_url': platform.s.base_url, 'username': user['username'],
        'password': PASSWORD, 'data_dir': str(home),
        'identities': [{'key': 'personal', 'role': 'assistant', 'name': 'Original'}]})
    with ResidentRuntime(RuntimeConfig.load(original)) as runtime:
        wait_for(lambda: runtime.connection.connected)
        before = original.read_bytes()
        onboarding = Onboarding(tmp_path / 'new.json')
        onboarding.home.mkdir(parents=True)
        ui = UserUi(onboarding=onboarding)
        server = IpcServer(onboarding.home, 'reuse-browser', lambda *_: {}, user_ui=ui).start()
        try:
            with playwright.sync_playwright() as manager:
                browser = manager.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(ui.entry_url())
                page.get_by_label('平台地址').fill(platform.s.base_url)
                page.get_by_label('账号', exact=True).fill(user['username'])
                page.get_by_label('密码', exact=True).fill(PASSWORD)
                page.get_by_role('button', name='保存并接入', exact=True).click()
                playwright.expect(page.locator('#reuseSection')).to_be_visible()
                playwright.expect(page.get_by_role('button', name='恢复原请求', exact=True)).to_be_hidden()
                assert page.get_by_label('原配置文件路径').count() == 0
                playwright.expect(page.locator('#existingAccess')).to_contain_text(user['username'])
                page.get_by_role('button', name='继续使用', exact=True).click()
                playwright.expect(page.locator('#message')).to_contain_text('已核对原接入')
                assert original.read_bytes() == before and not onboarding.path.exists()
                browser.close()
        finally:
            server.close()


def test_inline_account_registers_in_sandbox_without_mcp_credentials(platform, tmp_path):
    from ioalab_sdk.runtime import account_page
    from ioalab_sdk.runtime.onboarding import Onboarding
    from ioalab_sdk.runtime.user_ui import UserUi
    from ioalab_sdk.runtime.ipc import IpcServer
    account = Onboarding(tmp_path / 'inline.json')
    account.home.mkdir(parents=True)
    ui = UserUi(onboarding=account)
    server = IpcServer(account.home, 'account-inline-browser', lambda *_: {}, user_ui=ui).start()
    try:
        with playwright.sync_playwright() as manager:
            browser = manager.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width':1000,'height':900})
            page.goto(ui.origin+'/user')
            page.set_content('<iframe id="app" sandbox="allow-scripts" style="width:950px;height:850px"></iframe>')
            page.evaluate('''entry=>{
                window.events=[];
                addEventListener('message',event=>{
                    if(event.source!==document.getElementById('app').contentWindow)return;
                    const m=event.data;events.push(m);
                    if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26'}},'*');
                    if(m.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{_meta:{'ioalab/account':entry}}},'*');
                    if(m.method==='tools/call')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{structuredContent:{status:'completed',data:{opened:true}}}},'*');
                });
            }''', {**ui.account_app_entry(), 'navigationToken':'navigation-only'})
            page.locator('#app').evaluate('(node,html)=>node.srcdoc=html',account_page())
            frame=page.frame_locator('#app')
            playwright.expect(frame.locator('#message')).to_have_text('请填写接入信息')
            frame.get_by_label('平台地址').fill(platform.s.base_url)
            frame.get_by_label('接入方式').select_option('register')
            frame.get_by_role('button',name='保存并接入',exact=True).click()
            playwright.expect(frame.locator('#message')).to_contain_text('账号已就绪')
            playwright.expect(frame.locator('#configuration')).to_be_hidden()
            playwright.expect(frame.locator('#devicesSection')).to_be_hidden()
            frame.get_by_role('button',name='一次性取回注册密码',exact=True).click()
            playwright.expect(frame.locator('#secret')).to_be_visible()
            secret=frame.locator('#secret').inner_text().split('密码：')[1]
            assert len(secret)>=24
            assert secret not in json.dumps(page.evaluate('events'))
            assert not page.evaluate("events.some(e=>e.method==='tools/call')")
            frame.get_by_role('button',name='隐藏密码',exact=True).click()
            assert not RuntimeConfig.load(account.path).password
            frame.get_by_role('button',name='在浏览器继续',exact=True).click()
            playwright.expect(frame.locator('#message')).to_contain_text('已打开原账号页面')
            assert page.evaluate("events.find(e=>e.method==='tools/call').params") == {'name':'ioa_setup_browser','arguments':{'uiNavigationToken':'navigation-only'}}
            page.screenshot(path=str(tmp_path/'inline-account.png'),full_page=True)
            browser.close()
    finally:
        server.close()


@pytest.mark.parametrize('mode',['browser','app'])
@pytest.mark.parametrize('confirmation', [False, True, 'payment'])
def test_expired_quote_is_visible_and_recovers_in_same_form(tmp_path,mode,confirmation):
    form={'reference':'original-form','kind':'delegation.trade','title':'比较报价与选择合同','revision':9,
          'phase':'rejected','values':{'action':'select'},'schema':{'type':'object','properties':{}},
          'preview':None,'result':{'status':'rejected','error':{'code':'expired','message':'This candidate can no longer form a new contract'}},
          'readOnly':True,'canRecover':True}
    recovered={**form,'revision':10,'title':'报价已过期：沿用原需求重新报价','phase':'draft','canRecover':False,
               'readOnly':False,'result':None,'values':{'action':'retry'}}
    label = '沿用原需求重新报价'
    message = '报价已过期，不能继续签约'
    if confirmation:
        form['values'] = {'action': 'pay' if confirmation == 'payment' else 'accept', 'period': 1}
        form['result'] = {'status': 'rejected', 'data': {'reason': 'expired', 'confirmationRef': 'original-confirmation'}}
        recovered['title'] = '验收确认已过期：重新申请确认'
        label = '重新申请验收确认'
        message = '验收确认已过期'
        if confirmation == 'payment':
            recovered['title'] = '付款确认已过期：重新申请确认'
            label = '重新申请付款确认'
            message = '付款确认已过期'
    with playwright.sync_playwright() as manager:
        browser=manager.chromium.launch(headless=True)
        try:
            page=browser.new_page();calls=[]
            if mode=='browser':
                def route(request):
                    path=request.request.url
                    if path.endswith('/user/api'):value={'csrf':'test'}
                    elif path.endswith('/user/forms/api'):
                        args=request.request.post_data_json;calls.append(args)
                        value=recovered if args['action']=='recover' else form
                    else:
                        request.fulfill(content_type='text/html',body=form_page('browser'));return
                    request.fulfill(content_type='application/json',body=json.dumps(value))
                page.route('http://form.test/**',route);page.goto('http://form.test/user/forms/original-form');target=page
            else:
                page.set_content('<iframe id="app" sandbox="allow-scripts"></iframe>')
                page.evaluate('''({form,recovered})=>{
                    window.calls=[];addEventListener('message',event=>{
                        const m=event.data;if(event.source!==document.getElementById('app').contentWindow)return;
                        if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26'}},'*');
                        if(m.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{status:'completed',data:form}}},'*');
                        if(m.method==='tools/call'){calls.push(m.params);event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{structuredContent:{status:'completed',data:recovered}}},'*');}
                    });
                }''',{'form':form,'recovered':recovered})
                page.locator('#app').evaluate('(el,html)=>el.srcdoc=html',form_page('app'));target=page.frame_locator('#app')
            playwright.expect(target.get_by_role('alert')).to_contain_text(message)
            target.get_by_role('button',name=label,exact=True).click()
            playwright.expect(target.get_by_role('heading',name=recovered['title'])).to_be_visible()
            playwright.expect(target.get_by_role('alert')).to_be_hidden()
            if mode=='browser':assert calls[-1]=={'action':'recover','reference':'original-form','revision':9}
            else:assert page.evaluate('calls')==[{'name':'ioa_recover_form','arguments':{'reference':'original-form','revision':9}}]
            page.screenshot(path=str(tmp_path/(mode+'-recovered.png')))
        finally:browser.close()


@pytest.mark.parametrize('decision', ['approve', 'reject', 'lost', 'blocked', 'entry', 'expired', 'terminal', 'csp'])
def test_trade_app_confirmation_uses_direct_user_channel(platform, tmp_path, decision):
    path, _ = configuration(platform, tmp_path, service=False)
    with ResidentRuntime(RuntimeConfig.load(path), modules=(AssistantRole(),)) as runtime:
        wait_for(lambda: runtime.connection.connected)
        api=ClientOperations(path)
        f=api.call('runtime.forms.open',{'kind':'delegation.trade','values':{'purpose':'测试交付','budget':5,'proposalFeeLimit':0,'query':'测试'}})['data']
        target={'reference':f['reference'],'revision':f['revision']}
        api.call('runtime.forms.preview',target)
        f=api.call('runtime.forms.submit',target)['data']
        entry=api.confirmation_entry(f['reference'])
        original = runtime.confirmations.get(entry['reference'])
        if decision in ('expired', 'terminal'):
            from ioalab_sdk.runtime.protocol import instant
            runtime.confirmations.clock = lambda: instant(original['expiresAt']) + 1
        if decision == 'terminal':
            entry = api.confirmation_entry(f['reference'])
        if decision == 'entry':
            entry = {**entry, 'token': 'expired-entry'}
        with playwright.sync_playwright() as manager:
            browser=manager.chromium.launch(headless=True)
            page=browser.new_page()
            posts=[]
            if decision=='blocked':
                page.route('**/user/confirmation-app/status',lambda route:route.abort())
            if decision=='lost':
                def lose_response(route):
                    posts.append(route.request.post_data)
                    response=route.fetch()
                    assert response.status==200
                    route.abort()
                page.route('**/user/confirmation-app/decision',lose_response)
            def read_original(args):
                assert args == {'reference': f['reference']}
                latest = api.call('runtime.forms.get', args)['data']
                return {'structuredContent': {'status': 'completed', 'data': latest},
                        '_meta': {'ioalab/confirmation': api.confirmation_entry(f['reference'])}}
            page.expose_function('readOriginal', read_original)
            page.set_content('<iframe id="app" sandbox="allow-scripts" style="width:950px;height:600px"></iframe>')
            page.evaluate('''data=>{window.calls=[];addEventListener('message',event=>{
                if(event.source!==document.getElementById('app').contentWindow)return;
                const m=event.data;
                if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26'}},'*');
                if(m.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{status:'completed',data:data.form},_meta:{'ioalab/confirmation':data.entry,'ioalab/navigation':{reference:data.form.reference,token:'test-navigation'}}}},'*');
                if(m.method==='tools/call'){calls.push(m);if(m.params.name==='ioa_get_form')readOriginal(m.params.arguments).then(result=>event.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*'));}
            });}''',{'form':f,'entry':entry})
            html = form_page('app')
            if decision == 'csp':
                html = html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="connect-src &#39;none&#39;">')
            page.locator('#app').evaluate('(node,html)=>node.srcdoc=html', html)
            frame=page.frame_locator('#app')
            playwright.expect(frame.locator('#confirmationCard')).to_be_visible()
            if decision in ('expired', 'terminal'):
                playwright.expect(frame.locator('#confirmationStatus')).to_contain_text('业务确认已过期')
                playwright.expect(frame.get_by_role('button', name='允许此操作', exact=True)).to_have_count(0)
                assert runtime.confirmations.get(original['reference'])['expiresAt'] == original['expiresAt']
                assert not page.evaluate('calls')
                browser.close()
                return
            if decision == 'csp':
                playwright.expect(frame.locator('#confirmationStatus')).to_contain_text('重新打开当前聊天中的原表单')
                playwright.expect(frame.get_by_role('button', name='重新连接原确认', exact=True)).to_be_hidden()
                assert not page.evaluate('calls')
                browser.close()
                return
            if decision == 'entry':
                playwright.expect(frame.locator('#confirmationStatus')).to_contain_text('内嵌连接已失效')
                frame.get_by_role('button', name='重新连接原确认', exact=True).click()
                playwright.expect(frame.get_by_role('button', name='允许此操作', exact=True)).to_be_enabled()
                assert [call['params']['name'] for call in page.evaluate('calls')] == ['ioa_get_form']
                latest = runtime.confirmations.get(original['reference'])
                assert latest['status'] == 'pending' and latest['expiresAt'] == original['expiresAt']
                browser.close()
                return
            if decision=='blocked':
                playwright.expect(frame.locator('#confirmationStatus')).to_contain_text('在浏览器确认')
                frame.get_by_role('button',name='在浏览器确认',exact=True).click()
                assert page.evaluate('calls[0].params.name')=='ioa_open_browser'
                assert runtime.confirmations.get(entry['reference'])['status']=='pending'
                browser.close()
                return
            playwright.expect(frame.locator('#confirmationCard')).to_contain_text('测试交付')
            playwright.expect(frame.locator('#confirmationTitle')).to_be_in_viewport()
            assert runtime.confirmations.get(entry['reference'])['status']=='pending'
            frame.get_by_role('button',name='拒绝此操作' if decision=='reject' else '允许此操作',exact=True).click()
            if decision=='lost':
                playwright.expect(frame.locator('#confirmationStatus')).to_contain_text('提交结果待核实')
                frame.get_by_role('button',name='核对确认结果',exact=True).click()
                assert len(posts)==1
            playwright.expect(frame.locator('#confirmationStatus')).to_contain_text('已拒绝' if decision=='reject' else '已允许')
            assert runtime.confirmations.get(entry['reference'])['status']==('rejected' if decision=='reject' else 'approved')
            assert not page.evaluate('calls')
            browser.close()


@pytest.mark.parametrize('mode', ['app', 'browser'])
def test_quote_labels_keep_original_selection_and_escape_names(tmp_path, mode):
    label = '<b>文本服务</b> · 0.50 虚拟币 · 单期交付 · 报价1'
    form = {'reference': 'original-form', 'kind': 'delegation.trade', 'title': '比较报价与选择合同',
            'revision': 8, 'phase': 'draft', 'values': {}, 'readOnly': False,
            'schema': {'type': 'object', 'properties': {'quoteRef': {'type': 'string', 'title': '候选报价',
                       'enum': ['original-quote'], 'enumNames': [label]}}},
            'presentation': {'choices': {'quoteRef': {'original-quote': label}}},
            'evidence': {'报价': [{'quoteRef': 'original-quote', 'service': 'svc-internal', 'card': {'derived': {'total': 0.5}}}]}}
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        try:
            page = browser.new_page(); calls = []
            if mode == 'browser':
                def route(request):
                    url = request.request.url
                    if url.endswith('/user/api'): value = {'csrf': 'test'}
                    elif url.endswith('/user/forms/api'):
                        args = request.request.post_data_json; calls.append(args); value = form
                    else:
                        request.fulfill(content_type='text/html', body=form_page('browser')); return
                    request.fulfill(content_type='application/json', body=json.dumps(value))
                page.route('http://form.test/**', route)
                page.goto('http://form.test/user/forms/original-form'); target = page
            else:
                page.set_content('<iframe id="app" sandbox="allow-scripts"></iframe>')
                page.evaluate('''form=>{
                    window.calls=[];addEventListener('message',event=>{
                        const m=event.data;if(event.source!==document.getElementById('app').contentWindow)return;
                        if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26'}},'*');
                        if(m.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{status:'completed',data:form}}},'*');
                        if(m.method==='tools/call'){calls.push(m.params);event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{structuredContent:{status:'completed',data:form}}},'*');}
                    });
                }''', form)
                page.locator('#app').evaluate('(el,html)=>el.srcdoc=html', form_page('app'))
                target = page.frame_locator('#app')
            playwright.expect(target.locator('#field-quoteRef')).to_contain_text(label)
            playwright.expect(target.locator('#evidence')).to_contain_text(label)
            assert target.locator('#evidence b').count() == 0
            assert 'svc-internal' not in target.locator('#evidence').inner_text()
            target.get_by_label('候选报价', exact=True).select_option(label=label)
            target.get_by_role('button', name='保存草稿', exact=True).click()
            playwright.expect(target.locator('#saveStatus')).to_contain_text('草稿已保存')
            saved = calls[-1] if mode == 'browser' else page.evaluate('calls')[-1]['arguments']
            assert saved['values'] == {'quoteRef': 'original-quote'}
            assert saved['revision'] == 8 and saved['reference'] == 'original-form'
        finally:
            browser.close()


@pytest.mark.parametrize('mode', ['app', 'browser'])
def test_revision_conflict_can_load_latest_without_saving_or_losing_draft(tmp_path, mode):
    original = {'reference': 'same-form', 'kind': 'delegation.trade', 'title': '比较报价与选择合同',
        'revision': 0, 'phase': 'draft', 'values': {'note': '旧值'}, 'readOnly': False,
        'schema': {'type': 'object', 'properties': {'note': {'type': 'string', 'title': '说明'}}}}
    latest = {**original, 'revision': 8, 'values': {'note': '已保存的新值'}}
    error = {'code': 'id_conflict', 'message': 'Form revision changed; reload the original form'}
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        try:
            page = browser.new_page(); calls = []; updated = []
            if mode == 'browser':
                def route(request):
                    url = request.request.url
                    if url.endswith('/user/api'): value = {'csrf': 'test'}
                    elif url.endswith('/user/forms/api'):
                        args = request.request.post_data_json; calls.append(args)
                        if args['action'] == 'update':
                            updated.append(args)
                            request.fulfill(status=409, content_type='application/json', body=json.dumps({'error': error})); return
                        value = latest if updated else original
                    else:
                        request.fulfill(content_type='text/html', body=form_page('browser')); return
                    request.fulfill(content_type='application/json', body=json.dumps(value))
                page.route('http://form.test/**', route)
                page.goto('http://form.test/user/forms/same-form'); target = page
            else:
                page.set_content('<iframe id="app" sandbox="allow-scripts"></iframe>')
                page.evaluate('''({original,latest,error})=>{
                    window.calls=[];addEventListener('message',event=>{
                        const m=event.data;if(event.source!==document.getElementById('app').contentWindow)return;
                        if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26'}},'*');
                        if(m.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{status:'completed',data:original}}},'*');
                        if(m.method==='tools/call'){
                            calls.push(m.params);
                            const value=m.params.name==='ioa_update_form'?{status:'rejected',error}:{status:'completed',data:latest};
                            event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{structuredContent:value}},'*');
                        }
                    });
                }''', {'original': original, 'latest': latest, 'error': error})
                page.locator('#app').evaluate('(el,html)=>el.srcdoc=html', form_page('app'))
                target = page.frame_locator('#app')
            target.get_by_label('说明', exact=True).fill('本页尚未保存的选择')
            target.get_by_role('button', name='保存草稿', exact=True).click()
            playwright.expect(target.get_by_role('alert')).to_contain_text('本页版本已过期')
            playwright.expect(target.get_by_role('button', name='保存草稿', exact=True)).to_be_disabled()
            playwright.expect(target.get_by_label('说明', exact=True)).to_have_value('本页尚未保存的选择')
            target.get_by_role('button', name='加载最新版本', exact=True).click()
            playwright.expect(target.get_by_label('说明', exact=True)).to_have_value('已保存的新值')
            playwright.expect(target.locator('#unsavedCopy')).to_contain_text('本页尚未保存的选择')
            playwright.expect(target.get_by_role('alert')).to_be_hidden()
            playwright.expect(target.get_by_role('button', name='保存草稿', exact=True)).to_be_enabled()
            if mode == 'browser':
                assert len(updated) == 1 and updated[0]['revision'] == 0
                assert calls[-1] == {'action': 'get', 'reference': 'same-form'}
            else:
                assert page.evaluate('calls') == [
                    {'name': 'ioa_update_form', 'arguments': {'reference': 'same-form', 'revision': 0, 'values': {'note': '本页尚未保存的选择'}}},
                    {'name': 'ioa_get_form', 'arguments': {'reference': 'same-form'}}]
        finally:
            browser.close()


@pytest.mark.parametrize('mode', ['app', 'browser'])
def test_fulfillment_queries_run_once_with_feedback_and_preserve_approval(tmp_path, mode):
    from copy import deepcopy
    form = {'reference': 'original-contract-form', 'kind': 'delegation.trade',
        'title': '合同履约、验收与结算', 'revision': 19, 'phase': 'draft',
        'values': {'action': 'refresh'}, 'readOnly': False, 'preview': None,
        'schema': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'title': '下一步', 'enum': ['refresh', 'verify', 'accept', 'pay'],
                       'enumNames': ['刷新履约状态', '查看本期验收预览', '申请本期验收与付款确认', '恢复已验收期付款']},
            'period': {'type': 'integer', 'title': '交付期数', 'enum': [1]}}},
        'presentation': {'directActions': {'refresh': '刷新合同状态', 'verify': '查看本期验收结果'}},
        'evidence': {'合同状态': {'formed': True, 'availablePeriods': 1, 'acceptedPeriods': 0, 'payment': 'unpaid'}}}
    calls = []; fail = {}; changed = []; lost = []
    def request(action, args):
        calls.append((action, deepcopy(args)))
        if action == 'update':
            form.update(values=args['values'], revision=form['revision']+1, preview=None)
        if action == 'preview':
            if fail:
                return {'error': {'code': 'invalid_input', 'message': '验收参数有误'}}
            if changed:
                form.update(revision=form['revision']+1, values={'action': 'accept', 'period': 1})
            form['preview'] = {'values': deepcopy(form['values'])}
        if action == 'submit':
            assert form['values']['action'] in ('refresh', 'verify')
            if form['values']['action'] == 'verify':
                form['evidence']['操作结果'] = {'report': {'passed': True}}
            form.update(revision=form['revision']+1, values={'action': 'refresh'}, preview=None)
            if lost:
                lost.clear()
                return {'error': {'code': 'unknown_result', 'message': '测试回执丢失'}}
        return {'data': deepcopy(form)}
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            if mode == 'browser':
                def route(route):
                    url = route.request.url
                    if url.endswith('/user/api'): value = {'csrf': 'test'}
                    elif url.endswith('/user/forms/api'):
                        args = route.request.post_data_json; action = args.pop('action')
                        response = request(action, args)
                        if response.get('error'):
                            route.fulfill(status=400, content_type='application/json', body=json.dumps(response)); return
                        value = response['data']
                    else:
                        route.fulfill(content_type='text/html', body=form_page('browser')); return
                    route.fulfill(content_type='application/json', body=json.dumps(value))
                page.route('http://form.test/**', route)
                page.goto('http://form.test/user/forms/original-contract-form'); target = page
            else:
                page.expose_function('formRequest', request)
                page.set_content('<iframe id="app" sandbox="allow-scripts"></iframe>')
                page.evaluate('''form=>{
                    addEventListener('message',async event=>{
                        const m=event.data;if(event.source!==document.getElementById('app').contentWindow)return;
                        if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26'}},'*');
                        if(m.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{data:form}}},'*');
                        if(m.method==='tools/call'){
                            const action=m.params.name.replace('ioa_','').replace('_form','');
                            const result=await formRequest(action,m.params.arguments);
                            event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{structuredContent:result}},'*');
                        }
                    });
                }''', form)
                page.locator('#app').evaluate('(el,html)=>el.srcdoc=html', form_page('app')); target=page.frame_locator('#app')
            playwright.expect(target.get_by_role('button', name='生成预览', exact=True)).to_be_hidden()
            target.get_by_role('button', name='刷新合同状态', exact=True).evaluate('(button)=>{button.click();button.click();}')
            playwright.expect(target.locator('#saveStatus')).to_contain_text('合同状态已刷新')
            assert [a for a, _ in calls].count('submit') == 1
            target.get_by_label('下一步', exact=True).select_option(label='查看本期验收预览')
            target.get_by_label('交付期数', exact=True).select_option(label='1')
            target.get_by_role('button', name='查看本期验收结果', exact=True).click()
            playwright.expect(target.locator('#saveStatus')).to_contain_text('查看本期验收结果已完成')
            playwright.expect(target.locator('#evidence')).to_contain_text('操作结果')
            assert [a for a, _ in calls].count('submit') == 2
            assert form['evidence']['合同状态']['acceptedPeriods'] == 0
            assert form['evidence']['合同状态']['payment'] == 'unpaid'
            for action in ('accept', 'pay'):
                target.get_by_label('下一步', exact=True).select_option(label={'accept':'申请本期验收与付款确认','pay':'恢复已验收期付款'}[action])
                playwright.expect(target.get_by_role('button', name='生成预览', exact=True)).to_be_visible()
                playwright.expect(target.get_by_role('button', name='提交并申请确认', exact=True)).to_be_disabled()
                playwright.expect(target.locator('#run')).to_be_hidden()
            # A lost submit response must only reload the original form, never reissue the query.
            target.get_by_label('下一步', exact=True).select_option(label='查看本期验收预览')
            target.get_by_label('交付期数', exact=True).select_option(label='1'); lost.append(True)
            target.locator('#run').click()
            playwright.expect(target.get_by_role('alert')).to_contain_text('测试回执丢失')
            playwright.expect(target.locator('#run')).to_be_disabled()
            target.get_by_role('button', name='刷新表单状态', exact=True).click()
            playwright.expect(target.locator('#saveStatus')).to_contain_text('表单状态已更新')
            assert [a for a, _ in calls].count('submit') == 3
            # A rejected query must stop before submit and report the real error.
            target.get_by_label('下一步', exact=True).select_option(label='查看本期验收预览')
            target.get_by_label('交付期数', exact=True).select_option(label='1'); fail['preview'] = True
            target.locator('#run').click()
            playwright.expect(target.get_by_role('alert')).to_contain_text('验收参数有误')
            assert [a for a, _ in calls].count('submit') == 3
            fail.clear(); changed.append(True)
            target.locator('#run').click()
            playwright.expect(target.get_by_role('alert')).to_contain_text('本页版本已过期')
            assert [a for a, _ in calls].count('submit') == 3
            assert all(args['reference'] == 'original-contract-form' for _, args in calls)
        finally: browser.close()


@pytest.mark.parametrize('mode', ['app', 'browser'])
def test_contract_progress_distinguishes_query_result_next_step_and_payment(tmp_path, mode):
    from copy import deepcopy
    form = {'reference': 'original-form', 'kind': 'delegation.trade', 'title': '合同履约、验收与结算',
        'revision': 20, 'phase': 'accepted', 'readOnly': True, 'values': {'action': 'verify', 'period': 1},
        'schema': {'type': 'object', 'properties': {}}, 'result': {'status': 'accepted', 'data': {}},
        'evidence': {'合同状态': {'formed': True, 'availablePeriods': 1, 'acceptedPeriods': 0, 'payment': 'unpaid'},
                     '操作结果': {'period': 1, 'report': {'status': 'pass', 'checks': []}}}}
    calls = []
    def get_form():
        calls.append('get'); return deepcopy(form)
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            if mode == 'browser':
                def route(route):
                    url=route.request.url
                    if url.endswith('/user/api'): value={'csrf':'test'}
                    elif url.endswith('/user/forms/api'):
                        assert route.request.post_data_json['action']=='get';value=get_form()
                    else:
                        route.fulfill(content_type='text/html',body=form_page('browser'));return
                    route.fulfill(content_type='application/json',body=json.dumps(value))
                page.route('http://form.test/**',route);page.goto('http://form.test/user/forms/original-form');target=page
            else:
                page.expose_function('getForm',get_form)
                page.set_content('<iframe id="app" sandbox="allow-scripts"></iframe>')
                page.evaluate('''form=>{addEventListener('message',async event=>{
                    const m=event.data;if(event.source!==document.getElementById('app').contentWindow)return;
                    if(m.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26'}},'*');
                    if(m.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{data:form}}},'*');
                    if(m.method==='tools/call'){
                        if(m.params.name!=='ioa_get_form')throw Error('Unexpected mutation');
                        const data=await getForm();event.source.postMessage({jsonrpc:'2.0',id:m.id,result:{structuredContent:{data}}},'*');
                    }
                });}''',form)
                page.locator('#app').evaluate('(el,html)=>el.srcdoc=html',form_page('app'));target=page.frame_locator('#app')
            # A previous successful report must not mask a new pending operation.
            playwright.expect(target.locator('#resultText')).to_contain_text('正在等待业务处理结果')
            form.update(revision=21,phase='draft',readOnly=False,values={'action':'refresh'},result={'status':'completed','data':{}})
            target.locator('#refresh').click()
            playwright.expect(target.locator('#state')).to_have_text('等待选择下一步')
            playwright.expect(target.locator('#resultText')).to_have_text('第1期验收检查已完成，全部通过。')
            playwright.expect(target.locator('#contractProgress')).to_contain_text('尚未确认接受交付；尚未付款')
            playwright.expect(target.locator('#nextStep')).to_contain_text('独立确认')
            assert form['revision']==21 and form['evidence']['合同状态']['acceptedPeriods']==0
            form['evidence']['操作结果']['report']['status']='fail'
            target.locator('#refresh').click()
            playwright.expect(target.locator('#resultText')).to_contain_text('检查未通过')
            form.update(phase='unknown',readOnly=True,result={'status':'unknown','data':{}})
            target.locator('#refresh').click()
            playwright.expect(target.locator('#resultText')).to_contain_text('尚未核实')
            # Refresh alone is not verification. No report means no fabricated pass.
            form.update(phase='draft',readOnly=False,result={'status':'completed','data':{}})
            del form['evidence']['操作结果']
            target.locator('#refresh').click()
            playwright.expect(target.locator('#resultText')).to_have_text('合同状态已更新。')
            form['evidence']['合同状态'].update(acceptedPeriods=1,payment='paid',closed=True)
            target.locator('#refresh').click()
            playwright.expect(target.locator('#contractProgress')).to_contain_text('已确认接受1期交付；已付清')
            playwright.expect(target.locator('#nextStep')).to_contain_text('合同已结束')
            assert calls and all(call=='get' for call in calls)
        finally:browser.close()
