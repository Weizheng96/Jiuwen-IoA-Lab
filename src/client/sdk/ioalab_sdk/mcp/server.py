"""Standard stdio MCP tools/resources mapped to the shared public client API."""
import argparse
import asyncio
import hmac
import secrets
from ..runtime import installed_version
import json
import sys

from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from ..runtime import ClientOperations, RuntimeFault, configure_stdio, form_page, setup, account_page, account_app_origin


APP_URI = 'ui://ioalab/forms/v1.html'
ACCOUNT_URI = 'ui://ioalab/account/v1.html'
APP_MIME = 'text/html;profile=mcp-app'


def object_schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


REFERENCE = {'reference': {'type': 'string', 'minLength': 1, 'maxLength': 64}}
REVISION = {**REFERENCE, 'revision': {'type': 'integer', 'minimum': 0}}
VALUES = {'type': 'object', 'description': '普通业务字段；不要包含密码、令牌或私钥。'}
# Mapping only: role definitions and the resident runtime own all business behavior.
TOOLS = {
    'ioa_setup': ('展示账号接入界面：支持 Apps 时内嵌，否则打开浏览器；用户在页面登录或注册，已有接入仍展示内嵌状态页并继续使用，不在聊天传递密码。回执opened仅表示是否打开外部浏览器，false不代表内嵌页面未显示；不要因此引导重新登录。waiting时明确请用户在页面填写并结束本轮等待，不轮询；use_existing_configuration只读核原实例后结束，不执行停机、迁移或重配。', 'account.setup', object_schema({'platform_url': {'type': 'string', 'maxLength': 2048}, 'role': {'type': 'string', 'enum': ['assistant', 'service']}})),
    'ioa_setup_browser': ('仅供账号内嵌页面的用户点击打开原浏览器账号页，不接收账号秘密。', None,
                          object_schema({'uiNavigationToken': {'type': 'string', 'maxLength': 64}}, ('uiNavigationToken',))),
    'ioa_setup_status': ('查询接入进度，不读取账号秘密。', 'account.setup.status', object_schema()),
    'ioa_status': ('查看 IoA 客户端连接、身份和宿主就绪状态。', 'runtime.status', object_schema()),
    'ioa_configuration': ('读取 IoA 本地配置的公开摘要，不读取或输出凭据字段。', None, object_schema()),
    'ioa_forms': ('列出本机可用业务表单及已保存的表单引用，恢复时先查这里。', 'runtime.forms.list', object_schema()),
    'ioa_open_form': ('按业务类型打开表单，预填用户已提供的信息；可拟定业务草稿但不猜金额、对象或扩大权限。缺项交给页面。展示后仅简短引导，不复述JSON和技术编号。查看收到的请求用 messaging.inbox（只读列表/详情），不使用收信规则inbox动作。先用 ioa_forms 查看类型；已有表单用 ioa_get_form 恢复。',
                      'runtime.forms.open', object_schema({'kind': {'type': 'string'}, 'values': VALUES}, ('kind',))),
    'ioa_get_form': ('读取并重新展示原表单和真实业务结果；支持 Apps 时内嵌显示，不需另开浏览器，不启动新操作。', 'runtime.forms.get', object_schema(REFERENCE, ('reference',))),
    'ioa_update_form': ('保存普通字段草稿，必须带当前修订号。', 'runtime.forms.update',
                        object_schema({**REVISION, 'values': VALUES}, ('reference', 'revision', 'values'))),
    'ioa_preview_form': ('校验当前表单并生成真实操作预览，不批准、不发布。', 'runtime.forms.preview', object_schema(REVISION, ('reference', 'revision'))),
    'ioa_recover_form': ('准备原表单的恢复选项，保留原需求和历史；不重新报价、不签约。', 'runtime.forms.recover', object_schema(REVISION, ('reference', 'revision'))),
    'ioa_submit_form': ('提交已预览的修订或续接原操作。需要批准时保持等待，不以调用工具代替用户批准。',
                        'runtime.forms.submit', object_schema(REVISION, ('reference', 'revision'))),
    'ioa_open_browser': ('浏览器导航。支持 Apps 时仅供页面按钮使用，模型应以 ioa_get_form 内嵌恢复；不返回浏览器凭据。', 'runtime.forms.open_ui',
                         object_schema({**REFERENCE, 'uiNavigationToken': {'type': 'string', 'maxLength': 64,
                             'description': '仅由 App 从原表单结果 _meta 取得的导航校验信息，不推测或向用户索要。'}}, ('reference',))),
}


def create_server(config_path):
    api = ClientOperations(config_path)
    # UI navigation only; never an account credential or business approval.
    # A restarted stdio process requires the App to read its original form again.
    navigation_key = secrets.token_bytes(32)
    def navigation_token(reference):
        return hmac.digest(navigation_key, reference.encode(), 'sha256').hex()

    server = Server('ioalab-local', version=installed_version('ioalab-sdk'), instructions=
                    '查看已收到的预约或请求直接打开messaging.inbox只读界面，不沿旧对话执行迁移或启停。'
                    '使用 ioa_forms 发现业务表单，通过界面收集信息。恢复原 reference，不重复创建业务。'
                    '优先复用用户已说明的信息，缺项交给页面；仅意图或对象不明时追问。页面展示后只简短说明下一步，不复述整份数据。'
                    '支持 Apps 时打开和读取表单即内嵌显示，不另调用浏览器工具；外部导航交给用户点击页面按钮。'
                    '外层 completed 只表示表单读写成功，业务状态看 data.phase 和 data.result.status。批准只能由受控用户页面产生。')

    def apps_supported():
        parameters = server.request_context.session.client_params
        capabilities = parameters.capabilities.model_dump() if parameters else {}
        extensions = capabilities.get('extensions')
        ui = extensions.get('io.modelcontextprotocol/ui') if isinstance(extensions, dict) else None
        mime_types = ui.get('mimeTypes') if isinstance(ui, dict) else None
        return isinstance(mime_types, list) and APP_MIME in mime_types

    @server.list_tools()
    async def list_tools():
        supported = apps_supported()
        result = []
        for name, (description, operation, schema) in TOOLS.items():
            meta = None
            if supported and name in ('ioa_open_form', 'ioa_get_form', 'ioa_update_form', 'ioa_preview_form', 'ioa_recover_form', 'ioa_submit_form'):
                meta = {'ui': {'resourceUri': APP_URI}}
            if supported and name == 'ioa_setup':
                meta = {'ui': {'resourceUri': ACCOUNT_URI}}
            if name == 'ioa_setup_browser' or supported and name == 'ioa_open_browser':
                meta = {'ui': {'visibility': ['app']}}
            result.append(types.Tool(name=name, description=description, inputSchema=schema,
                annotations=types.ToolAnnotations(readOnlyHint=name in ('ioa_status', 'ioa_configuration', 'ioa_forms', 'ioa_get_form'),
                                                 destructiveHint=False, openWorldHint=name == 'ioa_submit_form'),
                **({'_meta': meta} if meta else {})))
        return result

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri=APP_URI, name='IoA 业务表单', description='随客户端安装的通用表单；无需访问外部资源。', mimeType=APP_MIME),
                types.Resource(uri=ACCOUNT_URI, name='IoA 账号接入', mimeType=APP_MIME)]

    @server.read_resource()
    async def read_resource(uri):
        if str(uri) == ACCOUNT_URI:
            # Resource and tool requests can arrive on different MCP connections.
            # Resolve the original page over its authenticated IPC, not process memory.
            try:
                account_origin = await asyncio.to_thread(account_app_origin, config_path)
            except RuntimeFault:
                account_origin = None
            return [ReadResourceContents(content=account_page(), mime_type=APP_MIME,
                    meta={'ui': {'csp': {'connectDomains': [account_origin] if account_origin else [], 'resourceDomains': []}}})]
        if str(uri) != APP_URI:
            raise ValueError('Unknown IoA UI resource')
        # Hosts may read resources using a different MCP connection from tools/call.
        # Resolve the current resident origin independently; never rely on prior tool calls.
        try:
            confirmation_origin = (await asyncio.to_thread(api.confirmation_entry))['origin']
        except RuntimeFault:
            confirmation_origin = None
        return [ReadResourceContents(content=form_page('app'), mime_type=APP_MIME,
                                     meta={'ui': {'csp': {'connectDomains': [confirmation_origin] if confirmation_origin else [], 'resourceDomains': []}}})]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        result_meta = None
        try:
            if name not in TOOLS:
                raise RuntimeFault('invalid_input', 'Unknown IoA tool')
            _, operation, schema = TOOLS[name]
            if not isinstance(arguments, dict) or len(json.dumps(arguments, ensure_ascii=False).encode()) > 96 * 1024:
                raise RuntimeFault('invalid_input', 'Tool arguments must be a bounded object')
            if not Draft202012Validator(schema).is_valid(arguments):
                raise RuntimeFault('invalid_input', 'Use the declared tool fields and current form revision')
            if name == 'ioa_configuration':
                result = await asyncio.to_thread(api.configuration)
            elif name == 'ioa_setup_browser':
                if not hmac.compare_digest(arguments['uiNavigationToken'], navigation_token('account')):
                    raise RuntimeFault('forbidden', '请从账号内嵌页面点击浏览器入口。')
                result = await asyncio.to_thread(setup, config_path, open_browser=True)
            elif name == 'ioa_open_browser':
                if apps_supported() and not hmac.compare_digest(
                        arguments.get('uiNavigationToken', ''), navigation_token(arguments['reference'])):
                    raise RuntimeFault('invalid_input', '请用 ioa_get_form 在聊天中展示原表单；外部页面仅由用户点击表单按钮打开。连接重建后请先刷新原表单。')
                target = {'reference': arguments['reference']}
                result = await asyncio.to_thread(api.call, 'runtime.forms.get', target)
                form = result.get('data') or {}
                if result.get('status') == 'completed':
                    confirmation = ((form.get('result') or {}).get('data') or {}).get('confirmationRef')
                    operation = 'runtime.open_ui' if form.get('phase') == 'waiting' and confirmation else 'runtime.forms.open_ui'
                    result = await asyncio.to_thread(api.call, operation, {} if operation == 'runtime.open_ui' else target)
            elif name in ('ioa_setup', 'ioa_setup_status'):
                result = await asyncio.to_thread(setup, config_path, open_browser=name == 'ioa_setup',
                                                 embedded=name == 'ioa_setup' and apps_supported(),
                                                 options=arguments if name == 'ioa_setup' else None)
                result_meta = result.pop('_meta', None)
                if result_meta and result_meta.get('ioalab/account'):
                    result_meta['ioalab/account']['navigationToken'] = navigation_token('account')
            else:
                result = await asyncio.to_thread(api.call, operation, arguments)
            if name == 'ioa_open_form' and result.get('data') and not apps_supported():
                opened = await asyncio.to_thread(api.call, 'runtime.forms.open_ui', {'reference': result['data']['reference']})
                result = {**result, 'presentation': {'mode': 'browser', 'opened': opened.get('data', {}).get('opened', False) if opened.get('data') else False,
                                                   'error': opened.get('error')}}
            form = result.get('data') or {}
            if apps_supported() and result.get('status') == 'completed' and form.get('reference') and form.get('schema'):
                result_meta = {'ioalab/navigation': {'reference': form['reference'], 'token': navigation_token(form['reference'])}}
                try:
                    entry = await asyncio.to_thread(api.confirmation_entry, form['reference'])
                    if entry.get('reference'):
                        result_meta['ioalab/confirmation'] = entry
                except RuntimeFault:
                    # Business result is authoritative even if the renderer entry is unavailable.
                    result_meta['ioalab/confirmationUnavailable'] = True
        except RuntimeFault as exc:
            result = {'status': 'rejected', 'data': None, 'error': {'code': exc.code, 'message': str(exc), 'recovery': exc.recovery}}
        except Exception:
            # Do not expose config values, OS launcher errors, paths with tokens or stack locals.
            result = {'status': 'failed', 'data': None, 'error': {'code': 'not_ready', 'message': 'IoA local operation failed; inspect the private client diagnostics'}}
        return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                                    structuredContent=result, isError=result.get('status') in ('rejected', 'failed', 'unknown'),
                                    **({'_meta': result_meta} if result_meta else {}))

    return server


def main(argv=None):
    configure_stdio()
    parser = argparse.ArgumentParser(prog='python -m ioalab_sdk.mcp')
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)

    async def serve():
        server = create_server(args.config)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    try:
        asyncio.run(serve())
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        print('IoA MCP service stopped; inspect the local client configuration.', file=sys.stderr)
        return 1
