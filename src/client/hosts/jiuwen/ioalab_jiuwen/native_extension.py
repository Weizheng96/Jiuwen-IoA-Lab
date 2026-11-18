"""Install the bundled Rail on the current local WorkSwarm WebChannel."""

import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
import uuid

from websockets.exceptions import InvalidStatus, WebSocketException
from websockets.sync.client import connect


NAME = 'ioalab_native_entry'
RAIL_CLASS = 'IoAEntryRail'
TIMEOUT = 5


class ExtensionFault(Exception):
    pass


def local_target():
    if not os.environ.get('JIUWENSWARM_START_CMD'):
        return None
    host = os.environ.get('WEB_HOST', '127.0.0.1')
    port = os.environ.get('WEB_PORT', '')
    path = os.environ.get('WEB_PATH', '/ws')
    if (host not in ('127.0.0.1', 'localhost', '::1') or not re.fullmatch('[0-9]{1,5}', port)
            or not 1 <= int(port) <= 65535 or not re.fullmatch(r'/[a-zA-Z0-9_/-]+', path)):
        return None
    home = Path(os.environ.get('JIUWENSWARM_HOME') or Path.home())
    workspace = Path(os.environ.get('JIUWENSWARM_DATA_DIR') or home/'.jiuwenswarm')
    agent_workspace = workspace/'agent'/'workspace'
    if not workspace.is_absolute() or not agent_workspace.is_dir():
        return None
    address = ('[::1]' if host == '::1' else host)+':'+str(int(port))
    return address, path, agent_workspace/'extensions'


def request(socket, method, params):
    identifier = 'ioa-install-'+uuid.uuid4().hex
    socket.send(json.dumps({'type': 'req', 'id': identifier, 'method': method, 'params': params}))
    deadline = time.monotonic()+TIMEOUT
    while time.monotonic() < deadline:
        frame = json.loads(socket.recv(timeout=max(0, deadline-time.monotonic())))
        if not isinstance(frame, dict) or frame.get('id') != identifier:
            continue
        if frame.get('ok') is not True:
            raise ExtensionFault('host_rejected')
        payload = frame.get('payload')
        if not isinstance(payload, dict):
            raise ExtensionFault('unexpected_receipt')
        return payload
    raise TimeoutError


def is_current(target, resources):
    if not target.is_dir() or target.resolve() != target.absolute():
        return False
    for name, content in resources.items():
        path = target/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != len(content) or path.read_bytes() != content:
            return False
    return True


def rail_receipt(receipt, name):
    return (receipt.get('name') == name and receipt.get('class_name') == RAIL_CLASS
            and type(receipt.get('enabled')) is bool)


def install(bundle_directory, resources):
    phase = 'target'
    try:
        target = local_target()
        if target is None:
            return result('waiting', 'local_web_unavailable')
        address, path, extension_root = target
        installed = extension_root/NAME
        name = NAME
        phase = 'list'
        with connect('ws://'+address+path, proxy=None, open_timeout=TIMEOUT, close_timeout=1,
                     max_size=131072) as socket:
            if socket.request.headers['Host'] != address or socket.request.path != path:
                return result('waiting', 'unexpected_web_endpoint')
            extensions = request(socket, 'extensions.list', {}).get('extensions')
            if not isinstance(extensions, list) or any(not isinstance(item, dict) for item in extensions):
                return result('waiting', 'unexpected_extension_list')
            matching = [item for item in extensions if item.get('name') == NAME or item.get('class_name') == RAIL_CLASS]
            if matching:
                entry = matching[0]
                name = entry.get('name')
                if (len(matching) != 1 or not isinstance(name, str) or len(name) > 120 or
                        not name.isascii() or not name.isidentifier() or not rail_receipt(entry, name) or
                        not is_current(extension_root/name, resources)):
                    return result('conflict', 'extension_ownership_unverified')
                if entry['enabled']:
                    return result('enabled', name=name)
            else:
                # WorkSwarm's importer replaces unregistered folders with the requested name.
                if installed.exists() or installed.is_symlink() or installed.parent.resolve() != installed.parent.absolute():
                    return result('conflict', 'extension_ownership_unverified')
                phase = 'import'
                with TemporaryDirectory(prefix='ioa-rail-', dir=bundle_directory, ignore_cleanup_errors=True) as stage:
                    source = Path(stage)/NAME
                    source.mkdir()
                    for filename, data in resources.items():
                        (source/filename).write_bytes(data)
                    imported = request(socket, 'extensions.import', {'folder_path': str(source)})
                if not rail_receipt(imported, name) or imported['enabled'] or not is_current(installed, resources):
                    return result('waiting', 'import_receipt_unverified')
            phase = 'enable'
            enabled = request(socket, 'extensions.toggle', {'name': name, 'enabled': True})
            return (result('enabled', name=name) if rail_receipt(enabled, name) and enabled['enabled']
                    else result('waiting', 'enable_receipt_unverified', name=name))
    except InvalidStatus as error:
        code = 'permission_required' if error.response.status_code in (401, 403) else 'local_web_unavailable'
        return result('waiting', code)
    except ExtensionFault as error:
        return result('waiting', phase+'_'+str(error))
    except (OSError, RuntimeError, ValueError, WebSocketException):
        return result('waiting', phase+'_unconfirmed')


def result(status, reason=None, *, name=NAME):
    value = {'name': name, 'status': status, 'nativeLoadedVerified': False}
    if status == 'enabled':
        value['instruction'] = '九问已登记并启用同一份两角色扩展；先在真实九问会话核对对应的 IoA 工具可用。'
    elif reason == 'extension_bundle_unverified':
        value['instruction'] = '本轮导出的扩展字节无法核实；先核对原安装包再继续。'
    elif status == 'conflict':
        value['instruction'] = '九问已有同名或同类扩展，当前包无法确认其归属；保留原件，查清冲突后再继续。'
    elif reason == 'roles_pending':
        value['instruction'] = '先完成账号与两角色配置，再从本次九问内重新运行 skills-install。'
    elif reason == 'runtime_pending':
        value['instruction'] = ('先登记当前九问宿主，并启动或复用原客户端；核对原账号认证和平台连接后，'
                                '从本次九问重新运行 skills-install。')
    else:
        value['instruction'] = ('九问扩展启用回执尚未核实；先在本次九问按实际回执诊断，再按随包 '
                                'native/README.md 的手动步骤处理确需用户操作的部分。')
    if reason is not None:
        value['reason'] = reason
    return value
