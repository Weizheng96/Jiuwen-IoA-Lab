"""Standard stdio MCP tools/resources mapped to the shared public client API."""
import argparse
import asyncio
from ..runtime import installed_version
import json
import sys

from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from ..runtime import ClientOperations, RuntimeFault, configure_stdio, form_page, setup


APP_URI = 'ui://ioalab/forms/v1.html'
APP_MIME = 'text/html;profile=mcp-app'


def object_schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


REFERENCE = {'reference': {'type': 'string', 'minLength': 1, 'maxLength': 64}}
REVISION = {**REFERENCE, 'revision': {'type': 'integer', 'minimum': 0}}
VALUES = {'type': 'object', 'description': '普通业务字段；不要包含密码、令牌或私钥。'}
# Mapping only: role definitions and the resident runtime own all business behavior.
TOOLS = {
    'ioa_setup': ('打开本机受控账号接入页面；不在聊天或工具参数中传递密码。已有账号配置会复用。', 'account.setup', object_schema()),
    'ioa_setup_status': ('查询接入进度，不读取账号秘密。', 'account.setup.status', object_schema()),
    'ioa_status': ('查看 IoA 客户端连接、身份和宿主就绪状态。', 'runtime.status', object_schema()),
    'ioa_configuration': ('读取 IoA 本地配置的公开摘要，不读取或输出凭据字段。', None, object_schema()),
    'ioa_forms': ('列出本机可用业务表单及已保存的表单引用，恢复时先查这里。', 'runtime.forms.list', object_schema()),
    'ioa_open_form': ('按业务类型打开表单，让用户填写所需信息。先用 ioa_forms 查看类型；已有表单用 ioa_get_form 恢复。',
                      'runtime.forms.open', object_schema({'kind': {'type': 'string'}, 'values': VALUES}, ('kind',))),
    'ioa_get_form': ('读取原表单和真实业务结果；不启动新操作。', 'runtime.forms.get', object_schema(REFERENCE, ('reference',))),
    'ioa_update_form': ('保存普通字段草稿，必须带当前修订号。', 'runtime.forms.update',
                        object_schema({**REVISION, 'values': VALUES}, ('reference', 'revision', 'values'))),
    'ioa_preview_form': ('校验当前表单并生成真实操作预览，不批准、不发布。', 'runtime.forms.preview', object_schema(REVISION, ('reference', 'revision'))),
    'ioa_submit_form': ('提交已预览的修订或续接原操作。需要批准时保持等待，不以调用工具代替用户批准。',
                        'runtime.forms.submit', object_schema(REVISION, ('reference', 'revision'))),
    'ioa_open_browser': ('在本机打开原表单及受控确认入口；不返回浏览器凭据。', 'runtime.forms.open_ui', object_schema(REFERENCE, ('reference',))),
}


def create_server(config_path):
    api = ClientOperations(config_path)
    server = Server('ioalab-local', version=installed_version('ioalab-sdk'), instructions=
                    '使用 ioa_forms 发现业务表单，通过界面收集信息。恢复原 reference，不重复创建业务。'
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
            if supported and name in ('ioa_open_form', 'ioa_get_form', 'ioa_update_form', 'ioa_preview_form', 'ioa_submit_form'):
                meta = {'ui': {'resourceUri': APP_URI}}
            result.append(types.Tool(name=name, description=description, inputSchema=schema,
                annotations=types.ToolAnnotations(readOnlyHint=name in ('ioa_status', 'ioa_configuration', 'ioa_forms', 'ioa_get_form'),
                                                 destructiveHint=False, openWorldHint=name == 'ioa_submit_form'),
                **({'_meta': meta} if meta else {})))
        return result

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri=APP_URI, name='IoA 业务表单', description='随客户端安装的通用表单；无需访问外部资源。', mimeType=APP_MIME)]

    @server.read_resource()
    async def read_resource(uri):
        if str(uri) != APP_URI:
            raise ValueError('Unknown IoA UI resource')
        return [ReadResourceContents(content=form_page('app'), mime_type=APP_MIME,
                                     meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
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
            elif name in ('ioa_setup', 'ioa_setup_status'):
                result = await asyncio.to_thread(setup, config_path, open_browser=name == 'ioa_setup')
            else:
                result = await asyncio.to_thread(api.call, operation, arguments)
            if name == 'ioa_open_form' and result.get('data') and not apps_supported():
                opened = await asyncio.to_thread(api.call, 'runtime.forms.open_ui', {'reference': result['data']['reference']})
                result = {**result, 'presentation': {'mode': 'browser', 'opened': opened.get('data', {}).get('opened', False) if opened.get('data') else False,
                                                   'error': opened.get('error')}}
        except RuntimeFault as exc:
            result = {'status': 'rejected', 'data': None, 'error': {'code': exc.code, 'message': str(exc), 'recovery': exc.recovery}}
        except Exception:
            # Do not expose config values, OS launcher errors, paths with tokens or stack locals.
            result = {'status': 'failed', 'data': None, 'error': {'code': 'not_ready', 'message': 'IoA local operation failed; inspect the private client diagnostics'}}
        return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                                    structuredContent=result, isError=result.get('status') in ('rejected', 'failed', 'unknown'))

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
