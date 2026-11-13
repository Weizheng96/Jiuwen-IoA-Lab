"""Real browser walk of the shipped pages against a stub platform: operations, errors and who sees what."""
from contextlib import contextmanager
import threading

import pytest

from conftest import ROOT, free_port, wait_for, write_platform_configuration
from _support import PASSWORD, new_user, username
from test_agent_delivery import headers, people, peers, publish, request  # noqa: F401  (fixtures use this module's platform)

playwright = pytest.importorskip('playwright.sync_api')
ADMIN = ('admin', 'admin1234')


@pytest.fixture(scope='module')
def platform(tmp_path_factory):
    import uvicorn
    from ioalab.app import Platform
    from ioalab.web.app import create_app
    root = tmp_path_factory.mktemp('browser')
    port = free_port()
    authority = write_platform_configuration(root / 'configuration', data=str(root / 'data'), port=port, autoseed=True,
                                             seed_file=str(ROOT / 'deploy/default-seed.json'))
    platform = Platform(authority.settings(), configuration=authority)
    app = create_app(platform)
    platform.app = app
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='critical'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started)
    yield platform
    server.should_exit = True
    thread.join(8)


@pytest.fixture(scope='module')
def browser():
    with playwright.sync_playwright() as pw:
        launched = None
        for options in ({}, {'channel': 'chrome'}, {'channel': 'msedge'}):
            try:
                launched = pw.chromium.launch(**options)
                break
            except playwright.Error:
                continue
        if launched is None:
            pytest.skip('No Chromium browser is available')
        yield launched
        launched.close()


class Visit:
    """One browser session; every page error and failed request is kept for the test to assert."""

    def __init__(self, context, base):
        self.context, self.base = context, base
        self.page = context.new_page()
        self.problems = []
        self.page.on('pageerror', lambda error: self.problems.append(('pageerror', str(error))))
        self.page.on('console', lambda message: self.problems.append(('console', message.text)) if message.type == 'error' and 'status of' not in message.text else None)
        self.page.on('response', self._response)

    def _response(self, response):
        if response.status >= 400:
            self.problems.append((response.status, response.request.method, response.url.replace(self.base, '')))

    def open(self, path):
        self.page.goto(self.base + path)
        self.page.wait_for_load_state('networkidle')
        return self.page

    @property
    def path(self):
        return self.page.url.replace(self.base, '')

    def text(self):
        return self.page.inner_text('body')


@contextmanager
def visit(browser, platform):
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    try:
        yield Visit(context, platform.s.base_url)
    finally:
        context.close()


def register(session, name):
    page = session.open('/register')
    page.fill('#account-name', name)
    page.fill('#account-password', PASSWORD)
    page.fill('#confirm-password', PASSWORD)
    page.click('#account-submit')
    page.wait_for_url('**/workbench')
    page.wait_for_load_state('networkidle')


def login(session, name):
    page = session.open('/login')
    page.fill('#account-name', name)
    page.fill('#account-password', PASSWORD)
    page.click('#account-submit')
    page.wait_for_url('**/workbench')
    page.wait_for_load_state('networkidle')
    page.wait_for_selector('#workspace:not([hidden])')
    return page


def admin_login(session):
    page = session.open('/login?next=/admin')
    page.click('details.admin-login summary')
    page.fill('#admin-username', ADMIN[0])
    page.fill('#admin-password', ADMIN[1])
    page.click('#admin-form button[type=submit]')
    page.wait_for_url('**/admin')
    page.wait_for_selector('#mainView:not(.hide)')
    return page


def test_public_pages_load_without_errors_and_private_pages_ask_for_login(browser, platform):
    with visit(browser, platform) as session:
        for path in ('/', '/services', '/assistants'):
            session.open(path)
            assert session.path == path
        assert session.problems == []
        for path, target in (('/workbench', '/login?next=%2Fworkbench'), ('/admin', '/login?next=%2Fadmin'), ('/trial', '/login?next=%2Ftrial')):
            session.open(path)
            assert session.path == target
        assert session.problems == []


def test_register_validation_login_logout_through_the_real_forms(browser, platform):
    name = username()
    with visit(browser, platform) as session:
        register(session, name)
        page = session.page
        wait_for(lambda: '虚拟币' in page.inner_text('#credit'))
        assert session.problems == []
        page.click('#logout')
        page.wait_for_url(platform.s.base_url + '/')
        page = session.open('/login')
        page.fill('#account-name', name)
        page.fill('#account-password', 'wrong-password')
        page.click('#account-submit')
        page.wait_for_function("document.getElementById('auth-error').textContent.length>0")
        assert PASSWORD not in session.text()
        session.problems.clear()
        page.fill('#account-password', PASSWORD)
        page.click('#account-submit')
        page.wait_for_url('**/workbench')
        session.page.wait_for_load_state('networkidle')
        assert session.problems == []
        taken = session.context.new_page()
        taken.goto(platform.s.base_url + '/api/me')
        assert name in taken.inner_text('body')
    with visit(browser, platform) as second:
        page = second.open('/register')
        page.fill('#account-name', name)
        page.fill('#account-password', PASSWORD)
        page.fill('#confirm-password', PASSWORD)
        page.click('#account-submit')
        page.wait_for_function("document.getElementById('auth-error').textContent.length>0")
        assert second.path.startswith('/register')


def test_ordinary_account_cannot_open_the_admin_page_or_its_data(browser, platform):
    with visit(browser, platform) as session:
        register(session, username())
        session.problems.clear()
        response = session.page.goto(platform.s.base_url + '/admin')
        assert response.status == 403
        for path in ('/api/admin/users', '/api/admin/configuration', '/api/admin/orders', '/api/admin/messages', '/api/admin/arbitrations'):
            assert session.page.evaluate('path=>fetch(path).then(r=>r.status)', path) in (401, 403), path


ADMIN_TABS = ('panorama', 'stream', 'lanes', 'components', 'configuration', 'messages', 'users', 'registry', 'dispatch', 'needs',
              'orders', 'search', 'runs')


def test_admin_opens_every_tab_without_errors(browser, platform):
    with visit(browser, platform) as session:
        page = admin_login(session)
        for tab in ADMIN_TABS:
            page.click(f'#tabs button[data-tab={tab}]')
            page.wait_for_selector(f'#view-{tab}.on')
            page.wait_for_timeout(300)
        assert session.problems == []


def test_admin_configuration_saves_detects_conflicts_and_never_shows_secrets(browser, platform):
    with visit(browser, platform) as session:
        page = admin_login(session)
        page.click('#tabs button[data-tab=configuration]')
        page.wait_for_function("document.getElementById('cfgStatus').textContent.includes('修订')")
        before = platform.configuration.view()['revision']
        assert ADMIN[1] not in page.content()
        page.fill('#cfgFailures', '33')
        page.click('#configurationForm button[type=submit]')
        page.wait_for_function(f"document.getElementById('cfgStatus').textContent.includes('修订 {before+1}')")
        assert platform.configuration.view()['editable']['authentication']['max_failures'] == 33
        assert platform.s.policy()['authentication']['max_failures'] == 33
        # Another administrator saved first: the stale form must not overwrite that revision.
        platform.configuration.update(before + 1, {'authentication': {'max_failures': 44}})
        page.fill('#cfgFailures', '55')
        page.click('#configurationForm button[type=submit]')
        page.wait_for_function("!document.getElementById('cfgStatus').textContent.startsWith('修订')")
        assert platform.configuration.view()['editable']['authentication']['max_failures'] == 44
        session.problems[:] = [problem for problem in session.problems if problem[0] != 409]
        page.click('#cfgReload')
        page.wait_for_function(f"document.getElementById('cfgStatus').textContent.includes('修订 {before+2}')")
        assert page.input_value('#cfgFailures') == '44'
        assert session.problems == []


def test_message_is_visible_to_its_parties_and_an_auditor_but_only_as_metadata_to_others(browser, platform, client, people, peers):
    recipient, sender, third = people
    publish(client, recipient).raise_for_status()
    secret = 'BROWSER-PRIVATE-TEXT'
    message = request(recipient['actor'], body={'interfaceName': 'meeting', 'revision': 1, 'input': {'text': secret}})
    peers(sender).call('message.send', message)
    for person in (sender, recipient):
        with visit(browser, platform) as session:
            page = login(session, person['username'])
            page.click(f"[data-message=\"{message['messageId']}\"]")
            page.wait_for_selector('#detail[open]')
            assert secret in page.inner_text('#detail-body')
            assert session.problems == []
    with visit(browser, platform) as session:
        page = login(session, third['username'])
        assert secret not in page.content()
        assert secret not in page.evaluate("fetch('/api/workbench').then(r=>r.text())")
        history = page.evaluate("name=>fetch('/api/people/'+name+'/history').then(r=>r.json())", sender['username'])
        assert history == {'messages': [], 'contracts': [], 'intents': []}
        assert session.problems == []
    with visit(browser, platform) as session:
        page = admin_login(session)
        page.click('#tabs button[data-tab=messages]')
        page.wait_for_selector('#adminMessages table')
        assert sender['username'] in page.inner_text('#adminMessages') and '仅元数据' in page.inner_text('#adminMessages')
        assert secret not in page.content()
        assert page.evaluate("id=>fetch('/api/admin/messages/'+id).then(r=>r.status)", message['messageId']) == 403
    auditor = new_user(platform)
    platform.auth.set_roles('admin', auditor['id'], ['admin', 'auditor'], [])
    with visit(browser, platform) as session:
        page = login(session, auditor['username'])
        page = session.open('/admin')
        page.wait_for_selector('#mainView:not(.hide)')
        page.click('#tabs button[data-tab=messages]')
        page.click(f"[data-audit=\"{message['messageId']}\"]")
        page.wait_for_selector('#messageAudit[open]')
        assert secret in page.inner_text('#messageAuditBody')
        assert session.problems == []
    audits = platform.db.rows("SELECT sender FROM events WHERE op='message.audit'")
    assert any(row['sender'] == auditor['id'] for row in audits)


def test_real_contract_appears_for_both_parties_and_admin_but_not_an_outsider(browser, platform, tmp_path, monkeypatch):
    from test_resident_contracts import candidate, confirm, status
    from test_resident_delegation import participants
    from test_resident_fulfillment import ProductionHost, VERIFIER, configure_template
    configure_template(monkeypatch)
    with participants(platform, tmp_path, formation='proposal', fee=1, agent_host=ProductionHost(), verifier_source=VERIFIER) as (provider, assistant, host, template, task):
        quote = candidate(assistant, provider, template, task)
        selected, _, _ = confirm(assistant, quote)
        reference = selected['data']['contractRef']
        wait_for(lambda: status(assistant, reference))
        assert status(assistant, reference)['data']['formed']
        service = '用户配置的专业服务'
        client_name, provider_name = assistant.account.account['username'], provider.account.account['username']
        with visit(browser, platform) as session:
            page = login(session, client_name)
            page.wait_for_selector('#orders [data-order]')
            page.click('#orders [data-order]')
            page.wait_for_selector('#detail[open]')
            assert service in page.inner_text('#detail-body') or provider.identities[0].agent_id in page.inner_text('#detail-body')
            assert session.problems == []
        with visit(browser, platform) as session:
            page = login(session, provider_name)
            page.click('[data-view=provider]')
            page.wait_for_function('text=>document.body.innerText.includes(text)', arg=service)
            assert session.problems == []
        outsider = new_user(platform)
        with visit(browser, platform) as session:
            page = login(session, outsider['username'])
            assert page.evaluate("fetch('/api/workbench').then(r=>r.json())")['orders'] == []
            assert session.problems == []
        with visit(browser, platform) as session:
            page = admin_login(session)
            page.click('#tabs button[data-tab=orders]')
            page.wait_for_function("document.getElementById('ordersTable').querySelectorAll('tr').length>1")
            assert session.problems == []


def test_home_language_navigation_and_real_public_stats(browser, platform):
    with visit(browser, platform) as session:
        page = session.open('/')
        assert page.get_attribute('html', 'lang') == 'zh-CN'
        assert page.locator('.side-nav a').all_text_contents() == [
            '智能体网络', '如何接入', '发现专业服务', '发现个人助手', '我的智能体', '管理者全局', '在线试用']
        assert page.locator('.side-nav a').evaluate_all('(links)=>links.map(a=>a.getAttribute("href"))') == [
            '/#network', '/connect', '/services', '/assistants', '/workbench', '/admin', '/trial']
        assert page.locator('.home-header a[href="/services"],.home-header a[href="/assistants"]').count() == 0
        stats = page.request.get(platform.s.base_url + '/api/public/stats').json()
        for key, value in stats.items():
            assert page.locator(f'[data-public-stat="{key}"]').inner_text() == str(value)
        assert page.locator('a.nav-login').get_attribute('href') == '/login'
        page.click('[data-language="en"]')
        assert page.get_attribute('html', 'lang') == 'en'
        assert 'A world where' in page.locator('h1').inner_text()
        assert 'Not another platform.' in page.locator('#protocol h2').inner_text()
        assert page.locator('a.nav-login').inner_text() == 'Log in'
        frame = page.frame_locator('#agent-demo-frame')
        frame.locator('[data-mode="chat"]').click()
        assert 'coordinate meetings' in frame.locator('#scenario-text').inner_text()
        page.reload()
        assert page.get_attribute('html', 'lang') == 'zh-CN'
        assert session.problems == []
