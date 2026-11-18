"""Standard MCP client for the user's installed Codex desktop tools plugin.

The desktop context comes only from the fixed native setup entry. This module
does not discover pipes, invoke a second App Server, approve tools, or replay a
request whose result was lost. Closing reaps only its own MCP child.
"""
import json
import os
from pathlib import Path
import time

from .desktop_context import validate
from .transport import Stdio, WireError, WireRejected

HANDSHAKE_SECONDS = 15
BOOTSTRAP = ('IoA Lab is initializing this business chat. Do not perform any business action, '
             'invoke tools, or change settings. Reply only: IoA Lab chat initialized.')
REQUIRED_TOOLS = {'create_thread', 'send_message_to_thread', 'read_thread'}


class McpStdio(Stdio):
    """The existing bounded owned-child transport with MCP JSON-RPC framing."""
    def send(self, value):
        super().send({'jsonrpc': '2.0', **value})


class NativeAppTools:
    def __init__(self, context, *, wire_factory=McpStdio):
        self.context = dict(validate(context))
        self._factory, self._wire, self._opened = wire_factory, None, False

    def _environment(self):
        env = dict(os.environ)
        env['CODEX_APP_TOOLS_PIPE_PATH'] = self.context['pipePath']
        env['CODEX_MCP_NODE_PATH'] = self.context['nodePath']
        env['CODEX_THREAD_ID'] = self.context['threadId']
        env.pop('CODEX_APP_TOOLS_CALLER_HOST_ID', None)
        if self.context['hostId'] is not None:
            env['CODEX_APP_TOOLS_CALLER_HOST_ID'] = self.context['hostId']
        return env

    def _request(self, method, params, deadline):
        if self._wire is None:
            raise WireError('Desktop app tools are not open')
        def native_request(packet):
            if 'id' in packet and 'method' in packet:
                self._wire.send({'id': packet['id'], 'error': {'code': -32601,
                    'message': 'This MCP client does not expose that capability'}})
        try:
            return self._wire.request(method, params, deadline, native_request)
        except WireRejected:
            # A host error can follow a successful side effect. It is never a
            # blanket assurance that create/send was not performed.
            raise WireError('Desktop app tools returned an error; preserve the original operation and do not replay') from None

    def open(self):
        if self._opened:
            raise WireError('Open each owned desktop tools connection once')
        self._opened = True
        plugin = Path(self.context['pluginDirectory'])
        try:
            config = json.loads((plugin/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']['codex_app']
            command, args = config['command'], config['args']
            if (not isinstance(command, str) or not command or not isinstance(args, list) or
                    any(not isinstance(arg, str) or not arg for arg in args)):
                raise ValueError()
            self._wire = self._factory([command, *args], cwd=str(plugin), env=self._environment())
            deadline = time.monotonic()+HANDSHAKE_SECONDS
            result = self._request('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                'clientInfo': {'name': 'ioalab_codex_desktop', 'version': '0.1'}}, deadline)
            if not isinstance(result, dict) or result.get('serverInfo', {}).get('name') != 'codex-app-tools':
                raise WireError('The captured plugin did not confirm Codex desktop tools')
            self._wire.send({'method': 'notifications/initialized'})
            listing = self._request('tools/list', {}, deadline)
            names = {tool.get('name') for tool in listing.get('tools', []) if isinstance(tool, dict)}
            if not REQUIRED_TOOLS.issubset(names):
                raise WireError('The desktop does not expose the required chat tools')
            return result
        except (OSError, ValueError, KeyError, TypeError):
            self.close()
            raise WireError('Refresh the fixed native setup entry to restore the desktop tools connection') from None
        except Exception:
            self.close()
            raise

    def _tool(self, name, arguments, deadline):
        result = self._request('tools/call', {'name': name, 'arguments': arguments,
            '_meta': {'x-codex-turn-metadata': {'thread_id': self.context['threadId']}}}, deadline)
        if not isinstance(result, dict) or result.get('isError'):
            raise WireError('Desktop tool outcome is unknown; preserve the original operation and do not replay')
        for block in result.get('content', []):
            if isinstance(block, dict) and block.get('type') == 'text':
                try:
                    value = json.loads(block['text'])
                except (ValueError, TypeError, KeyError):
                    continue
                if isinstance(value, dict):
                    return value
        raise WireError('Unrecognized desktop tool result; preserve the original operation and do not replay')

    def create(self, name, *, deadline):
        result = self._tool('create_thread', {'title': name, 'prompt': BOOTSTRAP,
            'target': {'type': 'projectless', 'directoryName': 'ioa-lab'}}, deadline)
        if not isinstance(result.get('threadId'), str) or not result['threadId']:
            raise WireError('No actual desktop SID returned; preserve the original creation without replay')
        return result

    def send(self, sid, text, *, deadline):
        result = self._tool('send_message_to_thread', {'threadId': sid, 'hostId': 'local', 'prompt': text}, deadline)
        if result.get('threadId') != sid:
            raise WireError('Desktop send identity conflict; preserve the original operation without replay')
        return result  # Official acknowledgement currently has SID, not turn ID.

    def read(self, sid, *, deadline, cursor=None):
        args = {'threadId': sid, 'hostId': 'local', 'turnLimit': 10,
                'includeOutputs': True, 'maxOutputCharsPerItem': 20000}
        if cursor is not None:
            args['cursor'] = cursor
        result = self._tool('read_thread', args, deadline)
        if result.get('thread', {}).get('id') != sid or not isinstance(result.get('turns'), list):
            raise WireError('Desktop read identity conflict')
        return result

    def turns(self, sid, *, deadline, pages=5):
        found, cursor = [], None
        for _ in range(pages):
            result = self.read(sid, deadline=deadline, cursor=cursor)
            found.extend(result['turns'])
            page = result.get('page', {})
            if page.get('hasMore') is False:
                return found, True
            cursor = page.get('nextCursor')
            if not isinstance(cursor, str) or not cursor:
                raise WireError('Desktop turn page has no exact continuation cursor')
        return found, False

    def close(self):
        if self._wire is not None:
            self._wire.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()
